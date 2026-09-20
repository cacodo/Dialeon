"""
Testes de fronteira B1 (Etapa 17A) — usam um `AnthropicProvider` REAL
(não um `ProviderResponse` construído à mão) com `api_key=None`,
cruzando pra dentro de cada consumidor de stage real. Prova que o
contrato `attempts=0`/`cost_usd=0.0` do provider sobrevive intacto até
o Attempt final, sem `ValidationError`, e que o Run resultante continua
persistível — reprodução do crash real encontrado na investigação B1.
"""

from __future__ import annotations

import pytest

from app.debate.claim_extraction import extract_claims
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.pricing import PricingRegistry
from app.source_analysis.analyzer import SourceAnalyzer
from tests.council.fixtures import run_config as _base_run_config
from tests.editor.fixtures import judge_result as editor_judge_result_fixture
from tests.editor.fixtures import verdict as editor_verdict_fixture
from tests.judge.fixtures import debate_result, model_response, raw_claim


def _keyless_provider() -> AnthropicProvider:
    return AnthropicProvider(
        api_key=None,
        timeout_seconds=30,
        max_retries=2,
        default_model="claude-sonnet-5",
        pricing=PricingRegistry({}),
    )


@pytest.mark.asyncio
async def test_extraction_survives_missing_api_key():
    response = model_response("openai")
    claims, attempts, verifications = await extract_claims(
        response, round_number=1, total_models_in_round=3,
        extractor=_keyless_provider(), max_output_tokens_per_call=1024,
        run_config=_base_run_config(), prior_input_tokens=0,
        prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert claims == []
    assert verifications == []
    assert len(attempts) == 1
    assert attempts[0].transport_attempts == 0
    assert attempts[0].cost_usd == 0.0  # confirmado-zero, nunca None
    assert attempts[0].transport_status == "error"


@pytest.mark.asyncio
async def test_source_analysis_survives_missing_api_key():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    analyzer = SourceAnalyzer({"anthropic": _keyless_provider()})

    result = await analyzer.analyze(
        dr, _base_run_config(source_text="fonte qualquer", source_analyzer_provider="anthropic")
    )

    assert result is not None
    assert result.skipped_reason == "source_analysis_transport_failed"
    assert len(result.attempts) == 1
    assert result.attempts[0].transport_attempts == 0
    assert result.attempts[0].cost_usd == 0.0


@pytest.mark.asyncio
async def test_judge_survives_missing_api_key():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    judge = SingleJudge({"anthropic": _keyless_provider()})

    result = await judge.judge(
        dr, _base_run_config(judge_provider="anthropic"),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_transport_failed"
    assert len(result.attempts) == 1
    assert result.attempts[0].transport_attempts == 0
    assert result.attempts[0].cost_usd == 0.0


@pytest.mark.asyncio
async def test_editor_survives_missing_api_key():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    v = editor_verdict_fixture(
        [__import__("app.models.domain", fromlist=["ClaimAssessment"]).ClaimAssessment(
            claim_id=c1.id, verdict="supported", explanation="ok"
        )]
    )
    jr = editor_judge_result_fixture(v)
    editor = Editor({"anthropic": _keyless_provider()})

    result = await editor.compose(
        dr, jr, _base_run_config(editor_provider="anthropic"),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert result.fallback_reason == "editor_transport_failed"
    assert len(result.attempts) == 1
    assert result.attempts[0].transport_attempts == 0
    assert result.attempts[0].cost_usd == 0.0
