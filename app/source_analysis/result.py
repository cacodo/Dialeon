"""
`SourceAnalysisResult` — agregado final da fase de análise de fonte,
Etapa 16.

Mesmo padrão de mutual-exclusividade já usado por `JudgeResult`
(`verdict`/`verdict_unavailable_reason`), mas aplicado a
`claim_results`/`skipped_reason` — nunca a `attempts`: `attempts` pode
legitimamente coexistir com `skipped_reason` preenchido (uma chamada
real aconteceu e falhou -- transporte ou parse -- distinto de nunca ter
sido tentada). `claim_results` só é não-vazio quando `skipped_reason is
None` (sucesso real).

`CouncilRunResult.source_analysis_result` é `SourceAnalysisResult | None`
— `None` SÓ quando `RunConfig.source_text is None` (nenhuma fonte
fornecida: a análise "não existe", nem sequer é tentada/pulada — ver
`app/source_analysis/analyzer.py`). Quando uma fonte FOI fornecida, este
objeto sempre existe, mesmo que a análise em si tenha sido pulada
(budget/zero claims) ou falhado — a distinção entre "nunca chamado"
(`skipped_reason`) e "chamado e falhou" (`attempts` preenchido,
`claim_results=[]`) é preservada exatamente como em todo o resto do
projeto (NEVER CALLED != CALLED AND FAILED).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.orchestrator.budget import sum_usage_and_cost
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.models import RejectedSourceEntry, SourceClaimAnalysisResult

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class SourceAnalysisResult(BaseModel):
    model_config = _CONFIG

    attempts: list[SourceAnalysisAttempt] = Field(default_factory=list)
    claim_results: list[SourceClaimAnalysisResult] = Field(default_factory=list)
    skipped_reason: (
        Literal[
            "no_claims_to_analyze",
            "budget_exhausted_before_source_analysis",
            "source_analysis_transport_failed",
            "source_analysis_output_invalid",
        ]
        | None
    ) = None
    source_analyzer_provider: str = Field(min_length=1)
    cumulative_budget_exceeded: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_analysis_input_tokens(self) -> int:
        return sum_usage_and_cost(self.attempts)[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_analysis_output_tokens(self) -> int:
        return sum_usage_and_cost(self.attempts)[1]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_analysis_cost_usd(self) -> float:
        return sum_usage_and_cost(self.attempts)[2]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unknown_accounting_components(self) -> bool:
        return sum_usage_and_cost(self.attempts)[3]

    @model_validator(mode="after")
    def _success_requires_at_least_one_attempt(self) -> SourceAnalysisResult:
        """`skipped_reason is None` (sucesso real, com `claim_results`
        potencialmente preenchido) exige pelo menos um attempt real --
        não existe "sucesso" sem nenhuma chamada ter acontecido. O
        inverso NÃO é verdade: `skipped_reason` preenchido pode
        coexistir com `attempts` não-vazio (falha de transporte/parse
        DEPOIS de uma tentativa real, distinto de nunca ter tentado --
        NEVER CALLED != CALLED AND FAILED)."""
        if self.skipped_reason is None and len(self.attempts) == 0:
            raise ValueError("skipped_reason ausente (sucesso) exige pelo menos um attempt real")
        return self

    @model_validator(mode="after")
    def _claim_results_only_when_not_skipped(self) -> SourceAnalysisResult:
        if self.skipped_reason is not None and self.claim_results:
            raise ValueError("skipped_reason preenchido não deve ter claim_results")
        return self

    @model_validator(mode="after")
    def _all_attempts_use_declared_provider(self) -> SourceAnalysisResult:
        for attempt in self.attempts:
            if attempt.provider != self.source_analyzer_provider:
                raise ValueError(
                    f"SourceAnalysisAttempt.provider={attempt.provider!r} diverge de "
                    f"source_analyzer_provider={self.source_analyzer_provider!r} — "
                    "nenhuma troca silenciosa de provider é permitida"
                )
        return self

    @model_validator(mode="after")
    def _rejected_entries_never_masquerade_as_relations(self) -> SourceAnalysisResult:
        """Checagem defensiva (o discriminador `kind` já impede isso a
        nível de tipo, mas documenta o invariante explicitamente):
        `RejectedSourceEntry` nunca é confundido com uma relação
        epistêmica real."""
        for result in self.claim_results:
            if isinstance(result, RejectedSourceEntry) and result.reason not in (
                "omitted_by_model",
                "duplicate_claim_id",
                "invalid_entry",
            ):
                raise ValueError(f"reason inesperado: {result.reason!r}")
        return self
