"""
`JudgeAttempt` — registro de auditoria de UMA chamada real do Judge, Etapa 6.

Espelha `ClaimProcessingAttempt` (app/debate/processing_record.py), mas SEM
`operation`/`target_model_response_id`/`target_claim_ids`: só existe um tipo
de chamada ("julgar") e o "alvo" é sempre "o DebateResult inteiro" — não há
o que targetar por campo, diferente da extração/agrupamento que processam
uma resposta ou um conjunto de claims específico por vez.

Deliberadamente NÃO é um `ModelResponse`: nunca participa de rodada de
debate, nunca é supporter de nenhuma Claim, nunca entra em
`ClaimSupport`/`supporting_model_response_ids`, nunca afeta
`supporting_model_ratio`, nunca conta para quórum.

Cada tentativa real vira um registro próprio — inclusive as rejeitadas por
output malformado/inconsistente — não um contador dentro de um registro só,
mesma disciplina do claim processor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.provider_models import (
    ModelIdentitySource,
    PricingProvenance,
    ProviderErrorInfo,
    TokenUsage,
)
from app.models.request_provenance import RequestProvenance

_CONFIG = ConfigDict(frozen=True, extra="forbid")


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JudgeAttempt(BaseModel):
    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    # Contador do retry de STRUCTURED OUTPUT, local ao Judge — 1=primeira
    # tentativa, 2=retry por output malformado/inconsistente.
    attempt_number: int = Field(ge=1)

    provider: str = Field(min_length=1)
    # Etapa 13 (T03.A) — ver ProviderResponse (app/models/provider_models.py).
    requested_model: str = Field(min_length=1)
    model: str = Field(min_length=1)
    # Provenance de `model` -- ver ModelResponse.model_identity_source
    # (app/models/domain.py) pra semântica completa (mesma disciplina:
    # copiado verbatim de ProviderResponse.model_identity_source em toda
    # tentativa NOVA, `None` só numa reconstrução histórica).
    model_identity_source: ModelIdentitySource | None = None

    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None = None
    # Retry de TRANSPORTE (interno ao LLMProvider) que aconteceu DENTRO
    # desta tentativa — copiado de ProviderResponse.attempts. Não
    # confundir com attempt_number acima.
    # Etapa 17A (B1): ge=0 (não ge=1) -- 0 é um valor real e confirmado (ProviderResponse.attempts=0 quando complete() estabeleceu que nenhum dispatch de rede ocorreu, ex.: API key ausente), não um erro de validação a rejeitar.
    transport_attempts: int = Field(ge=0)

    raw_output_text: str | None = None
    # Repair H1 (audit-truth na fronteira response -> interpretation) --
    # "interpretation_failed": o transporte teve sucesso e `raw_output_text`
    # é verdadeiro, mas a interpretação da saída levantou uma exceção FORA
    # do vocabulário de erro antecipado (ver `INTERPRETATION_FAILURE_MESSAGE`,
    # app/structured_output.py). Mesmo significado de
    # `EditorAttempt.parse_status`; nunca tratado como aceito.
    parse_status: Literal[
        "accepted", "malformed", "inconsistent_references", "interpretation_failed", "not_attempted"
    ]
    parse_error_message: str | None = None

    usage: TokenUsage | None = None
    cost_usd: float | None = None
    pricing_provenance: PricingProvenance | None = None
    latency_ms: int = Field(ge=0)
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py). Copiado verbatim.
    had_uncertain_prior_attempts: bool = False
    # Etapa 17A.1 (Objetivo B) -- ver docstring de ProviderResponse.
    # Copiado verbatim, nunca normalizado.
    provider_finish_reason: str | None = None
    # Provider-Neutral Request Provenance V1 -- ver docstring de
    # `RequestProvenance` (app/models/request_provenance.py). NUNCA
    # altera semântica epistêmica do Judge -- só observa o request já
    # construído. `None` é EXCLUSIVAMENTE o valor de uma tentativa
    # persistida ANTES deste slice existir.
    request_provenance: RequestProvenance | None = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _transport_and_parse_are_consistent(self) -> JudgeAttempt:
        if self.transport_status == "error":
            if self.raw_output_text is not None:
                raise ValueError("transport_status='error' não deve ter raw_output_text")
            if self.parse_status != "not_attempted":
                raise ValueError(
                    "transport_status='error' exige parse_status='not_attempted' "
                    "— não houve texto pra tentar parsear"
                )
            if self.transport_error is None:
                raise ValueError("transport_status='error' exige transport_error preenchido")
        else:  # transport_status == "success"
            if not self.raw_output_text:
                raise ValueError("transport_status='success' exige raw_output_text preenchido")
            if self.transport_error is not None:
                raise ValueError("transport_status='success' não deve ter transport_error")
            if self.parse_status == "not_attempted":
                raise ValueError(
                    "transport_status='success' não pode ter parse_status='not_attempted' "
                    "— o texto existe, então uma tentativa de parse sempre acontece"
                )
        return self

    @model_validator(mode="after")
    def _parse_status_and_error_message_are_consistent(self) -> JudgeAttempt:
        if self.parse_status == "accepted" and self.parse_error_message is not None:
            raise ValueError("parse_status='accepted' não deve ter parse_error_message")
        if (
            self.parse_status
            in ("malformed", "inconsistent_references", "interpretation_failed")
            and not self.parse_error_message
        ):
            raise ValueError(
                "parse_status em {'malformed','inconsistent_references',"
                "'interpretation_failed'} exige parse_error_message preenchido"
            )
        return self
