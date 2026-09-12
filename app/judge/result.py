"""`JudgeResult` — agregado final da Etapa 6."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.judge.attempt import JudgeAttempt
from app.models.domain import JudgeVerdict
from app.orchestrator.budget import sum_usage_and_cost

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class JudgeResult(BaseModel):
    """`verdict`/`verdict_unavailable_reason` são mutuamente exclusivos e
    exaustivos (validado abaixo) — nunca os dois `None` simultaneamente,
    nunca os dois preenchidos. O motivo de indisponibilidade e o estado de
    budget são informações INDEPENDENTES: `verdict_unavailable_reason`
    explica por que não há veredito; `cumulative_budget_exceeded` descreve
    o estado acumulado do run, e pode ser `True` mesmo quando o motivo é
    `"no_claims_to_judge"` (ex.: debate já estourou o budget E não sobrou
    claim nenhuma pra julgar — são dois fatos simultâneos e independentes).

    `judge_input_tokens`/`judge_output_tokens`/`judge_cost_usd` são
    `computed_field` — somam só `self.attempts` (sem dependência externa),
    sempre deriváveis. `cumulative_budget_exceeded` é campo armazenado
    comum: precisa do limiar do `RunConfig` E do total acumulado do
    `DebateResult`, nenhum dos dois guardado aqui — copiar os totais do
    `DebateResult` pra dentro deste objeto seria o campo redundante que a
    arquitetura pediu pra evitar. O total combinado (debate + Judge) é um
    one-liner pra quem precisar: `debate_result.cumulative_input_tokens +
    judge_result.judge_input_tokens`, não precisa virar campo do schema.
    """

    model_config = _CONFIG

    verdict: JudgeVerdict | None
    attempts: list[JudgeAttempt] = Field(default_factory=list)
    verdict_unavailable_reason: (
        Literal[
            "budget_exhausted_before_judge",
            "no_claims_to_judge",
            "judge_transport_failed",
            "judge_output_invalid",
            # Etapa 17A.2 -- subcaso ESPECÍFICO de falha (transporte OU
            # parse) cujo `provider_finish_reason` confirma corte por
            # teto de output (ver
            # `app/providers/base.py:is_known_output_truncation`) --
            # distinto de "judge_output_invalid" genérico porque a causa
            # é conhecida (não um output genuinamente incoerente) e de
            # "judge_transport_failed" porque nem toda falha de
            # transporte é truncamento.
            "judge_output_truncated",
        ]
        | None
    ) = None
    judge_provider: str = Field(min_length=1)
    cumulative_budget_exceeded: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def judge_input_tokens(self) -> int:
        return sum((a.usage.input_tokens or 0) for a in self.attempts if a.usage is not None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def judge_output_tokens(self) -> int:
        return sum((a.usage.output_tokens or 0) for a in self.attempts if a.usage is not None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def judge_cost_usd(self) -> float:
        return sum((a.cost_usd or 0.0) for a in self.attempts)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unknown_accounting_components(self) -> bool:
        """`attempts=[]` (no_claims_to_judge/budget_exhausted_before_judge)
        -> False — sem chamada, sem desconhecido. `True` se alguma
        tentativa real teve `cost_usd=None` OU
        `had_uncertain_prior_attempts=True` (Etapa 17A, B3 — uma
        tentativa de transporte ANTERIOR à que produziu o attempt final
        teve accounting desconhecida, mesmo o custo final sendo
        conhecido). Reusa `sum_usage_and_cost` -- mesma regra completa já
        usada em `SourceAnalysisResult`, nunca uma segunda definição
        divergente."""
        return sum_usage_and_cost(self.attempts)[3]

    @model_validator(mode="after")
    def _verdict_presence_matches_reason(self) -> JudgeResult:
        if self.verdict is not None and self.verdict_unavailable_reason is not None:
            raise ValueError(
                "verdict presente não deve ter verdict_unavailable_reason preenchido"
            )
        if self.verdict is None and self.verdict_unavailable_reason is None:
            raise ValueError("verdict ausente exige verdict_unavailable_reason preenchido")
        return self

    @model_validator(mode="after")
    def _all_attempts_use_declared_provider(self) -> JudgeResult:
        for attempt in self.attempts:
            if attempt.provider != self.judge_provider:
                raise ValueError(
                    f"JudgeAttempt.provider={attempt.provider!r} diverge de "
                    f"judge_provider={self.judge_provider!r} — nenhuma troca "
                    "silenciosa de provider é permitida"
                )
        return self
