"""
Patch de revisão do Stage 17A — blocker confirmado: `DebateResult`/
`JudgeResult`/`EditorResult.has_unknown_accounting_components` ainda
usavam a definição PRÉ-Stage-17A (`cost_usd is None` sozinho), perdendo o
sinal de `had_uncertain_prior_attempts=True` na agregação.
"""

from __future__ import annotations

import pytest

from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.result import DebateResult
from app.editor.attempt import EditorAttempt
from app.editor.result import EditorResult, FinalAnswer
from app.judge.attempt import JudgeAttempt
from app.judge.result import JudgeResult
from tests.judge.fixtures import debate_result, model_response, raw_claim


def _claim_processing_attempt(
    cost_usd: float | None, had_uncertain_prior_attempts: bool
) -> ClaimProcessingAttempt:
    return ClaimProcessingAttempt(
        operation="extraction",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        target_model_response_id="resp-1",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{...}",
        parse_status="accepted",
        cost_usd=cost_usd,
        latency_ms=100,
        had_uncertain_prior_attempts=had_uncertain_prior_attempts,
    )


def _judge_attempt(cost_usd: float | None, had_uncertain_prior_attempts: bool) -> JudgeAttempt:
    return JudgeAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{...}",
        parse_status="accepted",
        cost_usd=cost_usd,
        latency_ms=100,
        had_uncertain_prior_attempts=had_uncertain_prior_attempts,
    )


def _editor_attempt(cost_usd: float | None, had_uncertain_prior_attempts: bool) -> EditorAttempt:
    return EditorAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{...}",
        parse_status="accepted",
        cost_usd=cost_usd,
        latency_ms=100,
        had_uncertain_prior_attempts=had_uncertain_prior_attempts,
    )


# ---------------------------------------------------------------------------
# A — DebateResult
# ---------------------------------------------------------------------------


def test_a_debate_result_known_cost_uncertain_prior_marks_incomplete():
    c1 = raw_claim("A", "resp-1", provider="openai")
    attempt = _claim_processing_attempt(cost_usd=0.01, had_uncertain_prior_attempts=True)
    dr = debate_result([c1], [model_response("openai")], claim_processing_attempts=[attempt])

    assert dr.cumulative_cost_usd >= 0.01  # custo conhecido preservado
    assert dr.has_unknown_accounting_components is True


def test_a_control_debate_result_known_cost_no_uncertainty_stays_complete():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", cost_usd=0.5)  # custo conhecido -- isola a variável
    attempt = _claim_processing_attempt(cost_usd=0.01, had_uncertain_prior_attempts=False)
    dr = debate_result([c1], [mr], claim_processing_attempts=[attempt])

    assert dr.has_unknown_accounting_components is False


def test_e_debate_result_unknown_cost_still_marks_incomplete():
    c1 = raw_claim("A", "resp-1", provider="openai")
    attempt = _claim_processing_attempt(cost_usd=None, had_uncertain_prior_attempts=False)
    dr = debate_result([c1], [model_response("openai")], claim_processing_attempts=[attempt])

    assert dr.has_unknown_accounting_components is True


# ---------------------------------------------------------------------------
# B — JudgeResult
# ---------------------------------------------------------------------------


def test_b_judge_result_known_cost_uncertain_prior_marks_incomplete():
    attempt = _judge_attempt(cost_usd=0.02, had_uncertain_prior_attempts=True)
    jr = JudgeResult(
        verdict=None,
        attempts=[attempt],
        verdict_unavailable_reason="judge_output_invalid",
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    assert jr.judge_cost_usd == 0.02  # custo conhecido preservado
    assert jr.has_unknown_accounting_components is True


def test_b_control_judge_result_known_cost_no_uncertainty_stays_complete():
    attempt = _judge_attempt(cost_usd=0.02, had_uncertain_prior_attempts=False)
    jr = JudgeResult(
        verdict=None,
        attempts=[attempt],
        verdict_unavailable_reason="judge_output_invalid",
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    assert jr.has_unknown_accounting_components is False


# ---------------------------------------------------------------------------
# C — EditorResult
# ---------------------------------------------------------------------------


def test_c_editor_result_known_cost_uncertain_prior_marks_incomplete():
    attempt = _editor_attempt(cost_usd=0.03, had_uncertain_prior_attempts=True)
    final_answer = FinalAnswer(
        answer_text="Resposta composta pela LLM.",
        limitations=[],
        status="llm_composed",
        editor_model="claude-sonnet-5",
        based_on_verdict_id="verdict-1",
        judge_confidence=0.7,
    )
    er = EditorResult(
        final_answer=final_answer,
        attempts=[attempt],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    assert er.editor_cost_usd == 0.03  # custo conhecido preservado
    assert er.has_unknown_accounting_components is True


def test_c_control_editor_result_known_cost_no_uncertainty_stays_complete():
    attempt = _editor_attempt(cost_usd=0.03, had_uncertain_prior_attempts=False)
    final_answer = FinalAnswer(
        answer_text="Resposta composta pela LLM.",
        limitations=[],
        status="llm_composed",
        editor_model="claude-sonnet-5",
        based_on_verdict_id="verdict-1",
        judge_confidence=0.7,
    )
    er = EditorResult(
        final_answer=final_answer,
        attempts=[attempt],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    assert er.has_unknown_accounting_components is False


# ---------------------------------------------------------------------------
# F — CouncilRunResult, ponta a ponta
# ---------------------------------------------------------------------------


def test_f_council_run_result_propagates_uncertainty_without_losing_known_cost():
    from tests.storage.fixtures import full_council_run_result

    result = full_council_run_result()
    assert result.has_unknown_accounting_components is False  # baseline limpo

    extra_attempt = _claim_processing_attempt(cost_usd=0.05, had_uncertain_prior_attempts=True)
    new_debate = result.debate_result.model_copy(
        update={
            "claim_processing_attempts": [
                *result.debate_result.claim_processing_attempts,
                extra_attempt,
            ]
        }
    )
    result_with_uncertainty = result.model_copy(update={"debate_result": new_debate})

    assert result_with_uncertainty.has_unknown_accounting_components is True
    # custo conhecido do attempt extra continua contribuindo pro total --
    # nunca foi zerado/descartado pela incerteza.
    assert result_with_uncertainty.total_cost_usd == pytest.approx(
        result.total_cost_usd + 0.05
    )
