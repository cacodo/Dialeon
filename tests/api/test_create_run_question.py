"""
Accepted Question Size Boundary V1 -- POST /runs, contrato HTTP público.

Mesmo espírito/estrutura de tests/api/test_create_run_source.py: prova o
threading POST /runs -> CreateRunRequest -> RunConfig.from_settings ->
CouncilExecutionService.run() de ponta a ponta, incluindo o caminho de
rejeição ANTES de qualquer chamada de provider.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.orchestrator.config import MAX_QUESTION_CHARACTERS
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_create_run_question_preserved_verbatim():
    """A question efetivamente construída (visível via o que chega ao
    DebateEngine fake) precisa ser EXATAMENTE a enviada -- incluindo
    espaço em branco significativo ao redor, nunca trimada."""
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
                "question": "  Qual a capital do Brasil?  ",
                "enabled_providers": ["openai", "anthropic"],
            },
        )

    assert resp.status_code == 201
    components = app.state.components
    debate_engine_calls = components.service._runner._debate_engine.calls
    assert len(debate_engine_calls) == 1
    assert debate_engine_calls[0].question == "  Qual a capital do Brasil?  "


def test_create_run_oversized_question_returns_422_no_provider_call():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)
    oversized = "x" * (MAX_QUESTION_CHARACTERS + 1)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"question": oversized, "enabled_providers": ["openai"]},
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    components = app.state.components
    assert components.service._runner._debate_engine.calls == []


def test_create_run_whitespace_only_question_returns_422_no_provider_call():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"question": "   \n\t  ", "enabled_providers": ["openai"]},
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    components = app.state.components
    assert components.service._runner._debate_engine.calls == []


def test_create_run_exact_boundary_question_is_accepted():
    """Exatamente MAX_QUESTION_CHARACTERS caracteres (o limite exato,
    não N-1) é aceito e executa o caminho fake normal."""
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)
    boundary_question = "x" * MAX_QUESTION_CHARACTERS

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"question": boundary_question, "enabled_providers": ["openai"]},
        )

    assert resp.status_code == 201
    components = app.state.components
    assert components.service._runner._debate_engine.calls[0].question == boundary_question


def test_create_run_one_over_boundary_question_is_rejected():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)
    one_over = "x" * (MAX_QUESTION_CHARACTERS + 1)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"question": one_over, "enabled_providers": ["openai"]},
        )

    assert resp.status_code == 422
    components = app.state.components
    assert components.service._runner._debate_engine.calls == []
