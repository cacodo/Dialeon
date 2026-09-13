from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.debate.processing_record import ClaimProcessingAttempt
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType, TokenUsage


def _base(**overrides) -> dict:
    fields = dict(
        operation="extraction",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        model="claude-test",
        target_model_response_id="resp-1",
        target_claim_ids=[],
        transport_status="success",
        transport_error=None,
        transport_attempts=1,
        raw_output_text='{"claims": []}',
        parse_status="accepted",
        parse_error_message=None,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        cost_usd=None,
        latency_ms=100,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return fields


def test_accepted_extraction_attempt_is_valid():
    attempt = ClaimProcessingAttempt(**_base())
    assert attempt.parse_status == "accepted"


def test_is_frozen():
    attempt = ClaimProcessingAttempt(**_base())
    with pytest.raises(ValidationError):
        attempt.parse_status = "malformed"  # type: ignore[misc]


def test_transport_error_requires_not_attempted_parse_status():
    with pytest.raises(ValidationError, match="not_attempted"):
        ClaimProcessingAttempt(
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
        ClaimProcessingAttempt(
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
        ClaimProcessingAttempt(**_base(parse_status="not_attempted"))


def test_malformed_requires_error_message():
    with pytest.raises(ValidationError, match="parse_error_message"):
        ClaimProcessingAttempt(
            **_base(parse_status="malformed", parse_error_message=None)
        )


def test_accepted_cannot_have_error_message():
    with pytest.raises(ValidationError, match="parse_error_message"):
        ClaimProcessingAttempt(
            **_base(parse_status="accepted", parse_error_message="não deveria existir")
        )


def test_extraction_requires_target_model_response_id():
    with pytest.raises(ValidationError, match="target_model_response_id"):
        ClaimProcessingAttempt(**_base(target_model_response_id=None))


def test_extraction_cannot_have_target_claim_ids():
    with pytest.raises(ValidationError, match="target_claim_ids"):
        ClaimProcessingAttempt(**_base(target_claim_ids=["a", "b"]))


def test_grouping_requires_target_claim_ids():
    with pytest.raises(ValidationError, match="target_claim_ids"):
        ClaimProcessingAttempt(
            **_base(
                operation="grouping",
                target_model_response_id=None,
                target_claim_ids=[],
                raw_output_text='{"groups": [], "ungrouped_claim_ids": []}',
            )
        )


def test_grouping_cannot_have_target_model_response_id():
    with pytest.raises(ValidationError, match="target_model_response_id"):
        ClaimProcessingAttempt(
            **_base(
                operation="grouping",
                target_model_response_id="resp-1",
                target_claim_ids=["a", "b"],
                raw_output_text='{"groups": [], "ungrouped_claim_ids": ["a", "b"]}',
            )
        )


def test_grouping_valid_attempt():
    attempt = ClaimProcessingAttempt(
        **_base(
            operation="grouping",
            target_model_response_id=None,
            target_claim_ids=["a", "b"],
            raw_output_text='{"groups": [], "ungrouped_claim_ids": ["a", "b"]}',
        )
    )
    assert attempt.operation == "grouping"


# ---------------------------------------------------------------------------
# Cross-round claim reconciliation -- operation="reconciliation" (20)
# ---------------------------------------------------------------------------


def test_reconciliation_valid_with_target_claim_ids():
    """20 -- operation='reconciliation' válida com target_claim_ids não-
    vazio, round_number=2 (ver docstring do campo -- reconciliação SEMPRE
    ocorre depois da Round 2, nunca inventa uma Round 3)."""
    attempt = ClaimProcessingAttempt(
        **_base(
            operation="reconciliation",
            round_number=2,
            target_model_response_id=None,
            target_claim_ids=["r1-claim", "r2-claim"],
            raw_output_text='{"groups": [], "ungrouped_claim_ids": ["r1-claim", "r2-claim"]}',
        )
    )
    assert attempt.operation == "reconciliation"
    assert attempt.round_number == 2


def test_reconciliation_rejects_target_model_response_id():
    """20 -- mesmo formato de target que 'grouping': reconciliação nunca
    tem target_model_response_id (não processa uma resposta específica,
    compara N claims já existentes)."""
    with pytest.raises(ValidationError, match="target_model_response_id"):
        ClaimProcessingAttempt(
            **_base(
                operation="reconciliation",
                round_number=2,
                target_model_response_id="resp-1",
                target_claim_ids=["r1-claim", "r2-claim"],
                raw_output_text='{"groups": [], "ungrouped_claim_ids": ["r1-claim", "r2-claim"]}',
            )
        )


def test_reconciliation_requires_target_claim_ids():
    """20 -- reconciliação exige target_claim_ids não-vazio, mesma regra
    de 'grouping'."""
    with pytest.raises(ValidationError, match="target_claim_ids"):
        ClaimProcessingAttempt(
            **_base(
                operation="reconciliation",
                round_number=2,
                target_model_response_id=None,
                target_claim_ids=[],
                raw_output_text='{"groups": [], "ungrouped_claim_ids": []}',
            )
        )


def test_extraction_behavior_unchanged_after_reconciliation_added():
    """20 -- extração continua exigindo target_model_response_id e
    proibindo target_claim_ids, comportamento inalterado por
    'reconciliation' ter sido adicionado ao Literal."""
    attempt = ClaimProcessingAttempt(**_base(operation="extraction"))
    assert attempt.operation == "extraction"
    with pytest.raises(ValidationError, match="target_model_response_id"):
        ClaimProcessingAttempt(**_base(operation="extraction", target_model_response_id=None))
    with pytest.raises(ValidationError, match="target_claim_ids"):
        ClaimProcessingAttempt(**_base(operation="extraction", target_claim_ids=["a"]))


def test_grouping_behavior_unchanged_after_reconciliation_added():
    """20 -- 'grouping' continua com exatamente as mesmas regras de
    target de antes -- 'reconciliation' compartilha o FORMATO, mas é um
    rótulo semântico distinto, nunca confundido com 'grouping' em nenhum
    teste ou validação."""
    attempt = ClaimProcessingAttempt(
        **_base(
            operation="grouping",
            target_model_response_id=None,
            target_claim_ids=["a", "b"],
            raw_output_text='{"groups": [], "ungrouped_claim_ids": ["a", "b"]}',
        )
    )
    assert attempt.operation == "grouping"


def test_unknown_operation_value_rejected_by_type() -> None:
    """Hardening -- um valor de operation fora do Literal fechado
    (extraction/grouping/reconciliation) é rejeitado na validação de
    tipo do Pydantic, antes mesmo de `_targets_match_operation` rodar."""
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_base(operation="made_up_future_operation"))
