from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.judge.attempt import JudgeAttempt
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType, TokenUsage


def _base(**overrides) -> dict:
    fields = dict(
        attempt_number=1,
        provider="anthropic",
        model="claude-test",
        transport_status="success",
        transport_error=None,
        transport_attempts=1,
        raw_output_text='{"claim_assessments": []}',
        parse_status="accepted",
        parse_error_message=None,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        cost_usd=None,
        latency_ms=100,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return fields


def test_accepted_attempt_is_valid():
    attempt = JudgeAttempt(**_base())
    assert attempt.parse_status == "accepted"


def test_is_frozen():
    attempt = JudgeAttempt(**_base())
    with pytest.raises(ValidationError):
        attempt.parse_status = "malformed"  # type: ignore[misc]


def test_transport_error_requires_not_attempted():
    with pytest.raises(ValidationError, match="not_attempted"):
        JudgeAttempt(
            **_base(
                transport_status="error",
                transport_error=ProviderErrorInfo(
                    type=ProviderErrorType.RATE_LIMIT, message="x", retryable=True
                ),
                raw_output_text=None,
                parse_status="accepted",
                usage=None,
            )
        )


def test_transport_error_cannot_have_raw_output():
    with pytest.raises(ValidationError, match="raw_output_text"):
        JudgeAttempt(
            **_base(
                transport_status="error",
                transport_error=ProviderErrorInfo(
                    type=ProviderErrorType.TIMEOUT, message="x", retryable=True
                ),
                raw_output_text="algo",
                parse_status="not_attempted",
                usage=None,
            )
        )


def test_transport_success_cannot_have_not_attempted():
    with pytest.raises(ValidationError, match="not_attempted"):
        JudgeAttempt(**_base(parse_status="not_attempted"))


def test_malformed_requires_error_message():
    with pytest.raises(ValidationError, match="parse_error_message"):
        JudgeAttempt(**_base(parse_status="malformed", parse_error_message=None))


def test_accepted_cannot_have_error_message():
    with pytest.raises(ValidationError, match="parse_error_message"):
        JudgeAttempt(**_base(parse_status="accepted", parse_error_message="não deveria existir"))


def test_no_operation_or_target_fields_exist():
    """JudgeAttempt não tem operation/target_* — diferente de
    ClaimProcessingAttempt, porque só existe um tipo de chamada e o alvo
    é sempre 'o DebateResult inteiro'."""
    attempt = JudgeAttempt(**_base())
    assert not hasattr(attempt, "operation")
    assert not hasattr(attempt, "target_model_response_id")
    assert not hasattr(attempt, "target_claim_ids")
