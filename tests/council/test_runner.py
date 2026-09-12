from __future__ import annotations

import pytest

from app.council.runner import CouncilRunner
from app.orchestrator.errors import InsufficientQuorumError
from app.source_analysis.result import SourceAnalysisResult
from tests.council.fakes import FakeDebateEngine, FakeEditor, FakeJudge, FakeSourceAnalyzer
from tests.council.fixtures import (
    debate_result,
    editor_result,
    final_answer,
    judge_result,
    model_response,
    raw_claim,
    round_result,
    run_config,
    verdict,
)


def _happy_path_fixtures():
    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    jr = judge_result(None, verdict_unavailable_reason="no_claims_to_judge")
    er = editor_result()
    return dr, jr, er


@pytest.mark.asyncio
async def test_happy_path_calls_in_order_with_correct_arguments():
    dr, jr, er = _happy_path_fixtures()
    debate_engine = FakeDebateEngine(result=dr)
    judge = FakeJudge(result=jr)
    editor = FakeEditor(result=er)
    runner = CouncilRunner(debate_engine=debate_engine, judge=judge, editor=editor, source_analyzer=FakeSourceAnalyzer(result=None))
    rc = run_config()

    result = await runner.run(rc)

    assert debate_engine.calls == [rc]
    assert judge.calls == [(dr, rc)]
    assert editor.calls == [(dr, jr, rc)]

    assert debate_engine.calls[0] is rc
    assert judge.calls[0][1] is rc
    assert editor.calls[0][2] is rc

    assert result.debate_result is dr
    assert result.judge_result is jr
    assert result.editor_result is er
    assert result.run_config is rc


@pytest.mark.asyncio
async def test_source_analysis_result_is_passed_to_editor_compose():
    """Patch de apresentação com fonte -- CouncilRunner precisa repassar
    o `SourceAnalysisResult` real pro Editor (pro renderizador
    determinístico poder anexar a relação com a fonte), nunca só pros 3
    números de accounting como antes."""
    dr, jr, er = _happy_path_fixtures()
    sa = SourceAnalysisResult(
        attempts=[],
        claim_results=[],
        skipped_reason="no_claims_to_analyze",
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    editor = FakeEditor(result=er)
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=editor,
        source_analyzer=FakeSourceAnalyzer(result=sa),
    )

    result = await runner.run(run_config())

    assert editor.source_analysis_result_calls == [sa]
    assert result.source_analysis_result is sa


@pytest.mark.asyncio
async def test_final_answer_accessible_via_result():
    dr, jr, er = _happy_path_fixtures()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=FakeEditor(result=er),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    result = await runner.run(run_config())
    assert result.final_answer.answer_text == er.final_answer.answer_text


@pytest.mark.asyncio
async def test_judge_without_verdict_still_calls_editor_and_completes():
    dr, jr, er = _happy_path_fixtures()
    assert jr.verdict is None
    editor = FakeEditor(result=er)
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=editor,
        source_analyzer=FakeSourceAnalyzer(result=None),
    )

    result = await runner.run(run_config())

    assert len(editor.calls) == 1
    assert result.judge_result.verdict is None
    assert result.editor_result is er


@pytest.mark.asyncio
async def test_editor_deterministic_no_verdict_produces_complete_result():
    dr, jr, er = _happy_path_fixtures()
    assert er.final_answer.status == "deterministic_no_verdict"
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=FakeEditor(result=er),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    result = await runner.run(run_config())
    assert result.status == "deterministic_no_verdict"


@pytest.mark.asyncio
async def test_editor_deterministic_from_verdict_produces_complete_result():
    v = verdict([])
    jr = judge_result(v)
    fa = final_answer(
        status="deterministic_from_verdict",
        based_on_verdict_id=v.id,
        judge_confidence=v.confidence,
    )
    er = editor_result(final_answer=fa, fallback_reason="budget_exhausted_before_editor")

    dr, _, _ = _happy_path_fixtures()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=FakeEditor(result=er),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    result = await runner.run(run_config())
    assert result.status == "deterministic_from_verdict"


@pytest.mark.asyncio
async def test_insufficient_quorum_propagates_and_stops_pipeline():
    rr = round_result([model_response("openai", status="error")])
    exc = InsufficientQuorumError(
        successful_count=0, total_providers=3, min_to_return=1, round_result=rr
    )
    debate_engine = FakeDebateEngine(exc=exc)
    judge = FakeJudge(result=None)
    editor = FakeEditor(result=None)
    runner = CouncilRunner(debate_engine=debate_engine, judge=judge, editor=editor, source_analyzer=FakeSourceAnalyzer(result=None))

    with pytest.raises(InsufficientQuorumError) as exc_info:
        await runner.run(run_config())

    assert exc_info.value.round_result is rr
    assert judge.calls == []
    assert editor.calls == []


@pytest.mark.asyncio
async def test_judge_value_error_propagates_and_editor_never_called():
    dr, _, _ = _happy_path_fixtures()
    debate_engine = FakeDebateEngine(result=dr)
    judge = FakeJudge(exc=ValueError("judge_provider desconhecido: 'x'"))
    editor = FakeEditor(result=None)
    runner = CouncilRunner(debate_engine=debate_engine, judge=judge, editor=editor, source_analyzer=FakeSourceAnalyzer(result=None))

    with pytest.raises(ValueError, match="judge_provider desconhecido"):
        await runner.run(run_config())

    assert editor.calls == []


@pytest.mark.asyncio
async def test_editor_value_error_propagates():
    dr, jr, _ = _happy_path_fixtures()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=FakeEditor(exc=ValueError("editor_provider desconhecido: 'y'")),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )

    with pytest.raises(ValueError, match="editor_provider desconhecido"):
        await runner.run(run_config())


@pytest.mark.asyncio
async def test_unexpected_error_propagates_intact():
    class WeirdBug(RuntimeError):
        pass

    debate_engine = FakeDebateEngine(exc=WeirdBug("bug real de programação"))
    runner = CouncilRunner(
        debate_engine=debate_engine, judge=FakeJudge(result=None), editor=FakeEditor(result=None),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )

    with pytest.raises(WeirdBug, match="bug real de programação"):
        await runner.run(run_config())


@pytest.mark.asyncio
async def test_works_with_fake_judge_strategy_not_single_judge():
    from app.judge.single_judge import SingleJudge

    dr, jr, er = _happy_path_fixtures()
    judge = FakeJudge(result=jr)
    assert not isinstance(judge, SingleJudge)

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr), judge=judge, editor=FakeEditor(result=er),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    result = await runner.run(run_config())
    assert result.judge_result is jr


def test_council_runner_has_no_from_providers():
    assert not hasattr(CouncilRunner, "from_providers")


def test_council_runner_does_not_import_single_judge():
    import inspect

    import app.council.runner as runner_module

    source = inspect.getsource(runner_module)
    assert "from app.judge.single_judge import SingleJudge" not in source


def test_council_runner_does_not_import_budget_helpers():
    import inspect

    import app.council.runner as runner_module

    source = inspect.getsource(runner_module)
    assert "compute_budget_exceeded" not in source
    assert "sum_usage_and_cost" not in source


@pytest.mark.asyncio
async def test_cumulative_budget_exceeded_is_pure_passthrough():
    dr, jr, _ = _happy_path_fixtures()
    er = editor_result(cumulative_budget_exceeded=True)
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=FakeEditor(result=er),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    result = await runner.run(run_config())
    assert result.cumulative_budget_exceeded is True
