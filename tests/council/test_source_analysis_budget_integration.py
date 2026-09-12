"""
Testes de integração — patch de revisão do Stage 16 (blocker de accounting).

Provam, via `CouncilRunner` REAL (SourceAnalyzer + SingleJudge + Editor
reais, só o DebateEngine é fake pra controlar precisamente o cumulative_*
de entrada) que Source Analysis passa a contar pro budget de Judge/Editor
-- e que Judge/Editor continuam epistemicamente cegos ao seu conteúdo.

Truque deliberado: `ScriptedProvider` do Judge/Editor é construído com
`responses=[]` (lista vazia) quando o teste espera ZERO chamadas -- se o
bug reaparecer e o Judge/Editor tentar chamar `.complete()` mesmo assim,
o próprio `ScriptedProvider` levanta `AssertionError` internamente
("esgotou as respostas roteirizadas"), then o teste falha de forma
inequívoca, não silenciosamente.
"""

from __future__ import annotations

import json

import pytest

from app.council.runner import CouncilRunner
from app.debate.result import DebateResult
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.models.provider_models import TokenUsage
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.source_analysis.analyzer import SourceAnalyzer
from tests.council.fakes import FakeDebateEngine
from tests.debate.fakes import ScriptedProvider, text_response
from tests.judge.fixtures import debate_result, model_response, raw_claim
from tests.judge.fixtures import initial_result as _initial_result_fixture

_SOURCE = "Texto de fonte qualquer, suficiente para uma análise real."


def _debate_result_with_known_cost(
    claims: list, responses: list, total_cost_usd: float
) -> DebateResult:
    """`tests.judge.fixtures.initial_result()` fixa `total_cost_usd=0.0`
    por padrão (não deriva de `ModelResponse.cost_usd` automaticamente --
    só `total_input_tokens`/`total_output_tokens` são somados de verdade
    na fixture) -- pra testes de accounting precisos, construímos o
    `InitialResponsesResult` passando `total_cost_usd` explicitamente."""
    ir = _initial_result_fixture(responses, total_cost_usd=total_cost_usd)
    return DebateResult(
        initial_result=ir,
        critique_round=None,
        claims=claims,
        claim_processing_attempts=[],
        claim_processor_provider="anthropic",
        debate_skipped_reason="insufficient_initial_quorum",
        cumulative_budget_exceeded=False,
    )


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="pergunta",
        enabled_providers=["openai"],
        max_cost_usd=1.00,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        overall_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
        source_text=_SOURCE,
    )
    fields.update(overrides)
    return RunConfig(**fields)


def _empty_source_payload(cost_usd: float, input_tokens: int = 100, output_tokens: int = 50):
    return text_response(
        "anthropic",
        json.dumps({"claim_relations": []}),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


# ---------------------------------------------------------------------------
# A/B — Source Analysis empurra custo/tokens acima do limite -> Judge zero chamadas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_source_analysis_pushes_cost_over_limit_judge_makes_zero_calls():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.90)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.90)
    assert dr.cumulative_cost_usd == pytest.approx(0.90)

    sa_provider = ScriptedProvider("anthropic", [_empty_source_payload(cost_usd=0.20)])
    judge_provider = ScriptedProvider("anthropic", [])  # nunca deve ser chamado
    editor_provider = ScriptedProvider("anthropic", [])  # idem

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    result = await runner.run(_run_config(max_cost_usd=1.00))

    assert judge_provider.received_requests == []
    assert editor_provider.received_requests == []
    assert result.judge_result.verdict_unavailable_reason == "budget_exhausted_before_judge"
    assert result.judge_result.cumulative_budget_exceeded is True


@pytest.mark.asyncio
async def test_b_source_analysis_pushes_tokens_over_limit_judge_makes_zero_calls():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=900, output_tokens=0))
    dr = debate_result([c1], [mr])

    sa_provider = ScriptedProvider(
        "anthropic",
        [_empty_source_payload(cost_usd=0.001, input_tokens=200, output_tokens=0)],
    )
    judge_provider = ScriptedProvider("anthropic", [])
    editor_provider = ScriptedProvider("anthropic", [])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    # max_total_tokens=1000 -- debate(900) + source_analysis(200) = 1100 > 1000
    result = await runner.run(_run_config(max_cost_usd=1000.0, max_total_tokens=1000))

    assert judge_provider.received_requests == []
    assert result.judge_result.verdict_unavailable_reason == "budget_exhausted_before_judge"


# ---------------------------------------------------------------------------
# C — Source Analysis dentro do orçamento -> Judge roda normalmente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_source_analysis_within_budget_judge_runs_normally():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.10)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.10)

    sa_provider = ScriptedProvider("anthropic", [_empty_source_payload(cost_usd=0.05)])
    judge_payload = json.dumps(
        {
            "claim_assessments": [{"claim_id": c1.id, "verdict": "supported", "explanation": "ok"}],
            "confidence": 0.5,
            "reasoning": "justificativa",
        }
    )
    judge_provider = ScriptedProvider("anthropic", [text_response("anthropic", judge_payload)])
    # Etapa 17B: EditorOutput (claim_narratives/synthesis_*) foi
    # substituído por EditorPlan (opening_style/closing_style) -- ver
    # app/editor/schemas.py.
    editor_payload = json.dumps({"opening_style": "direct", "closing_style": "concise"})
    editor_provider = ScriptedProvider("anthropic", [text_response("anthropic", editor_payload)])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    result = await runner.run(_run_config(max_cost_usd=1.00))

    assert len(judge_provider.received_requests) == 1  # Judge RODOU
    assert len(editor_provider.received_requests) == 1  # Editor RODOU normalmente
    assert result.judge_result.verdict is not None
    assert result.judge_result.verdict_unavailable_reason is None


# ---------------------------------------------------------------------------
# D — Attempt de Source Analysis FALHO (malformado) com custo conhecido
# ainda conta pro budget subsequente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_failed_source_analysis_attempt_with_known_cost_still_counts():
    """Ajustado pra Etapa 17A (B2): os números agora deixam as DUAS
    tentativas de análise de fonte acontecerem legitimamente (budget
    ainda não esgotado entre elas), e só DEPOIS das duas o Judge fica
    bloqueado -- prova o ponto original (custo de tentativa falha conta
    pro budget seguinte) sem colidir com o novo gate de retry (que tem
    seu próprio teste dedicado)."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.50)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.50)

    # resposta malformada (não é JSON válido) mas com custo/uso REAIS --
    # a chamada aconteceu e consumiu recurso, mesmo tendo falhado.
    malformed = text_response("anthropic", "isto não é JSON", cost_usd=0.20)
    sa_provider = ScriptedProvider("anthropic", [malformed, malformed])  # retry consome 2x
    judge_provider = ScriptedProvider("anthropic", [])
    editor_provider = ScriptedProvider("anthropic", [])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    # 0.50 (debate) + 0.20 (tentativa 1) = 0.70 < 0.85 -- retry AUTORIZADO.
    # 0.50 + 0.20 + 0.20 = 0.90 >= 0.85 -- Judge bloqueado DEPOIS das duas.
    result = await runner.run(_run_config(max_cost_usd=0.85))

    assert result.source_analysis_result.skipped_reason == "source_analysis_output_invalid"
    assert result.source_analysis_result.source_analysis_cost_usd == pytest.approx(0.40)  # 2 tentativas
    assert len(sa_provider.received_requests) == 2  # as duas tentativas legítimas aconteceram
    assert judge_provider.received_requests == []
    assert result.judge_result.verdict_unavailable_reason == "budget_exhausted_before_judge"


@pytest.mark.asyncio
async def test_d2_retry_blocked_when_first_attempt_alone_exhausts_budget():
    """Etapa 17A (B2), caso específico do retry bloqueado: a 1ª tentativa
    sozinha já esgota o budget -- a 2ª tentativa (retry) nunca acontece.
    Contrato aprovado: reason == '*_output_invalid' AND
    len(attempts) < _MAX_STRUCTURED_OUTPUT_ATTEMPTS => retry bloqueado
    por budget, nunca as duas tentativas esgotadas normalmente."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.90)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.90)

    malformed = text_response("anthropic", "isto não é JSON", cost_usd=0.20)
    sa_provider = ScriptedProvider("anthropic", [malformed])  # só 1 resposta roteirizada --
    # se o retry fosse tentado (bug), o teste falharia com AssertionError
    # interna do ScriptedProvider por esgotar o roteiro.
    judge_provider = ScriptedProvider("anthropic", [])
    editor_provider = ScriptedProvider("anthropic", [])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    # 0.90 (debate) + 0.20 (tentativa 1) = 1.10 >= 1.00 -- retry bloqueado.
    result = await runner.run(_run_config(max_cost_usd=1.00))

    assert len(sa_provider.received_requests) == 1  # só a 1ª tentativa
    assert len(result.source_analysis_result.attempts) == 1
    assert result.source_analysis_result.skipped_reason == "source_analysis_output_invalid"
    # contrato aprovado: essa combinação só é possível via bloqueio de budget
    assert len(result.source_analysis_result.attempts) < 2


# ---------------------------------------------------------------------------
# E — Source Analysis + Judge juntos empurram o Editor pra zero chamadas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_source_analysis_plus_judge_push_editor_over_budget():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.50)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.50)

    sa_provider = ScriptedProvider("anthropic", [_empty_source_payload(cost_usd=0.30)])
    judge_payload = json.dumps(
        {
            "claim_assessments": [{"claim_id": c1.id, "verdict": "supported", "explanation": "ok"}],
            "confidence": 0.5,
            "reasoning": "justificativa",
        }
    )
    judge_provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", judge_payload, cost_usd=0.15)]
    )
    editor_provider = ScriptedProvider("anthropic", [])  # nunca deve ser chamado

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    # 0.50 (debate) + 0.30 (source) + 0.15 (judge) = 0.95 < 1.00 -- Judge RODA
    # mas o total já deixa pouca margem; vamos fechar o cerco no editor:
    result = await runner.run(_run_config(max_cost_usd=0.90))

    assert len(judge_provider.received_requests) == 1  # Judge rodou (0.80 <= 0.90)
    assert editor_provider.received_requests == []  # Editor não -- 0.95 > 0.90
    assert result.editor_result.fallback_reason == "budget_exhausted_before_editor"
    assert result.editor_result.cumulative_budget_exceeded is True


# ---------------------------------------------------------------------------
# F/K — contabilizado exatamente uma vez, totais agregados batem exato
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_k_source_analysis_counted_exactly_once_in_aggregate_totals():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.01)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.01)

    sa_provider = ScriptedProvider(
        "anthropic", [_empty_source_payload(cost_usd=0.02, input_tokens=7, output_tokens=3)]
    )
    judge_payload = json.dumps(
        {
            "claim_assessments": [{"claim_id": c1.id, "verdict": "supported", "explanation": "ok"}],
            "confidence": 0.5,
            "reasoning": "justificativa",
        }
    )
    judge_provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", judge_payload, input_tokens=4, output_tokens=2, cost_usd=0.03)]
    )
    # Etapa 17B: EditorOutput (claim_narratives/synthesis_*) foi
    # substituído por EditorPlan (opening_style/closing_style) -- ver
    # app/editor/schemas.py.
    editor_payload = json.dumps({"opening_style": "direct", "closing_style": "concise"})
    editor_provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", editor_payload, input_tokens=5, output_tokens=1, cost_usd=0.04)]
    )

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    result = await runner.run(_run_config(max_cost_usd=100.0, max_total_tokens=1_000_000))

    expected_input = dr.cumulative_input_tokens + 7 + 4 + 5
    expected_output = dr.cumulative_output_tokens + 3 + 2 + 1
    expected_cost = pytest.approx(dr.cumulative_cost_usd + 0.02 + 0.03 + 0.04)
    assert result.total_input_tokens == expected_input
    assert result.total_output_tokens == expected_output
    assert result.total_cost_usd == expected_cost


# ---------------------------------------------------------------------------
# G — CouncilRunResult.cumulative_budget_exceeded reflete o estouro
# causado só por Source Analysis, mesmo sem chamada posterior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g_top_level_cumulative_budget_exceeded_true_from_source_analysis_alone():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.90)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.90)

    sa_provider = ScriptedProvider("anthropic", [_empty_source_payload(cost_usd=0.20)])
    judge_provider = ScriptedProvider("anthropic", [])
    editor_provider = ScriptedProvider("anthropic", [])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    result = await runner.run(_run_config(max_cost_usd=1.00))

    assert result.cumulative_budget_exceeded is True


# ---------------------------------------------------------------------------
# H — sem fonte fornecida, comportamento pré-Etapa-16 inalterado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h_no_source_supplied_preserves_pre_stage16_behavior():
    c1 = raw_claim("A", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.10)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.10)

    sa_provider = ScriptedProvider("anthropic", [])  # nunca deve ser chamado
    judge_payload = json.dumps(
        {
            "claim_assessments": [{"claim_id": c1.id, "verdict": "supported", "explanation": "ok"}],
            "confidence": 0.5,
            "reasoning": "justificativa",
        }
    )
    judge_provider = ScriptedProvider("anthropic", [text_response("anthropic", judge_payload)])
    # Etapa 17B: EditorOutput (claim_narratives/synthesis_*) foi
    # substituído por EditorPlan (opening_style/closing_style) -- ver
    # app/editor/schemas.py.
    editor_payload = json.dumps({"opening_style": "direct", "closing_style": "concise"})
    editor_provider = ScriptedProvider("anthropic", [text_response("anthropic", editor_payload)])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )

    result = await runner.run(_run_config(source_text=None, max_cost_usd=1.00))

    assert result.source_analysis_result is None
    assert sa_provider.received_requests == []
    assert len(judge_provider.received_requests) == 1
    assert result.judge_result.verdict is not None


# ---------------------------------------------------------------------------
# I/J — Judge/Editor permanecem cegos ao conteúdo de Source Analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_j_judge_and_editor_requests_never_contain_source_content():
    c1 = raw_claim("A receita cresceu.", "resp-1", provider="openai")
    mr = model_response("openai", usage=TokenUsage(input_tokens=10, output_tokens=5), cost_usd=0.01)
    dr = _debate_result_with_known_cost([c1], [mr], total_cost_usd=0.01)

    marker = "TRECHO-SECRETO-DA-FONTE-QUE-NUNCA-DEVE-VAZAR"
    sa_payload = json.dumps(
        {"claim_relations": [{"claim_id": c1.id, "relation": "supports", "excerpt": marker}]}
    )
    sa_provider = ScriptedProvider("anthropic", [text_response("anthropic", sa_payload, cost_usd=0.01)])
    judge_payload = json.dumps(
        {
            "claim_assessments": [{"claim_id": c1.id, "verdict": "supported", "explanation": "ok"}],
            "confidence": 0.5,
            "reasoning": "justificativa",
        }
    )
    judge_provider = ScriptedProvider("anthropic", [text_response("anthropic", judge_payload, cost_usd=0.01)])
    # Etapa 17B: EditorOutput (claim_narratives/synthesis_*) foi
    # substituído por EditorPlan (opening_style/closing_style) -- ver
    # app/editor/schemas.py.
    editor_payload = json.dumps({"opening_style": "direct", "closing_style": "concise"})
    editor_provider = ScriptedProvider("anthropic", [text_response("anthropic", editor_payload, cost_usd=0.01)])

    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=dr),
        source_analyzer=SourceAnalyzer({"anthropic": sa_provider, "openai": sa_provider}),
        judge=SingleJudge({"anthropic": judge_provider}),
        editor=Editor({"anthropic": editor_provider}),
    )
    rc = RunConfig(
        question="pergunta",
        enabled_providers=["openai"],
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        overall_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
        source_text=marker,
    )

    await runner.run(rc)

    for req in judge_provider.received_requests:
        assert marker not in req.system_prompt
        assert marker not in req.messages[0].content
    for req in editor_provider.received_requests:
        assert marker not in req.system_prompt
        assert marker not in req.messages[0].content
