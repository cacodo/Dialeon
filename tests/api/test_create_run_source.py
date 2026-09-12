from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.orchestrator.config import MAX_SOURCE_TEXT_CHARACTERS
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.models import ValidSourceRelation
from app.source_analysis.result import SourceAnalysisResult
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_create_run_with_source_text_reaches_run_config():
    """O RunConfig efetivamente construído (visível via o que chega ao
    DebateEngine fake) precisa carregar o source_text exatamente como
    enviado -- prova que o threading POST /runs -> RunConfig.from_settings
    -> CouncilExecutionService.run() está correto de ponta a ponta."""
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={
                "question": "Qual a capital do Brasil?",
                "enabled_providers": ["openai", "anthropic"],
                "source_text": "Brasília é a capital federal do Brasil desde 1960.",
            },
        )

    assert resp.status_code == 201
    components = app.state.components
    debate_engine_calls = components.service._runner._debate_engine.calls
    assert len(debate_engine_calls) == 1
    assert debate_engine_calls[0].source_text == "Brasília é a capital federal do Brasil desde 1960."


def test_create_run_without_source_text_is_none_in_run_config():
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        client.post(
            "/runs",
            json={"question": "pergunta", "enabled_providers": ["openai"]},
        )

    components = app.state.components
    debate_engine_calls = components.service._runner._debate_engine.calls
    assert debate_engine_calls[0].source_text is None


def test_create_run_oversized_source_returns_422_no_provider_call():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)
    oversized = "x" * (MAX_SOURCE_TEXT_CHARACTERS + 1)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={
                "question": "pergunta",
                "enabled_providers": ["openai"],
                "source_text": oversized,
            },
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    components = app.state.components
    assert components.service._runner._debate_engine.calls == []


def test_create_run_whitespace_only_source_normalized_to_none():
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={
                "question": "pergunta",
                "enabled_providers": ["openai"],
                "source_text": "   \n\t  ",
            },
        )

    assert resp.status_code == 201
    components = app.state.components
    assert components.service._runner._debate_engine.calls[0].source_text is None


def test_audit_exposes_source_analysis_outcome_and_relations():
    result = full_council_run_result()
    attempt = SourceAnalysisAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{...}",
        parse_status="accepted",
        latency_ms=100,
    )
    claim = result.debate_result.claims[0]
    relation = ValidSourceRelation(
        claim_id=claim.id,
        relation="supports",
        excerpt="trecho",
        excerpt_start=0,
        excerpt_end=6,
    )
    source_analysis_result = SourceAnalysisResult(
        attempts=[attempt],
        claim_results=[relation],
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    result = result.model_copy(update={"source_analysis_result": source_analysis_result})

    factory = make_components_factory(
        debate_result=result.debate_result,
        source_analysis_result=result.source_analysis_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        create_resp = client.post(
            "/runs",
            json={
                "question": "pergunta",
                "enabled_providers": ["openai"],
                "source_text": "trecho de exemplo",
            },
        )
        run_id = create_resp.json()["id"]
        audit_resp = client.get(f"/runs/{run_id}/audit")

    body = audit_resp.json()
    assert body["source_analysis"] is not None
    assert body["source_analysis"]["source_analyzer_provider"] == "anthropic"
    assert body["source_analysis"]["skipped_reason"] is None
    relations = body["source_analysis"]["claim_results"]
    assert relations[0]["kind"] == "relation"
    assert relations[0]["relation"] == "supports"
