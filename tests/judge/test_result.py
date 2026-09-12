from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.judge.attempt import JudgeAttempt
from app.judge.result import JudgeResult
from app.models.domain import ClaimAssessment, JudgeVerdict
from app.models.provider_models import TokenUsage


def _verdict(**overrides) -> JudgeVerdict:
    fields = dict(
        evaluated_through_round=1,
        judge_model="claude-test",
        confidence=0.7,
        reasoning="justificativa qualquer",
    )
    fields.update(overrides)
    return JudgeVerdict(**fields)


def _attempt(**overrides) -> JudgeAttempt:
    fields = dict(
        attempt_number=1,
        provider="anthropic",
        model="claude-test",
        transport_status="success",
        raw_output_text='{"claim_assessments": []}',
        parse_status="accepted",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        latency_ms=50,
        transport_attempts=1,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return JudgeAttempt(**fields)


def test_verdict_present_requires_no_reason():
    with pytest.raises(ValidationError, match="verdict_unavailable_reason"):
        JudgeResult(
            verdict=_verdict(),
            attempts=[],
            verdict_unavailable_reason="no_claims_to_judge",
            judge_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_verdict_absent_requires_reason():
    with pytest.raises(ValidationError, match="verdict_unavailable_reason"):
        JudgeResult(
            verdict=None,
            attempts=[],
            verdict_unavailable_reason=None,
            judge_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_valid_with_verdict():
    result = JudgeResult(
        verdict=_verdict(),
        attempts=[_attempt()],
        verdict_unavailable_reason=None,
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.verdict is not None


def test_valid_without_verdict():
    result = JudgeResult(
        verdict=None,
        attempts=[],
        verdict_unavailable_reason="no_claims_to_judge",
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.verdict is None


def test_attempt_provider_mismatch_rejected():
    with pytest.raises(ValidationError, match="diverge"):
        JudgeResult(
            verdict=None,
            attempts=[_attempt(provider="openai")],
            verdict_unavailable_reason="judge_output_invalid",
            judge_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_judge_tokens_and_cost_are_computed_from_attempts():
    attempts = [
        _attempt(
            attempt_number=1,
            parse_status="malformed",
            parse_error_message="json ruim",
            usage=TokenUsage(input_tokens=100, output_tokens=20),
            cost_usd=0.01,
        ),
        _attempt(
            attempt_number=2,
            usage=TokenUsage(input_tokens=50, output_tokens=10),
            cost_usd=0.02,
        ),
    ]
    result = JudgeResult(
        verdict=_verdict(),
        attempts=attempts,
        verdict_unavailable_reason=None,
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.judge_input_tokens == 150
    assert result.judge_output_tokens == 30
    assert result.judge_cost_usd == pytest.approx(0.03)


def test_is_frozen():
    result = JudgeResult(
        verdict=None,
        attempts=[],
        verdict_unavailable_reason="no_claims_to_judge",
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    with pytest.raises(ValidationError):
        result.cumulative_budget_exceeded = True  # type: ignore[misc]
