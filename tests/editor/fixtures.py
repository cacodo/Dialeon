from __future__ import annotations

from app.judge.result import JudgeResult
from app.models.domain import ClaimAssessment, JudgeVerdict
from app.source_analysis.models import ValidSourceRelation
from app.source_analysis.result import SourceAnalysisResult

# Reexporta os builders de Claim/ModelResponse/DebateResult já existentes —
# não duplica o que a Etapa 6 já construiu.
from tests.judge.fixtures import (  # noqa: F401
    canonical_claim,
    debate_result,
    model_response,
    raw_claim,
)


def verdict(assessments: list[ClaimAssessment], **overrides) -> JudgeVerdict:
    fields = dict(
        evaluated_through_round=1,
        judge_model="claude-test",
        claim_assessments=assessments,
        debate_limitations=[],
        confidence=0.7,
        reasoning="justificativa do juiz",
    )
    fields.update(overrides)
    return JudgeVerdict(**fields)


def judge_result(verdict_obj: JudgeVerdict | None, **overrides) -> JudgeResult:
    fields = dict(
        verdict=verdict_obj,
        attempts=[],
        verdict_unavailable_reason=(
            None if verdict_obj is not None else "budget_exhausted_before_judge"
        ),
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    fields.update(overrides)
    return JudgeResult(**fields)


def source_relation(claim_id: str, relation: str, **overrides) -> ValidSourceRelation:
    """Etapa 16+ (patch de apresentação com fonte). `relation="unresolved"`
    nunca tem excerpt (mesma regra de `ValidSourceRelation`); supports/
    contradicts tem um excerpt/range default plausível, sobrescrevível."""
    fields: dict = {"claim_id": claim_id, "relation": relation}
    if relation != "unresolved":
        fields.update(excerpt="trecho da fonte", excerpt_start=0, excerpt_end=15)
    fields.update(overrides)
    return ValidSourceRelation(**fields)


def source_analysis_result(claim_results: list, **overrides) -> SourceAnalysisResult:
    """Etapa 16+ (patch de apresentação com fonte) -- default "concluída
    com sucesso" (skipped_reason=None), com um attempt mínimo pra
    satisfazer `_success_requires_at_least_one_attempt`."""
    from app.models.provider_models import PricingProvenance, TokenUsage
    from app.source_analysis.attempt import SourceAnalysisAttempt

    fields = dict(
        attempts=[
            SourceAnalysisAttempt(
                attempt_number=1,
                provider="anthropic",
                requested_model="claude-sonnet-5",
                model="claude-sonnet-5",
                transport_status="success",
                transport_attempts=1,
                raw_output_text="{}",
                parse_status="accepted",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                cost_usd=0.001,
                pricing_provenance=PricingProvenance(
                    source_id="test-table",
                    tier="standard",
                    input_rate_usd_per_million_tokens=1.0,
                    output_rate_usd_per_million_tokens=2.0,
                ),
                latency_ms=100,
            )
        ],
        claim_results=claim_results,
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    fields.update(overrides)
    return SourceAnalysisResult(**fields)
