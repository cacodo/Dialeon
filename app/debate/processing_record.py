"""
`ClaimProcessingAttempt` — registro de auditoria de UMA chamada real do
claim processor (extração; `grouping`/`reconciliation` são operações
HISTÓRICAS -- não são mais executadas, mas runs antigas as persistiram e
elas continuam legíveis), Etapa 5.

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


class ClaimProcessingAttempt(BaseModel):
    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    # Cross-round claim reconciliation -- "reconciliation" é uma comparação
    # semântica LLM entre claims de RODADAS DIFERENTES (Round 1 sobrevivente
    # vs. Round 2 sobrevivente), deliberadamente NUNCA rotulada "grouping"
    # no registro de auditoria mesmo reusando boa parte do mesmo mecanismo
    # (mesmo formato de schema/validação, mesma disciplina de retry) --
    # "grouping" continua significando exclusivamente comparação DENTRO de
    # uma única rodada (ver app/debate/claim_extraction.py). Ver
    # `_targets_match_operation` abaixo: o formato de target é o MESMO de
    # "grouping" (sem target_model_response_id, com target_claim_ids), mas
    # o rótulo semântico permanece distinto -- um quarto valor futuro
    # precisaria decidir explicitamente qual formato de target usar, nunca
    # herdar silenciosamente de um `else` genérico.
    operation: Literal["extraction", "grouping", "reconciliation"]
    # round_number=2 pra uma tentativa de reconciliation NÃO significa
    # "esta é uma operação ordinária da Round 2" -- significa "esta
    # reconciliação ocorreu depois que a Round 2 (a segunda rodada real do
    # debate) terminou de processar". A identidade auditável de uma
    # tentativa de reconciliação é o PAR (operation="reconciliation",
    # round_number=2), nunca round_number sozinho -- este sistema tem
    # exatamente 2 rodadas reais de debate (ver app/debate/debate_engine.py,
    # _INITIAL_ROUND_NUMBER/_CRITIQUE_ROUND_NUMBER); nenhuma "Round 3" é
    # inventada aqui nem em nenhum outro lugar do domínio.
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
    # Provenance de `model` -- ver ModelResponse.model_identity_source
    # (app/models/domain.py) pra semântica completa.
    model_identity_source: ModelIdentitySource | None = None

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
    # `accepted_normalized` -- HISTÓRICO (claim_grouping_v3): EXCLUSIVO de
    # `operation="grouping"`; registrava uma resposta que continha grupo(s)
    # unitário(s) aceita após normalização determinística. O agrupamento v4
    # (partição consultiva) NÃO normaliza nada e NUNCA emite este valor --
    # ele permanece no vocabulário SÓ pra que registros v3 persistidos
    # continuem legíveis. `raw_output_text` sempre foi a resposta ORIGINAL.
    # Nunca um conceito genérico de "saída normalizada": extração/
    # reconciliação não têm essa saída (validador
    # `_accepted_normalized_is_grouping_only`).
    parse_status: Literal[
        "accepted", "accepted_normalized", "malformed", "inconsistent_references", "not_attempted"
    ]
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
    # Provider-Neutral Request Provenance V1 -- ver docstring de
    # `RequestProvenance` (app/models/request_provenance.py). O request
    # de extração/agrupamento/reconciliação é construído UMA vez, ANTES
    # do loop de retry de structured output -- toda tentativa (aceita ou
    # rejeitada) desta MESMA chamada lógica carrega a MESMA provenance.
    # `None` é EXCLUSIVAMENTE o valor de uma tentativa persistida ANTES
    # deste slice existir.
    request_provenance: RequestProvenance | None = None
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
        if (
            self.parse_status in ("accepted", "accepted_normalized")
            and self.parse_error_message is not None
        ):
            raise ValueError(f"parse_status={self.parse_status!r} não deve ter parse_error_message")
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
    def _accepted_normalized_is_grouping_only(self) -> ClaimProcessingAttempt:
        if self.parse_status == "accepted_normalized" and self.operation != "grouping":
            raise ValueError(
                "parse_status='accepted_normalized' só existe pra operation='grouping' "
                f"(recebido operation={self.operation!r})"
            )
        return self

    @model_validator(mode="after")
    def _targets_match_operation(self) -> ClaimProcessingAttempt:
        """Sem `else` genérico de propósito -- cada operação conhecida é
        um ramo EXPLÍCITO com sua própria regra, nunca "qualquer coisa que
        não seja X". Isso garante que um QUARTO valor de `operation`
        adicionado no futuro sem atualizar esta função vira um
        `ValueError` aqui mesmo (nenhum ramo bate), nunca herda
        silenciosamente a regra de "grouping"/"reconciliation" só porque
        também não é "extraction" -- ver hardening análogo já aplicado a
        `_bucket_for_verdict` em app/editor/compose.py (mapeamento
        exaustivo, nunca fallback implícito)."""
        if self.operation == "extraction":
            if self.target_model_response_id is None:
                raise ValueError("operation='extraction' exige target_model_response_id")
            if self.target_claim_ids:
                raise ValueError("operation='extraction' não deve ter target_claim_ids")
        elif self.operation in ("grouping", "reconciliation"):
            # Mesmo formato de target pras duas -- "reconciliation" reusa
            # a forma estrutural de "grouping" (compara N claims dadas,
            # nunca uma resposta específica), mas continua um rótulo
            # semântico distinto (ver docstring de `operation` acima) --
            # nunca confundir "mesmo formato de target" com "mesma
            # operação".
            if self.target_model_response_id is not None:
                raise ValueError(
                    f"operation={self.operation!r} não deve ter target_model_response_id"
                )
            if not self.target_claim_ids:
                raise ValueError(f"operation={self.operation!r} exige target_claim_ids não-vazio")
        else:
            raise ValueError(
                f"operation={self.operation!r} não tem regra de target definida -- "
                "todo valor de operation precisa de um ramo explícito nesta função, "
                "nunca um fallback implícito"
            )
        return self
