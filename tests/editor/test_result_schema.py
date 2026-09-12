from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.editor.attempt import EditorAttempt
from app.editor.result import EditorResult, FinalAnswer
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType, TokenUsage


def _attempt(**overrides) -> EditorAttempt:
    fields = dict(
        attempt_number=1,
        provider="anthropic",
        model="claude-test",
        transport_status="success",
        transport_error=None,
        transport_attempts=1,
        raw_output_text='{"claim_narratives": []}',
        parse_status="accepted",
        parse_error_message=None,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        cost_usd=None,
        latency_ms=50,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return EditorAttempt(**fields)


def _final_answer(**overrides) -> FinalAnswer:
    fields = dict(
        answer_text="resposta de teste",
        limitations=[],
        status="llm_composed",
        editor_model="claude-test",
        based_on_verdict_id="verdict-1",
        judge_confidence=0.7,
    )
    fields.update(overrides)
    return FinalAnswer(**fields)


# ---------------------------------------------------------------------------
# EditorAttempt
# ---------------------------------------------------------------------------


def test_editor_attempt_accepted_is_valid():
    attempt = _attempt()
    assert attempt.parse_status == "accepted"


def test_editor_attempt_is_frozen():
    attempt = _attempt()
    with pytest.raises(ValidationError):
        attempt.parse_status = "malformed"  # type: ignore[misc]


def test_editor_attempt_transport_error_requires_not_attempted():
    with pytest.raises(ValidationError, match="not_attempted"):
        EditorAttempt(
            attempt_number=1,
            provider="anthropic",
            requested_model="claude-test",
            model="claude-test",
            transport_status="error",
            transport_error=ProviderErrorInfo(
                type=ProviderErrorType.TIMEOUT, message="x", retryable=True
            ),
            transport_attempts=2,
            raw_output_text=None,
            parse_status="accepted",
            usage=None,
            latency_ms=30,
        )


def test_editor_attempt_no_operation_or_target_fields():
    """EditorAttempt não tem operation/target_* — só existe uma operação
    ('compor') e o alvo é sempre o veredito+claims atuais inteiros."""
    attempt = _attempt()
    assert not hasattr(attempt, "operation")
    assert not hasattr(attempt, "target_claim_ids")
    assert not hasattr(attempt, "target_model_response_id")


# ---------------------------------------------------------------------------
# FinalAnswer
# ---------------------------------------------------------------------------


def test_final_answer_is_frozen():
    answer = _final_answer()
    with pytest.raises(ValidationError):
        answer.answer_text = "outro texto"  # type: ignore[misc]


def test_final_answer_extra_forbid():
    with pytest.raises(ValidationError):
        FinalAnswer(
            answer_text="x",
            status="deterministic_no_verdict",
            campo_que_nao_existe="y",  # type: ignore[call-arg]
        )


def test_final_answer_does_not_contain_attempts_field():
    assert "attempts" not in FinalAnswer.model_fields
    assert "cumulative_budget_exceeded" not in FinalAnswer.model_fields
    assert "fallback_reason" not in FinalAnswer.model_fields


def test_final_answer_llm_composed_requires_all_provenance_fields():
    with pytest.raises(ValidationError, match="llm_composed"):
        _final_answer(editor_model=None)
    with pytest.raises(ValidationError, match="llm_composed"):
        _final_answer(based_on_verdict_id=None)
    with pytest.raises(ValidationError, match="llm_composed"):
        _final_answer(judge_confidence=None)


def test_final_answer_llm_planned_requires_all_provenance_fields():
    """Etapa 17B — 'llm_planned' segue a MESMA regra de coerência de
    'llm_composed' (ambos exigem editor_model/based_on_verdict_id/
    judge_confidence): a diferença entre os dois é só QUEM escreveu o
    texto final, nunca a presença de provenance."""
    with pytest.raises(ValidationError, match="llm_planned"):
        _final_answer(status="llm_planned", editor_model=None)
    with pytest.raises(ValidationError, match="llm_planned"):
        _final_answer(status="llm_planned", based_on_verdict_id=None)
    with pytest.raises(ValidationError, match="llm_planned"):
        _final_answer(status="llm_planned", judge_confidence=None)


def test_final_answer_llm_planned_valid():
    answer = _final_answer(status="llm_planned")
    assert answer.status == "llm_planned"
    assert answer.editor_model is not None


def test_final_answer_deterministic_from_verdict_rejects_editor_model():
    with pytest.raises(ValidationError, match="deterministic_from_verdict"):
        _final_answer(
            status="deterministic_from_verdict",
            editor_model="claude-test",  # não deveria ter
            based_on_verdict_id="verdict-1",
            judge_confidence=0.5,
        )


def test_final_answer_deterministic_from_verdict_valid():
    answer = _final_answer(
        status="deterministic_from_verdict",
        editor_model=None,
        based_on_verdict_id="verdict-1",
        judge_confidence=0.5,
    )
    assert answer.editor_model is None


def test_final_answer_deterministic_no_verdict_rejects_provenance():
    with pytest.raises(ValidationError, match="deterministic_no_verdict"):
        _final_answer(
            status="deterministic_no_verdict",
            editor_model=None,
            based_on_verdict_id="verdict-1",  # não deveria ter
            judge_confidence=None,
        )


def test_final_answer_deterministic_no_verdict_valid():
    answer = FinalAnswer(
        answer_text="não avaliado",
        limitations=["motivo"],
        status="deterministic_no_verdict",
    )
    assert answer.editor_model is None
    assert answer.based_on_verdict_id is None
    assert answer.judge_confidence is None


# ---------------------------------------------------------------------------
# EditorResult
# ---------------------------------------------------------------------------


def test_editor_result_final_answer_is_required():
    with pytest.raises(ValidationError):
        EditorResult(
            attempts=[],
            fallback_reason="judge_verdict_unavailable",
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )  # type: ignore[call-arg]


def test_editor_result_llm_composed_requires_no_fallback_reason():
    with pytest.raises(ValidationError, match="fallback_reason"):
        EditorResult(
            final_answer=_final_answer(),
            attempts=[_attempt()],
            fallback_reason="judge_verdict_unavailable",
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_llm_composed_requires_accepted_last_attempt():
    with pytest.raises(ValidationError, match="aceita"):
        EditorResult(
            final_answer=_final_answer(),
            attempts=[],
            fallback_reason=None,
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_llm_composed_valid():
    result = EditorResult(
        final_answer=_final_answer(),
        attempts=[_attempt()],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.fallback_reason is None


def test_editor_result_llm_planned_requires_no_fallback_reason():
    with pytest.raises(ValidationError, match="fallback_reason"):
        EditorResult(
            final_answer=_final_answer(status="llm_planned"),
            attempts=[_attempt()],
            fallback_reason="judge_verdict_unavailable",
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_llm_planned_requires_accepted_last_attempt():
    with pytest.raises(ValidationError, match="aceita"):
        EditorResult(
            final_answer=_final_answer(status="llm_planned"),
            attempts=[],
            fallback_reason=None,
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_llm_planned_valid():
    result = EditorResult(
        final_answer=_final_answer(status="llm_planned"),
        attempts=[_attempt()],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.final_answer.status == "llm_planned"
    assert result.fallback_reason is None


def test_editor_result_deterministic_no_verdict_rejects_attempts():
    answer = FinalAnswer(
        answer_text="x", limitations=[], status="deterministic_no_verdict"
    )
    with pytest.raises(ValidationError, match="deterministic_no_verdict"):
        EditorResult(
            final_answer=answer,
            attempts=[_attempt()],
            fallback_reason="judge_verdict_unavailable",
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_deterministic_from_verdict_budget_rejects_attempts():
    answer = _final_answer(
        status="deterministic_from_verdict", editor_model=None, based_on_verdict_id="v1", judge_confidence=0.4
    )
    with pytest.raises(ValidationError, match="budget_exhausted_before_editor"):
        EditorResult(
            final_answer=answer,
            attempts=[_attempt()],
            fallback_reason="budget_exhausted_before_editor",
            editor_provider="anthropic",
            cumulative_budget_exceeded=True,
        )


def test_editor_result_deterministic_from_verdict_transport_requires_attempts():
    answer = _final_answer(
        status="deterministic_from_verdict", editor_model=None, based_on_verdict_id="v1", judge_confidence=0.4
    )
    with pytest.raises(ValidationError, match="editor_transport_failed"):
        EditorResult(
            final_answer=answer,
            attempts=[],
            fallback_reason="editor_transport_failed",
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_attempt_provider_mismatch_rejected():
    with pytest.raises(ValidationError, match="diverge"):
        EditorResult(
            final_answer=_final_answer(editor_model="claude-test"),
            attempts=[_attempt(provider="openai")],
            fallback_reason=None,
            editor_provider="anthropic",
            cumulative_budget_exceeded=False,
        )


def test_editor_result_is_frozen():
    result = EditorResult(
        final_answer=FinalAnswer(answer_text="x", status="deterministic_no_verdict"),
        attempts=[],
        fallback_reason="judge_verdict_unavailable",
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    with pytest.raises(ValidationError):
        result.cumulative_budget_exceeded = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EditorResult.editor_input_tokens/editor_output_tokens/editor_cost_usd
# (patch pós-Etapa-7, autorizado na Etapa 8 — espelha JudgeResult)
# ---------------------------------------------------------------------------


def test_editor_result_totals_are_zero_with_no_attempts():
    result = EditorResult(
        final_answer=FinalAnswer(answer_text="x", status="deterministic_no_verdict"),
        attempts=[],
        fallback_reason="judge_verdict_unavailable",
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.editor_input_tokens == 0
    assert result.editor_output_tokens == 0
    assert result.editor_cost_usd == 0.0


def test_editor_result_totals_sum_multiple_attempts():
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
    result = EditorResult(
        final_answer=_final_answer(),
        attempts=attempts,
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    assert result.editor_input_tokens == 150
    assert result.editor_output_tokens == 30
    assert result.editor_cost_usd == pytest.approx(0.03)


def test_editor_result_totals_handle_attempt_with_no_usage():
    """attempt com transport_status='error' tem usage=None — não deve
    quebrar a soma, só contribuir 0."""
    error_attempt = EditorAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-test",
        model="claude-test",
        transport_status="error",
        transport_error=ProviderErrorInfo(
            type=ProviderErrorType.TIMEOUT, message="x", retryable=True
        ),
        transport_attempts=2,
        raw_output_text=None,
        parse_status="not_attempted",
        usage=None,
        cost_usd=None,
        latency_ms=30,
    )
    result = EditorResult(
        final_answer=FinalAnswer(answer_text="x", status="deterministic_no_verdict"),
        attempts=[],  # o próprio validator de coerência não aceitaria isso com
        # status=deterministic_no_verdict; testamos a soma isoladamente:
        fallback_reason="judge_verdict_unavailable",
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    # soma isolada sobre uma lista com attempt sem usage, direto na fórmula:
    from app.orchestrator.budget import sum_usage_and_cost

    manual_input, manual_output, manual_cost, manual_has_unknown = sum_usage_and_cost(
        [error_attempt]
    )
    assert manual_input == 0
    assert manual_output == 0
    assert manual_cost == 0.0
    assert manual_has_unknown is True  # cost_usd=None -> desconhecido, não zero
    # e confirma que o computed_field do EditorResult vazio também dá 0
    assert result.editor_input_tokens == 0


def test_editor_result_totals_appear_in_model_dump():
    result = EditorResult(
        final_answer=_final_answer(),
        attempts=[_attempt()],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    dump = result.model_dump()
    assert "editor_input_tokens" in dump
    assert "editor_output_tokens" in dump
    assert "editor_cost_usd" in dump
    assert dump["editor_input_tokens"] == result.editor_input_tokens


# ---------------------------------------------------------------------------
# Superfície pública (Etapa 17B) — llm_planned precisa ser aceito e
# exposto pelo schema de audit/API, não só pelo domínio interno.
# ---------------------------------------------------------------------------


def test_final_answer_public_accepts_llm_planned_status():
    from app.presentation.schemas import FinalAnswerPublic

    public = FinalAnswerPublic(
        answer_text="resposta",
        limitations=[],
        status="llm_planned",
        editor_model="claude-test",
        judge_confidence=0.7,
    )
    assert public.status == "llm_planned"


def test_final_answer_public_still_accepts_historical_llm_composed_status():
    from app.presentation.schemas import FinalAnswerPublic

    public = FinalAnswerPublic(
        answer_text="resposta histórica",
        limitations=[],
        status="llm_composed",
        editor_model="claude-legacy",
        judge_confidence=0.6,
    )
    assert public.status == "llm_composed"
