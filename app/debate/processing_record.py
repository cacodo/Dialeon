"""
`ClaimProcessingAttempt` — registro de auditoria de UMA chamada real do
claim processor (extração ou agrupamento), Etapa 5.

Deliberadamente NÃO é um `ModelResponse`: chamadas de processamento nunca
participam de rodada de debate, nunca são supporter de nenhuma Claim,
nunca entram em `ClaimSupport`/`supporting_model_response_ids`, nunca
afetam `supporting_model_ratio`. Misturar os dois tipos faria uma chamada
operacional parecer um voto no debate — o que corrompeu justamente o que
`supporting_model_ratio` promete não fazer.

Cada tentativa real vira um registro próprio — inclusive as rejeitadas por
output malformado/inconsistente — não um contador dentro de um registro
só. Isso é o que permite auditar o output cru de CADA tentativa (não só a
aceita) e contabilizar tokens de tentativas rejeitadas no budget (elas
consumiram tokens de verdade, mesmo tendo sido descartadas).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.provider_models import PricingProvenance, ProviderErrorInfo, TokenUsage

_CONFIG = ConfigDict(frozen=True, extra="forbid")


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClaimProcessingAttempt(BaseModel):
    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    operation: Literal["extraction", "grouping"]
    round_number: int = Field(ge=1)
    # Contador do retry de STRUCTURED OUTPUT (camada separada do retry de
    # transporte, que já é interno/opaco ao LLMProvider) — 1=primeira
    # tentativa, 2=retry por output malformado/inconsistente na tentativa
    # anterior.
    attempt_number: int = Field(ge=1)

    provider: str = Field(min_length=1)
    # Etapa 13 (T03.A) — mesma semântica de ProviderResponse
    # (app/models/provider_models.py): requested_model é o que foi
    # PEDIDO nesta chamada real, imutável; model preserva compatibilidade
    # (provider-reported quando disponível, senão requested_model).
    requested_model: str = Field(min_length=1)
    model: str = Field(min_length=1)

    # extraction: a resposta sendo processada. grouping: None (processa
    # várias claims de uma vez, não uma resposta específica).
    target_model_response_id: str | None = None
    # grouping: quais claim ids foram dados como input. extraction: [].
    target_claim_ids: list[str] = Field(default_factory=list)

    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None = None
    # Retry de TRANSPORTE (interno ao LLMProvider) que aconteceu DENTRO
    # desta tentativa específica — copiado de ProviderResponse.attempts.
    # Não confundir com attempt_number acima.
    # Etapa 17A (B1): ge=0 (não ge=1) -- 0 é um valor real e confirmado (ProviderResponse.attempts=0 quando complete() estabeleceu que nenhum dispatch de rede ocorreu, ex.: API key ausente), não um erro de validação a rejeitar.
    transport_attempts: int = Field(ge=0)

    raw_output_text: str | None = None
    parse_status: Literal["accepted", "malformed", "inconsistent_references", "not_attempted"]
    parse_error_message: str | None = None

    usage: TokenUsage | None = None
    cost_usd: float | None = None
    # Etapa 10 — copiado verbatim de ProviderResponse.pricing_provenance.
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
    def _transport_and_parse_are_consistent(self) -> ClaimProcessingAttempt:
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
    def _parse_status_and_error_message_are_consistent(self) -> ClaimProcessingAttempt:
        if self.parse_status == "accepted" and self.parse_error_message is not None:
            raise ValueError("parse_status='accepted' não deve ter parse_error_message")
        if (
            self.parse_status in ("malformed", "inconsistent_references")
            and not self.parse_error_message
        ):
            raise ValueError(
                "parse_status em {'malformed','inconsistent_references'} exige "
                "parse_error_message preenchido"
            )
        return self

    @model_validator(mode="after")
    def _targets_match_operation(self) -> ClaimProcessingAttempt:
        if self.operation == "extraction":
            if self.target_model_response_id is None:
                raise ValueError("operation='extraction' exige target_model_response_id")
            if self.target_claim_ids:
                raise ValueError("operation='extraction' não deve ter target_claim_ids")
        else:  # grouping
            if self.target_model_response_id is not None:
                raise ValueError("operation='grouping' não deve ter target_model_response_id")
            if not self.target_claim_ids:
                raise ValueError("operation='grouping' exige target_claim_ids não-vazio")
        return self
