"""
`SourceAnalysisAttempt` — registro de auditoria de UMA chamada real do
source analyzer, Etapa 16.

Espelha `JudgeAttempt` (app/judge/attempt.py), com uma diferença
deliberada: `parse_status` NÃO tem `"inconsistent_references"` -- ver
docstring de `app/source_analysis/schemas.py` (referências inconsistentes
viram rejeição por-claim, não rejeição de attempt inteiro aqui).

Cada tentativa real vira um registro próprio -- inclusive as rejeitadas
por output malformado -- mesma disciplina de ClaimProcessingAttempt/
JudgeAttempt/EditorAttempt.
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

_CONFIG = ConfigDict(frozen=True, extra="forbid")


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceAnalysisAttempt(BaseModel):
    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    attempt_number: int = Field(ge=1)

    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    model: str = Field(min_length=1)
    # Provenance de `model` -- ver ModelResponse.model_identity_source
    # (app/models/domain.py) pra semântica completa.
    model_identity_source: ModelIdentitySource | None = None

    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None = None
    # Etapa 17A (B1): ge=0 (não ge=1) -- 0 é um valor real e confirmado (ProviderResponse.attempts=0 quando complete() estabeleceu que nenhum dispatch de rede ocorreu, ex.: API key ausente), não um erro de validação a rejeitar.
    transport_attempts: int = Field(ge=0)

    raw_output_text: str | None = None
    parse_status: Literal["accepted", "malformed", "not_attempted"]
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
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _transport_and_parse_are_consistent(self) -> SourceAnalysisAttempt:
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
    def _parse_status_and_error_message_are_consistent(self) -> SourceAnalysisAttempt:
        if self.parse_status == "accepted" and self.parse_error_message is not None:
            raise ValueError("parse_status='accepted' não deve ter parse_error_message")
        if self.parse_status == "malformed" and not self.parse_error_message:
            raise ValueError("parse_status='malformed' exige parse_error_message preenchido")
        return self
