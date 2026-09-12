"""
Resultados do Debate Engine (Etapa 5) — deliberadamente em `app/debate/`,
não em `app/orchestrator/result.py`: o Orchestrator não sabe o que é
"crítica" nem "debate".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.numeric_verification import DeterministicVerificationAttempt
from app.models.domain import Claim
from app.orchestrator.budget import sum_usage_and_cost
from app.orchestrator.result import InitialResponsesResult, RoundResult

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class CritiqueResult(BaseModel):
    """Interpretação, específica do Debate Engine, de um `RoundResult` cru
    de crítica. Ao contrário da rodada inicial, a crítica NUNCA levanta
    `InsufficientQuorumError` — mesmo com 0 respostas, o debate segue,
    reportando o fato explicitamente aqui em vez de escondê-lo atrás de um
    booleano só (`critique_obtained` sozinho não distingue 1/5 de 4/5;
    `coverage_ratio` resolve isso).

    Ambos os campos calculados são `computed_field` — mesma disciplina de
    `Claim.supporting_model_ratio`: derivados, nunca fontes independentes.
    """

    model_config = _CONFIG

    round_result: RoundResult

    @computed_field  # type: ignore[prop-decorator]
    @property
    def critique_obtained(self) -> bool:
        return self.round_result.successful_count > 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coverage_ratio(self) -> float:
        """Puramente descritivo — proporção de convidados à crítica que
        efetivamente responderam. Não é confiança nem qualidade da crítica."""
        return self.round_result.successful_count / self.round_result.total_participants

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unknown_accounting_components(self) -> bool:
        """Passthrough de round_result.has_unknown_accounting_components — a
        crítica é uma rodada só, então não tem nada pra combinar."""
        return self.round_result.has_unknown_accounting_components


class DebateResult(BaseModel):
    """Agregado final da Etapa 5. Compõe `InitialResponsesResult` +
    `CritiqueResult` (ou None) + tudo que foi produzido no meio — sem
    incluir nada de fases futuras (JudgeVerdict, evidência nova, resposta
    final, verificação, persistência). Quando essas fases existirem, cada
    uma tem seu próprio agregado que COMPÕE este, não o estende.

    `claims` é o conjunto COMPLETO (brutas + canônicas, dos dois rounds) —
    não só as "atuais". Descartar as brutas do resultado final
    contradiria a mesma auditabilidade que já motiva manter todo
    `ModelResponse` intacto; `get_current_claims()` (app/debate/claims.py)
    é quem filtra pra "atuais", como uma função separada, aplicada por
    quem precisar.
    """

    model_config = _CONFIG

    initial_result: InitialResponsesResult
    critique_round: CritiqueResult | None
    claims: list[Claim] = Field(default_factory=list)
    claim_processing_attempts: list[ClaimProcessingAttempt] = Field(default_factory=list)
    # Etapa 15 — verificação determinística NUNCA consome tokens/rede (é
    # código Python síncrono, sobre a MESMA chamada de extração já
    # contabilizada em claim_processing_attempts) — deliberadamente FORA
    # de cumulative_input_tokens/cumulative_output_tokens/cumulative_cost_usd/
    # has_unknown_accounting_components abaixo. Nenhuma categoria de custo
    # nova.
    numeric_verification_attempts: list[DeterministicVerificationAttempt] = Field(
        default_factory=list
    )
    claim_processor_provider: str = Field(min_length=1)
    debate_skipped_reason: (
        Literal["insufficient_initial_quorum", "budget_exhausted_before_critique"] | None
    ) = None
    # Campo armazenado comum (não computed_field): precisa do limiar
    # externo (RunConfig.max_total_tokens/max_cost_usd), que este objeto
    # não guarda — mesmo padrão já usado por InitialResponsesResult.budget_exceeded.
    cumulative_budget_exceeded: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cumulative_input_tokens(self) -> int:
        total = self.initial_result.total_input_tokens
        if self.critique_round is not None:
            total += self.critique_round.round_result.total_input_tokens
        total += sum(
            (a.usage.input_tokens or 0) for a in self.claim_processing_attempts if a.usage
        )
        return total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cumulative_output_tokens(self) -> int:
        total = self.initial_result.total_output_tokens
        if self.critique_round is not None:
            total += self.critique_round.round_result.total_output_tokens
        total += sum(
            (a.usage.output_tokens or 0) for a in self.claim_processing_attempts if a.usage
        )
        return total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cumulative_cost_usd(self) -> float:
        total = self.initial_result.total_cost_usd
        if self.critique_round is not None:
            total += self.critique_round.round_result.total_cost_usd
        total += sum((a.cost_usd or 0.0) for a in self.claim_processing_attempts)
        return total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unknown_accounting_components(self) -> bool:
        """OR de: rodada inicial + crítica (se ocorreu) + claim processing
        attempts. `critique_round is None` (pulada) contribui False — sem
        chamada, sem desconhecido; mesma regra pra
        `claim_processing_attempts=[]`. A porção de claim processing
        reusa `sum_usage_and_cost` (Etapa 17A, B3) -- considera tanto
        `cost_usd is None` quanto `had_uncertain_prior_attempts=True`,
        nunca uma segunda definição divergente da já usada em
        `SourceAnalysisResult`/`JudgeResult`/`EditorResult`. As porções
        de rodada inicial/crítica já herdam a regra completa
        automaticamente via `RoundResult`/`InitialResponsesResult`, que
        também são somadas com `sum_usage_and_cost` internamente."""
        return (
            self.initial_result.has_unknown_accounting_components
            or (
                self.critique_round is not None
                and self.critique_round.has_unknown_accounting_components
            )
            or sum_usage_and_cost(self.claim_processing_attempts)[3]
        )

    @model_validator(mode="after")
    def _skip_reason_matches_critique_presence(self) -> DebateResult:
        if self.critique_round is None and self.debate_skipped_reason is None:
            raise ValueError(
                "critique_round=None exige debate_skipped_reason preenchido "
                "(motivo de a crítica ter sido pulada)"
            )
        if self.critique_round is not None and self.debate_skipped_reason is not None:
            raise ValueError(
                "debate_skipped_reason só deve ser preenchido quando "
                "critique_round=None"
            )
        return self

    @model_validator(mode="after")
    def _all_processing_attempts_use_declared_provider(self) -> DebateResult:
        for attempt in self.claim_processing_attempts:
            if attempt.provider != self.claim_processor_provider:
                raise ValueError(
                    f"ClaimProcessingAttempt.provider={attempt.provider!r} diverge de "
                    f"claim_processor_provider={self.claim_processor_provider!r} — "
                    "nenhuma troca silenciosa de provider é permitida"
                )
        return self

    @model_validator(mode="after")
    def _verification_attempts_reference_real_claims(self) -> DebateResult:
        """Etapa 15: cada `claim_id` referenciado precisa existir entre
        `self.claims` -- nunca uma claim canônica de fusão (que nunca é
        alvo de verificação, ver numeric_verification.py), sempre uma
        claim BRUTA que este mesmo DebateResult já contém."""
        known_ids = {c.id for c in self.claims}
        for attempt in self.numeric_verification_attempts:
            if attempt.claim_id not in known_ids:
                raise ValueError(
                    f"DeterministicVerificationAttempt.claim_id={attempt.claim_id!r} "
                    "não corresponde a nenhuma claim deste DebateResult"
                )
        return self
