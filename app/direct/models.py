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

Fora de escopo nesta versão: fonte/grounding (rejeitada antes do aceite),
escolha de modelo (o modelo é o padrão configurado do provider no
deployment, congelado no aceite), orçamento agregado da execução (não tem
significado coerente para uma chamada única -- só o teto de output por
chamada se aplica).
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


class DirectRunConfig(BaseModel):
    """Autoridade aceita de uma run direta -- congelada no aceite, nunca
    recalculada depois. `requested_model` vem do provider construído pelo
    deployment (`LLMProvider.default_model`), nunca do cliente: uma mudança
    posterior de configuração não reescreve o que uma run aceita pediu."""

    model_config = _CONFIG

    question: str
    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
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
