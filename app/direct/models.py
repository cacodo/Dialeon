"""
Direct Answer Execution V1 -- uma pergunta, UM provider, UMA completion
lógica, a resposta do provider como resposta.

Semântica DISTINTA de uma run do Conselho, nunca "Conselho com um
participante": uma run direta nunca passa por extração de afirmações,
análise de fonte, crítica, Judge, reconciliação, Editor nem realização
linguística. A resposta é o texto que o provider escolhido devolveu -- não é
consenso, veredito, síntese do Editor nem evidência verificada.

Reusa os conceitos de baixo nível que já existem e já significam a mesma
coisa aqui: `CompletionRequest`/`LLMProvider.complete()` (retry/timeout de
transporte do provider inalterados -- várias tentativas FÍSICAS continuam
sendo UMA completion lógica), `ModelResponse` (modelo solicitado x
reportado, `model_identity_source`, uso, custo estimado, `PricingProvenance`,
tentativas, incerteza de tentativa anterior, erro) e `RequestProvenance`.

Modelo (Direct Accepted Effective Model Choice V1): sem escolha, o modelo é
o padrão configurado do provider no deployment; com escolha explícita, é o
identificador pedido pra esta run. Nos dois casos, resolvido e congelado no
aceite, com a origem registrada (`requested_model_origin`).

Fora de escopo nesta versão: fonte/grounding (rejeitada antes do aceite),
orçamento agregado da execução (não tem significado coerente para uma
chamada única -- só o teto de output por chamada se aplica).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import ModelResponse
from app.models.provider_models import CompletionRequest, Message, ProviderExecutionPolicy

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Contrato do request da resposta direta (proveniência de auditoria, ver
# `app.models.request_provenance`). Distinto de "initial_response_v1" do
# Conselho: o request direto carrega o modelo solicitado explicitamente.
DIRECT_ANSWER_CONTRACT_VERSION = "direct_answer_v1"

DirectRunStatus = Literal["completed", "failed"]

# De onde veio o modelo solicitado de uma run direta (mesmo vocabulário das
# escolhas dos participantes do Conselho, sem depender delas):
# - "configured_default": o padrão configurado do provider escolhido, lido
#   do provider construído pelo deployment no aceite (nenhuma escolha na run);
# - "run_override": escolhido explicitamente pra esta run -- mesmo que o
#   texto seja igual ao padrão configurado.
DirectModelOrigin = Literal["configured_default", "run_override"]


class DirectRunConfig(BaseModel):
    """Autoridade aceita de uma run direta -- congelada no aceite, nunca
    recalculada depois: uma mudança posterior de configuração não reescreve
    o que uma run aceita pediu.

    `requested_model` é o modelo EFETIVAMENTE pedido ao provider e
    `requested_model_origin` diz de onde ele veio (ver `DirectModelOrigin`).
    A origem é obrigatória: todo aceite novo a declara. Linhas gravadas
    antes deste campo existir são lidas pelo repositório como
    `configured_default` -- o contrato sob o qual foram escritas (a run
    direta não aceitava modelo do cliente), nunca a configuração atual.

    Com escolha explícita, o padrão configurado do provider naquele momento
    NÃO é registrado (não é material pra esta run)."""

    model_config = _CONFIG

    question: str
    provider: str = Field(min_length=1)
    # Leitura leniente (só o mínimo): a regra estrita de forma vale pra
    # entrada nova (`validate_model_override_identifier`), nunca reinterpreta
    # o que já foi aceito.
    requested_model: str = Field(min_length=1)
    requested_model_origin: DirectModelOrigin
    max_output_tokens: int = Field(gt=0)


def build_direct_request(config: DirectRunConfig) -> CompletionRequest:
    """Único ponto de construção do `CompletionRequest` direto: a pergunta
    como única mensagem do usuário e o modelo solicitado EXPLÍCITO (entra no
    digest da proveniência do request)."""
    return CompletionRequest(
        messages=[Message(role="user", content=config.question)],
        model=config.requested_model,
        max_tokens=config.max_output_tokens,
    )


class DirectRunResult(BaseModel):
    """Desfecho terminal de uma run direta: a resposta do provider (com toda
    a proveniência de `ModelResponse`) e o status derivado dela.

    `completed` só quando o provider devolveu sucesso COM texto; qualquer
    outra coisa é `failed` (o registro da chamada -- erro, custo, tentativas
    -- continua preservado em `response`). Nunca há resposta fabricada."""

    model_config = _CONFIG

    id: str
    started_at: datetime
    ended_at: datetime
    config: DirectRunConfig
    response: ModelResponse
    provider_execution_policy: ProviderExecutionPolicy

    @property
    def status(self) -> DirectRunStatus:
        return direct_status_of(self.response)


def direct_status_of(response: ModelResponse) -> DirectRunStatus:
    text = response.response_text
    if response.status == "success" and text is not None and text.strip() != "":
        return "completed"
    return "failed"
