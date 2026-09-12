"""
Estruturas de dados da Provider Layer.

Estas são as únicas estruturas que o resto do sistema (quando existir:
Orchestrator, Debate Engine, etc.) vai conhecer sobre os providers.
Nenhum componente fora de app/providers/ deve saber que um provider é
"OpenAI" ou "Gemini" para interpretar sua resposta — tudo passa por
ProviderResponse.

Semântica de `cost_usd` (Etapa 9 — calculado em `LLMProvider.complete()`, via
`PricingRegistry`, nunca nos adapters concretos): é sempre uma ESTIMATIVA
por tabela de preços públicos configurada localmente, nunca cobrança
real/efetiva (créditos, franquias free-tier, negociação de preço nunca são
modelados). Três estados, nunca confundidos entre si:

- `None` = desconhecido — sem preço registrado pro par (provider, model),
  OU uma falha ocorreu depois que a chamada real já havia começado (a
  request pode ter sido enviada e até processada remotamente antes do
  erro aparecer do nosso lado — nunca assumimos consumo zero nesse caso).
- `0.0` = conhecido-zero — taxa registrada e igual a zero, OU uma falha
  local comprovada (ex.: API key ausente) que nunca chegou a sair do
  processo, então sabemos com certeza que o custo de API é zero.
- `>0.0` = conhecido, calculado a partir de uso × taxa.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Message(BaseModel):
    """Um turno de uma conversa, no formato genérico usado por todos os providers."""

    role: Literal["user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    """Requisição normalizada enviada a qualquer LLMProvider.

    `model` é opcional: se omitido, o provider usa o default configurado
    (ver app/config.py). Isso permite ao chamador não precisar saber
    nomes de modelo específicos na Fase 1 do MVP, mas ainda permite
    escolher um modelo específico quando necessário.
    """

    messages: list[Message] = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    max_tokens: int = 1024
    temperature: float | None = None


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderErrorType(str, Enum):
    TIMEOUT = "timeout"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    API_ERROR = "api_error"  # erro do lado do provider (5xx, erro de request malformado, etc.)
    MALFORMED_RESPONSE = "malformed_response"  # resposta 200 mas em formato inesperado
    UNKNOWN = "unknown"


class ProviderErrorInfo(BaseModel):
    type: ProviderErrorType
    message: str
    retryable: bool


class PricingProvenance(BaseModel):
    """O que produziu um `cost_usd` conhecido — nasce dentro de
    `PricingRegistry.price()` (Etapa 10), no exato momento do cálculo, e
    viaja junto com o `cost_usd` correspondente até a persistência. NUNCA
    reconstituída depois consultando o registry atual — se a tabela de
    preços mudar no futuro, isso não pode alterar o que uma resposta
    histórica já registrou aqui.

    `input_rate_usd_per_million_tokens`/`output_rate_usd_per_million_tokens`
    são as taxas EFETIVAMENTE aplicadas àquela chamada específica — já
    multiplicadas pelo fator de long-context quando `tier="long_context"`
    (ex.: GPT-5.5 acima de 272K tokens de input: `input_rate_usd_per_million_tokens=10.0`,
    não `5.0` com um multiplicador separado). `source_id` identifica a
    tabela de preços usada (ex.: `DEFAULT_PRICING_REGISTRY.source_id`) —
    um identificador pequeno e explícito, não um sistema de
    versionamento/migração.

    Sempre acompanha um `cost_usd` conhecido — mas a implicação só vale
    numa direção: `cost_usd is None` sempre implica `pricing_provenance
    is None` (nunca inventamos proveniência pra um custo desconhecido).
    A volta não vale: existe um caso legítimo de `cost_usd=0.0` SEM
    provenance — a falha local pré-request (API key ausente, Etapa 9),
    porque aquela chamada nunca chegou a passar pelo `PricingRegistry`
    (não houve chamada real pra precificar).

    Patch de cobertura de pricing snapshot — `canonical_model_id`:
    `None` quando a chave `(provider, model)` bateu DIRETAMENTE na
    tabela — o `model`/`effective_model` já é, ele mesmo, o identifier
    precificado, sem alias envolvido (todo o histórico anterior a este
    patch, e a maioria dos casos daqui pra frente, cai aqui). Preenchido
    apenas quando `PricingRegistry.price()` resolveu a taxa através de
    um alias explícito de snapshot (`PricingRegistry.snapshot_aliases`)
    — contém o identifier CANÔNICO cuja taxa foi efetivamente usada
    (ex.: `"gpt-5.5"`), que é diferente do `model`/`effective_model`
    original (ex.: `"gpt-5.5-2026-04-23"`). Isso nunca reescreve
    `ProviderResponse.model`/`requested_model` — só documenta, pra
    auditoria, que a taxa aplicada veio de um modelo canônico distinto
    do identifier efetivamente reportado. Default `None` preserva
    leitura de registros históricos persistidos antes deste campo
    existir (chave ausente no JSON -> Pydantic aplica o default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    tier: Literal["standard", "long_context"]
    input_rate_usd_per_million_tokens: float = Field(ge=0)
    output_rate_usd_per_million_tokens: float = Field(ge=0)
    canonical_model_id: str | None = None


class ProviderResponse(BaseModel):
    """Resposta normalizada — o único formato que o resto do sistema conhece.

    Etapa 13 (T03.A) — `requested_model` vs `model`:

    - `requested_model`: o identificador exato que foi SOLICITADO nesta
      chamada (`CompletionRequest.model` OR `provider.default_model`),
      resolvido uma única vez ANTES de qualquer chamada de rede em
      `LLMProvider.complete()`. Provenance histórica, imutável durante
      retries/falhas — não muda mesmo que a chamada seja reexecutada.
    - `model`: campo já existente, preservado por compatibilidade.
      Semântica inalterada: provider-reported model quando o
      provider/SDK expõe essa informação, senão `requested_model` como
      fallback. NÃO é prova de que o provider informou um
      snapshot/version efetivamente executado — apenas
      "provider-reported quando disponível, senão o que foi pedido".
    """

    provider: str
    requested_model: str
    model: str
    status: Literal["success", "error"]
    text: str | None = None
    usage: TokenUsage | None = None
    cost_usd: float | None = None  # ver docstring do módulo — None/0.0/>0, nunca confundidos
    # Etapa 10 — ver docstring de PricingProvenance. None sempre que
    # cost_usd for None; também None no caso pré-request known-zero
    # (cost_usd=0.0 sem ter passado pelo PricingRegistry).
    pricing_provenance: PricingProvenance | None = None
    latency_ms: int
    attempts: int
    # Etapa 17A (B3) — True sse ao menos UMA tentativa de transporte
    # ANTES da última (a que produziu este ProviderResponse, sucesso ou
    # falha) já tinha discado de verdade (chegou a `_call_api()`) e
    # falhou. Independente do status final: `attempts > 1` já implica
    # isso, sempre — sinal ADICIONAL de incompletude de accounting, nunca
    # substitui `cost_usd`/`usage` conhecidos por None. Um único sucesso
    # (attempts=1) ou uma única falha final (attempts=1) são sempre
    # False -- não há tentativa "anterior" a uma tentativa única.
    had_uncertain_prior_attempts: bool = False
    # Etapa 17A.1 (Objetivo B) — motivo de parada NATIVO do provider
    # (ex.: "end_turn"/"max_tokens" da Anthropic, "stop"/"length" da
    # OpenAI, "STOP"/"MAX_TOKENS" da Gemini), preservado VERBATIM, nunca
    # normalizado pra um enum cross-provider inventado (os três providers
    # têm vocabulários genuinamente diferentes -- fingir que são o mesmo
    # esconderia informação real). `None` quando desconhecido/indisponível
    # (nunca inferido/fabricado) -- inclusive em qualquer falha ANTES de
    # `_call_api()` (nada foi observado) e em falhas onde o SDK não expôs
    # esse campo de forma confiável.
    provider_finish_reason: str | None = None
    error: ProviderErrorInfo | None = None

    @model_validator(mode="after")
    def _unknown_cost_never_has_provenance(self) -> ProviderResponse:
        if self.cost_usd is None and self.pricing_provenance is not None:
            raise ValueError(
                "cost_usd=None não pode ter pricing_provenance preenchido — "
                "nunca inventamos proveniência pra um custo desconhecido"
            )
        return self
