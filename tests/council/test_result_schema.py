from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.council.result import CouncilRunResult
from tests.council.fixtures import (
    debate_result,
    editor_result,
    final_answer,
    judge_result,
    model_response,
    raw_claim,
    run_config,
    verdict,
)


def _minimal_result(**overrides):
    from datetime import datetime, timezone

    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    jr = judge_result(None, verdict_unavailable_reason="no_claims_to_judge")
    er = editor_result()
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    completed = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)

    fields = dict(
        run_config=run_config(),
        debate_result=dr,
        source_analysis_result=None,
        judge_result=jr,
        editor_result=er,
        started_at=started,
        completed_at=completed,
    )
    fields.update(overrides)
    return CouncilRunResult(**fields)


def test_is_frozen():
    result = _minimal_result()
    with pytest.raises(ValidationError):
        result.id = "outro-id"  # type: ignore[misc]


def test_extra_forbid():
    with pytest.raises(ValidationError):
        _minimal_result(campo_que_nao_existe="x")  # type: ignore[call-arg]


def test_id_has_default():
    result = _minimal_result()
    assert result.id
    assert isinstance(result.id, str)


def test_started_at_le_completed_at():
    result = _minimal_result()
    assert result.started_at <= result.completed_at


def test_final_answer_is_same_instance_as_editor_result():
    result = _minimal_result()
    assert result.final_answer is result.editor_result.final_answer


def test_status_matches_editor_result_final_answer_status():
    result = _minimal_result()
    assert result.status == result.editor_result.final_answer.status


def test_cumulative_budget_exceeded_matches_editor_result():
    er = editor_result(cumulative_budget_exceeded=True)
    result = _minimal_result(editor_result=er)
    assert result.cumulative_budget_exceeded is True
    assert result.cumulative_budget_exceeded == result.editor_result.cumulative_budget_exceeded


def test_model_dump_contains_stored_and_total_fields():
    result = _minimal_result()
    dump = result.model_dump()
    assert "id" in dump
    assert "run_config" in dump
    assert "debate_result" in dump
    assert "judge_result" in dump
    assert "editor_result" in dump
    assert "started_at" in dump
    assert "completed_at" in dump
    assert "total_input_tokens" in dump
    assert "total_output_tokens" in dump
    assert "total_cost_usd" in dump


def test_model_dump_does_not_contain_convenience_properties():
    result = _minimal_result()
    dump = result.model_dump()
    assert "final_answer" not in dump
    assert "status" not in dump
    assert "cumulative_budget_exceeded" not in dump


def test_model_dump_json_does_not_contain_convenience_properties():
    import json

    result = _minimal_result()
    dump = json.loads(result.model_dump_json())
    assert "final_answer" not in dump
    assert "status" not in dump
    assert "cumulative_budget_exceeded" not in dump
    assert "total_input_tokens" in dump


def test_totals_appear_exactly_once_in_dump():
    result = _minimal_result()
    dump = result.model_dump()
    assert list(dump.keys()).count("total_input_tokens") == 1


def test_totals_sum_three_non_overlapping_sources():
    from app.editor.attempt import EditorAttempt
    from app.judge.attempt import JudgeAttempt
    from app.models.provider_models import TokenUsage

    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result(
        [c1], [model_response("openai", usage=TokenUsage(input_tokens=1000, output_tokens=100))]
    )

    v = verdict([], confidence=0.5)
    j_attempt = JudgeAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-test",
        model="claude-test",
        transport_status="success",
        raw_output_text='{"claim_assessments": []}',
        parse_status="accepted",
        usage=TokenUsage(input_tokens=200, output_tokens=20),
        latency_ms=10,
        transport_attempts=1,
    )
    jr = judge_result(v, attempts=[j_attempt])

    e_attempt = EditorAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-test",
        model="claude-test",
        transport_status="success",
        raw_output_text='{"claim_narratives": [], "synthesis_intro": "", "synthesis_conclusion": ""}',
        parse_status="accepted",
        usage=TokenUsage(input_tokens=50, output_tokens=5),
        latency_ms=10,
        transport_attempts=1,
    )
    fa = final_answer(
        status="llm_composed",
        editor_model="claude-test",
        based_on_verdict_id=v.id,
        judge_confidence=0.5,
    )
    er = editor_result(final_answer=fa, attempts=[e_attempt], fallback_reason=None)

    result = _minimal_result(debate_result=dr, judge_result=jr, editor_result=er)

    assert result.total_input_tokens == 1000 + 200 + 50
    assert result.total_output_tokens == 100 + 20 + 5


# ---------------------------------------------------------------------------
# has_unknown_accounting_components — propagação sem dupla contagem entre estágios
# ---------------------------------------------------------------------------


def test_has_unknown_accounting_components_false_when_all_stages_known():
    from app.editor.attempt import EditorAttempt
    from app.judge.attempt import JudgeAttempt
    from app.models.provider_models import TokenUsage

    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result(
        [c1], [model_response("openai", usage=TokenUsage(input_tokens=100, output_tokens=10), cost_usd=0.01)]
    )
    v = verdict([], confidence=0.5)
    j_attempt = JudgeAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_assessments": []}', parse_status="accepted",
        usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.001, latency_ms=10, transport_attempts=1,
    )
    jr = judge_result(v, attempts=[j_attempt])
    e_attempt = EditorAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_narratives": [], "synthesis_intro": "", "synthesis_conclusion": ""}',
        parse_status="accepted", usage=TokenUsage(input_tokens=5, output_tokens=2), cost_usd=0.0005,
        latency_ms=10, transport_attempts=1,
    )
    fa = final_answer(status="llm_composed", editor_model="claude-test", based_on_verdict_id=v.id, judge_confidence=0.5)
    er = editor_result(final_answer=fa, attempts=[e_attempt], fallback_reason=None)

    result = _minimal_result(debate_result=dr, judge_result=jr, editor_result=er)
    assert result.has_unknown_accounting_components is False


def test_has_unknown_accounting_components_true_when_only_debate_stage_is_unknown():
    from app.editor.attempt import EditorAttempt
    from app.judge.attempt import JudgeAttempt
    from app.models.provider_models import TokenUsage

    c1 = raw_claim("A", "resp-1", provider="openai")
    # debate: cost_usd=None -> desconhecido
    dr = debate_result([c1], [model_response("openai", usage=TokenUsage(input_tokens=100, output_tokens=10), cost_usd=None)])
    v = verdict([], confidence=0.5)
    j_attempt = JudgeAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_assessments": []}', parse_status="accepted",
        usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.001, latency_ms=10, transport_attempts=1,
    )
    jr = judge_result(v, attempts=[j_attempt])
    e_attempt = EditorAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_narratives": [], "synthesis_intro": "", "synthesis_conclusion": ""}',
        parse_status="accepted", usage=TokenUsage(input_tokens=5, output_tokens=2), cost_usd=0.0005,
        latency_ms=10, transport_attempts=1,
    )
    fa = final_answer(status="llm_composed", editor_model="claude-test", based_on_verdict_id=v.id, judge_confidence=0.5)
    er = editor_result(final_answer=fa, attempts=[e_attempt], fallback_reason=None)

    result = _minimal_result(debate_result=dr, judge_result=jr, editor_result=er)
    assert result.has_unknown_accounting_components is True


def test_has_unknown_accounting_components_true_when_only_judge_stage_is_unknown():
    from app.editor.attempt import EditorAttempt
    from app.judge.attempt import JudgeAttempt
    from app.models.provider_models import ProviderErrorInfo, ProviderErrorType, TokenUsage

    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai", usage=TokenUsage(input_tokens=100, output_tokens=10), cost_usd=0.01)])
    v = verdict([], confidence=0.5)
    # tentativa de Judge rejeitada por transporte -> cost_usd=None
    j_attempt = JudgeAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="error",
        transport_error=ProviderErrorInfo(type=ProviderErrorType.TIMEOUT, message="x", retryable=True),
        transport_attempts=2, raw_output_text=None, parse_status="not_attempted",
        usage=None, cost_usd=None, latency_ms=10,
    )
    jr = judge_result(v, attempts=[j_attempt])
    e_attempt = EditorAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_narratives": [], "synthesis_intro": "", "synthesis_conclusion": ""}',
        parse_status="accepted", usage=TokenUsage(input_tokens=5, output_tokens=2), cost_usd=0.0005,
        latency_ms=10, transport_attempts=1,
    )
    fa = final_answer(status="llm_composed", editor_model="claude-test", based_on_verdict_id=v.id, judge_confidence=0.5)
    er = editor_result(final_answer=fa, attempts=[e_attempt], fallback_reason=None)

    result = _minimal_result(debate_result=dr, judge_result=jr, editor_result=er)
    assert result.has_unknown_accounting_components is True
    # confirma que NÃO houve dupla contagem: debate e editor continuam conhecidos
    assert result.debate_result.has_unknown_accounting_components is False
    assert result.editor_result.has_unknown_accounting_components is False


def test_has_unknown_accounting_components_true_when_only_editor_stage_is_unknown():
    from app.editor.attempt import EditorAttempt
    from app.judge.attempt import JudgeAttempt
    from app.models.provider_models import TokenUsage

    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai", usage=TokenUsage(input_tokens=100, output_tokens=10), cost_usd=0.01)])
    v = verdict([], confidence=0.5)
    j_attempt = JudgeAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_assessments": []}', parse_status="accepted",
        usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.001, latency_ms=10, transport_attempts=1,
    )
    jr = judge_result(v, attempts=[j_attempt])
    # Editor com usage conhecido mas SEM preço registrado -> cost_usd=None
    e_attempt = EditorAttempt(
        attempt_number=1, provider="anthropic", requested_model="claude-test", model="claude-test", transport_status="success",
        raw_output_text='{"claim_narratives": [], "synthesis_intro": "", "synthesis_conclusion": ""}',
        parse_status="accepted", usage=TokenUsage(input_tokens=5, output_tokens=2), cost_usd=None,
        latency_ms=10, transport_attempts=1,
    )
    fa = final_answer(status="llm_composed", editor_model="claude-test", based_on_verdict_id=v.id, judge_confidence=0.5)
    er = editor_result(final_answer=fa, attempts=[e_attempt], fallback_reason=None)

    result = _minimal_result(debate_result=dr, judge_result=jr, editor_result=er)
    assert result.has_unknown_accounting_components is True


def test_has_unknown_accounting_components_false_with_no_calls_at_all_stages():
    """attempts=[] em Judge e Editor, debate sem responses com cost_usd=None
    -> nenhum estágio contribui unknown, tudo conhecido-zero."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai", cost_usd=0.0)])
    jr = judge_result(None, verdict_unavailable_reason="no_claims_to_judge")
    er = editor_result()  # attempts=[]

    result = _minimal_result(debate_result=dr, judge_result=jr, editor_result=er)
    assert result.debate_result.has_unknown_accounting_components is False
    assert result.judge_result.has_unknown_accounting_components is False
    assert result.editor_result.has_unknown_accounting_components is False
    assert result.has_unknown_accounting_components is False


def test_has_unknown_accounting_components_appears_in_model_dump():
    result = _minimal_result()
    dump = result.model_dump()
    assert "has_unknown_accounting_components" in dump


def test_model_dump_does_not_contain_old_flag_names():
    """Confirma explicitamente que o rename foi completo — nenhum dos
    nomes antigos (cost_has_unknown_components e variantes com prefixo
    judge_/editor_) sobrevive em nenhum nível serializado."""
    result = _minimal_result()
    dump = result.model_dump()
    assert "cost_has_unknown_components" not in dump
    assert "judge_cost_has_unknown_components" not in dump
    assert "editor_cost_has_unknown_components" not in dump

    debate_dump = dump["debate_result"]
    assert "cost_has_unknown_components" not in debate_dump
    assert "has_unknown_accounting_components" in debate_dump

    judge_dump = dump["judge_result"]
    assert "judge_cost_has_unknown_components" not in judge_dump
    assert "has_unknown_accounting_components" in judge_dump

    editor_dump = dump["editor_result"]
    assert "editor_cost_has_unknown_components" not in editor_dump
    assert "has_unknown_accounting_components" in editor_dump
