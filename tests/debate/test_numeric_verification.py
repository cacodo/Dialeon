from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.council.fixtures import run_config
from app.debate.numeric_verification import (
    ArithmeticAssertion,
    InvalidNumericLiteral,
    build_verification_attempt,
    parse_bounded_decimal,
)


# ---------------------------------------------------------------------------
# Aritmética exata — supports/contradicts
# ---------------------------------------------------------------------------


def test_valid_addition_supports():
    attempt = build_verification_attempt(
        "claim-1", {"kind": "arithmetic", "left": "2", "operator": "+", "right": "3", "asserted_result": "5"}
    )
    assert attempt.state == "supports"
    assert attempt.computed_result == "5"


def test_valid_subtraction_supports():
    attempt = build_verification_attempt(
        "claim-1", {"left": "10", "operator": "-", "right": "4", "asserted_result": "6"}
    )
    assert attempt.state == "supports"


def test_valid_multiplication_supports():
    attempt = build_verification_attempt(
        "claim-1", {"left": "6", "operator": "*", "right": "7", "asserted_result": "42"}
    )
    assert attempt.state == "supports"


def test_valid_division_supports():
    attempt = build_verification_attempt(
        "claim-1", {"left": "10", "operator": "/", "right": "4", "asserted_result": "2.5"}
    )
    assert attempt.state == "supports"


def test_wrong_result_contradicts():
    attempt = build_verification_attempt(
        "claim-1", {"left": "2", "operator": "+", "right": "2", "asserted_result": "5"}
    )
    assert attempt.state == "contradicts"
    assert attempt.computed_result == "4"


def test_one_third_vs_0_33_contradicts_exactly():
    """O caso central da correção final: 1/3 comparado a 0.33 NUNCA pode
    virar supports por arredondamento implícito."""
    attempt = build_verification_attempt(
        "claim-1", {"left": "1", "operator": "/", "right": "3", "asserted_result": "0.33"}
    )
    assert attempt.state == "contradicts"
    assert attempt.computed_result == "1/3"  # forma exata, não decimal aproximado


def test_percentage_normalized_as_multiplication():
    """'15% de 200 é 30' -> left=0.15, operator=*, right=200."""
    attempt = build_verification_attempt(
        "claim-1", {"left": "0.15", "operator": "*", "right": "200", "asserted_result": "30"}
    )
    assert attempt.state == "supports"
    assert attempt.computed_result == "30"


def test_division_by_zero_is_computation_failed():
    attempt = build_verification_attempt(
        "claim-1", {"left": "5", "operator": "/", "right": "0", "asserted_result": "0"}
    )
    assert attempt.state == "computation_failed"
    assert attempt.computed_result is None
    assert attempt.assertion is not None  # a asserção em si era válida


# ---------------------------------------------------------------------------
# invalid_proposal
# ---------------------------------------------------------------------------


def test_invalid_operator_is_invalid_proposal():
    attempt = build_verification_attempt(
        "claim-1", {"left": "2", "operator": "^", "right": "3", "asserted_result": "8"}
    )
    assert attempt.state == "invalid_proposal"
    assert attempt.assertion is None
    assert attempt.raw_proposal == {
        "left": "2",
        "operator": "^",
        "right": "3",
        "asserted_result": "8",
    }


def test_invalid_shape_missing_field_is_invalid_proposal():
    attempt = build_verification_attempt("claim-1", {"left": "2", "operator": "+"})
    assert attempt.state == "invalid_proposal"


def test_completely_unrelated_payload_is_invalid_proposal():
    attempt = build_verification_attempt("claim-1", "isso não é nem um dict")
    assert attempt.state == "invalid_proposal"
    assert attempt.raw_proposal == "isso não é nem um dict"


def test_malicious_payload_cannot_execute_code():
    """Payload que parece código não deve, sob nenhuma circunstância,
    ser executado — deve apenas ser rejeitado como dado."""
    malicious = {
        "left": "__import__('os').system('echo pwned')",
        "operator": "+",
        "right": "1",
        "asserted_result": "1",
    }
    attempt = build_verification_attempt("claim-1", malicious)
    assert attempt.state == "invalid_proposal"
    assert attempt.raw_proposal == malicious  # preservado como DADO, nunca avaliado


def test_scientific_notation_rejected_in_v1():
    attempt = build_verification_attempt(
        "claim-1", {"left": "1e10", "operator": "+", "right": "1", "asserted_result": "2"}
    )
    assert attempt.state == "invalid_proposal"


# ---------------------------------------------------------------------------
# never_attempted
# ---------------------------------------------------------------------------


def test_no_proposal_is_never_attempted_no_record():
    attempt = build_verification_attempt("claim-1", None)
    assert attempt is None


# ---------------------------------------------------------------------------
# Limites de segurança numérica
# ---------------------------------------------------------------------------


def test_parse_bounded_decimal_accepts_valid_literal():
    from decimal import Decimal

    assert parse_bounded_decimal("123.456") == Decimal("123.456")
    assert parse_bounded_decimal("0") == Decimal("0")
    assert parse_bounded_decimal("-5.5") == Decimal("-5.5")


def test_parse_bounded_decimal_rejects_too_many_digits():
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal("1" * 31)


def test_parse_bounded_decimal_rejects_excessive_magnitude():
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal("9" * 19)


def test_parse_bounded_decimal_rejects_scientific_notation():
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal("1e5")


def test_parse_bounded_decimal_rejects_non_string():
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal(5)  # type: ignore[arg-type]


def test_numeric_bounds_rejection_via_full_pipeline():
    attempt = build_verification_attempt(
        "claim-1",
        {"left": "1" * 31, "operator": "+", "right": "1", "asserted_result": "2"},
    )
    assert attempt.state == "invalid_proposal"


# ---------------------------------------------------------------------------
# Patch de revisão independente (Issue 2) — limite de comprimento TOTAL,
# separado do limite de dígitos significativos.
# ---------------------------------------------------------------------------


def test_extremely_long_fractional_leading_zeros_rejected():
    """'0.000...(muitos zeros)...0001' tem só 1 dígito significativo
    (zeros à esquerda descartados) mas uma string enorme -- precisa ser
    rejeitado pelo limite de comprimento TOTAL, não pelo de dígitos
    significativos."""
    payload = "0." + ("0" * 500) + "1"
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal(payload)


def test_extremely_long_zero_padded_integer_rejected():
    payload = "0" * 500 + "1"
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal(payload)


def test_ordinary_bounded_decimal_still_accepted():
    from decimal import Decimal

    assert parse_bounded_decimal("0.123456789012345678901234567890") == Decimal(
        "0.123456789012345678901234567890"
    )
    assert parse_bounded_decimal("0.123456789") == Decimal("0.123456789")
    assert parse_bounded_decimal("-999999999999999.5") == Decimal("-999999999999999.5")


def test_total_length_boundary_is_deterministic():
    """Isola o limite de COMPRIMENTO do de dígitos SIGNIFICATIVOS: só 1
    dígito significativo nos dois literais (o resto é zero de
    preenchimento), então o único fator em jogo é o comprimento total."""
    exactly_at_limit = "0." + ("0" * 61) + "1"  # 2 + 61 + 1 = 64 caracteres
    assert len(exactly_at_limit) == 64
    parse_bounded_decimal(exactly_at_limit)  # no limite -- não levanta

    over_limit = "0." + ("0" * 62) + "1"  # 65 caracteres
    assert len(over_limit) == 65
    with pytest.raises(InvalidNumericLiteral):
        parse_bounded_decimal(over_limit)


def test_overlong_literal_produces_invalid_proposal_not_computation_failed():
    payload = "0." + ("0" * 500) + "1"
    attempt = build_verification_attempt(
        "claim-1", {"left": payload, "operator": "+", "right": "1", "asserted_result": "1"}
    )
    assert attempt.state == "invalid_proposal"


@pytest.mark.asyncio
async def test_overlong_literal_extraction_isolation_intact():
    """A proposta com literal absurdamente longo continua isolada da
    validação atômica de extração -- a Claim é criada normalmente."""
    import json as json_module

    from tests.debate.fakes import ScriptedProvider, text_response
    from app.debate.claim_extraction import extract_claims
    from app.models.provider_models import TokenUsage
    from app.models.domain import ModelResponse

    overlong = "0." + ("0" * 500) + "1"
    payload = json_module.dumps(
        {
            "claims": [
                {
                    "text": "claim com literal absurdamente longo",
                    "revises_claim_id": None,
                    "proposed_numeric_assertion": {
                        "left": overlong, "operator": "+", "right": "1", "asserted_result": "1",
                    },
                }
            ]
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    response = ModelResponse(
        provider="openai", requested_model="gpt-test", model="gpt-test", round_number=1,
        status="success", response_text="resposta", usage=TokenUsage(input_tokens=10, output_tokens=5),
        latency_ms=50, attempts=1,
    )

    claims, attempts, verifications = await extract_claims(
        response, round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=run_config(), prior_input_tokens=0, prior_output_tokens=0,
        prior_cost_usd=0.0,
    )

    assert len(claims) == 1
    assert attempts[-1].parse_status == "accepted"
    assert verifications[0].state == "invalid_proposal"


# ---------------------------------------------------------------------------
# ArithmeticAssertion — validação estrita direta
# ---------------------------------------------------------------------------


def test_arithmetic_assertion_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ArithmeticAssertion(
            left="1", operator="+", right="2", asserted_result="3", extra_field="nope"
        )


def test_arithmetic_assertion_is_frozen():
    assertion = ArithmeticAssertion(left="1", operator="+", right="2", asserted_result="3")
    with pytest.raises(ValidationError):
        assertion.left = "9"  # type: ignore[misc]
