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


# ---------------------------------------------------------------------------
# Primary Answer -- provider roteirizado ciente da chamada de planejamento
# ---------------------------------------------------------------------------

from tests.debate.fakes import ScriptedProvider, text_response  # noqa: E402


def is_primary_answer_request(request) -> bool:
    return "apenas escolhe ids" in (request.system_prompt or "")


class PrimaryAwareScriptedProvider(ScriptedProvider):
    """`ScriptedProvider` que separa a chamada de PLANEJAMENTO da resposta
    principal da chamada de plano de estilo (`editor_v1`): a lista `responses`
    continua sendo SÓ a do plano de estilo (testes existentes inalterados) e
    `primary_responses` roteiriza a outra. Sem roteiro, o default é uma saída
    inválida de custo zero conhecido (o Primary Answer cai no fallback e o
    accounting dos testes existentes não muda)."""

    def __init__(self, name, responses, primary_responses=None, **kwargs):
        super().__init__(name, responses, **kwargs)
        self.primary_requests: list = []
        self._primary_responses = list(primary_responses) if primary_responses is not None else None

    async def complete(self, request, *, execution_policy=None):
        if is_primary_answer_request(request):
            self.primary_requests.append(request)
            if self._primary_responses:
                return self._primary_responses.pop(0)
            return text_response(
                self.provider_name, "{}", input_tokens=0, output_tokens=0, cost_usd=0.0
            )
        return await super().complete(request, execution_policy=execution_policy)
