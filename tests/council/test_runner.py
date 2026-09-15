from __future__ import annotations

import pytest

from app.council.runner import CouncilRunner
from app.models.domain import ClaimAssessment
from app.orchestrator.config import MAX_QUESTION_CHARACTERS
from app.orchestrator.errors import InsufficientQuorumError
from app.reconciliation.models import ChannelRelationship, SourceChannelState
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
async def test_oversized_question_rejected_before_any_engine_call(monkeypatch):
    """Accepted Question Size Boundary V1, defesa em profundidade --
    `CouncilRunner` é diretamente construível/chamável (como aqui, sem
    passar por `CouncilExecutionService`) -- `question` >
    MAX_QUESTION_CHARACTERS precisa falhar ANTES de qualquer chamada de
    provider/engine, mesmo nesse caminho direto.

    Repair F2 (revisão focada) -- `validate_question` precisa rodar
    ANTES até de `_now()` ser avaliado (started_at nunca deveria ser
    capturado pra uma execução que nem vai existir): sentinela que
    levanta se `_now` for chamado, prova que o Runner nem chega a essa
    linha."""
    import app.council.runner as runner_module

    def _now_should_never_be_called():
        raise AssertionError("_now() nunca deveria ser chamado pra question inválida")

    monkeypatch.setattr(runner_module, "_now", _now_should_never_be_called)

    debate_engine = FakeDebateEngine(exc=AssertionError("nunca deveria ser chamado"))
    judge = FakeJudge(result=None)
    editor = FakeEditor(result=None)
    runner = CouncilRunner(
        debate_engine=debate_engine, judge=judge, editor=editor,
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    rc = run_config(question="x" * (MAX_QUESTION_CHARACTERS + 1))

    with pytest.raises(ValueError, match="máximo"):
        await runner.run(rc)

    assert debate_engine.calls == []
    assert judge.calls == []
    assert editor.calls == []


@pytest.mark.asyncio
async def test_blank_question_rejected_before_any_engine_call(monkeypatch):
    """Repair F2 (revisão focada) -- mesma prova de
    `test_oversized_question_rejected_before_any_engine_call` acima,
    pro lado blank do contrato: `_now()` nunca é chamado."""
    import app.council.runner as runner_module

    def _now_should_never_be_called():
        raise AssertionError("_now() nunca deveria ser chamado pra question inválida")

    monkeypatch.setattr(runner_module, "_now", _now_should_never_be_called)

    debate_engine = FakeDebateEngine(exc=AssertionError("nunca deveria ser chamado"))
    judge = FakeJudge(result=None)
    editor = FakeEditor(result=None)
    runner = CouncilRunner(
        debate_engine=debate_engine, judge=judge, editor=editor,
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    rc = run_config(question="   \n\t  ")

    with pytest.raises(ValueError, match="vazia"):
        await runner.run(rc)

    assert debate_engine.calls == []
    assert judge.calls == []
    assert editor.calls == []


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


# ---------------------------------------------------------------------------
# Cross-Channel Reconciliation V1 -- roda DEPOIS do Judge, ANTES do
# Editor; função PURA (zero chamadas de provider adicionais); Editor
# recebe o resultado explicitamente; accounting nunca é afetado.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_is_computed_and_passed_to_editor_compose():
    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr = judge_result(v)
    er = editor_result()
    editor = FakeEditor(result=er)
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=editor,
        source_analyzer=FakeSourceAnalyzer(result=None),
    )

    result = await runner.run(run_config())

    assert len(editor.reconciliation_calls) == 1
    reconciliation = editor.reconciliation_calls[0]
    assert reconciliation is not None
    assert reconciliation.status == "complete"
    assert reconciliation.claim_outcomes[0].claim_id == c1.id
    assert reconciliation.claim_outcomes[0].source_state == SourceChannelState.NOT_SUPPLIED
    assert reconciliation.claim_outcomes[0].channel_relationship == (
        ChannelRelationship.NOT_COMPARABLE
    )
    # O MESMO objeto de reconciliação chega ao CouncilRunResult -- nunca
    # recalculado uma segunda vez em outro lugar.
    assert result.reconciliation is reconciliation


@pytest.mark.asyncio
async def test_reconciliation_reflects_judge_unavailable_status():
    """Judge indisponível -- reconciliação ainda roda, produz
    status='judge_unavailable' deterministicamente, e o Editor a
    recebe (nunca None só porque o Judge falhou)."""
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

    reconciliation = editor.reconciliation_calls[0]
    assert reconciliation.status == "judge_unavailable"
    assert result.reconciliation.status == "judge_unavailable"


@pytest.mark.asyncio
async def test_reconciliation_causes_zero_extra_provider_calls():
    """Reconciliação é uma função PURA -- roda entre Judge e Editor sem
    tocar nenhum provider. Prova isso indiretamente: cada fake estágio
    (Debate/SourceAnalyzer/Judge) é chamado EXATAMENTE uma vez, exatamente
    como seria sem reconciliação nenhuma."""
    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr = judge_result(v)
    er = editor_result()
    debate_engine = FakeDebateEngine(result=dr)
    source_analyzer = FakeSourceAnalyzer(result=None)
    judge = FakeJudge(result=jr)
    editor = FakeEditor(result=er)
    runner = CouncilRunner(
        debate_engine=debate_engine, judge=judge, editor=editor, source_analyzer=source_analyzer
    )

    await runner.run(run_config())

    assert len(debate_engine.calls) == 1
    assert len(source_analyzer.calls) == 1
    assert len(judge.calls) == 1
    assert len(editor.calls) == 1


@pytest.mark.asyncio
async def test_accounting_totals_unaffected_by_reconciliation():
    """Reconciliação custa ZERO tokens/dólares -- os totais continuam
    sendo exatamente a soma dos 4 componentes reais (debate/source/judge/
    editor), nunca um quinto termo."""
    dr, jr, er = _happy_path_fixtures()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        judge=FakeJudge(result=jr),
        editor=FakeEditor(result=er),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )

    result = await runner.run(run_config())

    assert result.reconciliation is not None
    assert result.total_input_tokens == (
        dr.cumulative_input_tokens + jr.judge_input_tokens + er.editor_input_tokens
    )
    assert result.total_output_tokens == (
        dr.cumulative_output_tokens + jr.judge_output_tokens + er.editor_output_tokens
    )
    assert result.total_cost_usd == (
        dr.cumulative_cost_usd + jr.judge_cost_usd + er.editor_cost_usd
    )


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

    # `v` tem claim_assessments=[] -- combinado com um DebateResult SEM
    # claims correntes (nunca o `c1` de `_happy_path_fixtures()`, que
    # deixaria o veredito sem cobertura da única claim corrente e violaria
    # a garantia real de SingleJudge, que reconcile_source_and_judge
    # agora verifica -- ver app/reconciliation/reconcile.py).
    dr = debate_result([], [model_response("openai")])
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
