from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, quorum_failure_exception


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_create_run_completed_returns_201():
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
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert body["final_answer"]["answer_text"] == result.final_answer.answer_text
    assert "id" in body


def test_create_run_calls_service_exactly_once():
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )
        debate_engine = app.state.components.service._runner._debate_engine
        assert len(debate_engine.calls) == 1


def test_create_run_uses_run_config_from_settings():
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    settings = _settings()
    app = create_app(settings=settings, components_factory=factory)

    with TestClient(app) as client:
        client.post(
            "/runs", json={"question": "pergunta específica", "enabled_providers": ["openai"]}
        )
        debate_engine = app.state.components.service._runner._debate_engine
        used_config = debate_engine.calls[0]

    assert used_config.question == "pergunta específica"
    assert used_config.enabled_providers == ("openai",)
    assert used_config.max_cost_usd == settings.default_max_cost_usd
    assert used_config.max_total_tokens == settings.default_max_total_tokens
    assert used_config.judge_provider == settings.default_judge_provider


def test_create_run_invalid_provider_returns_422_before_calling_service():
    factory = make_components_factory(provider_names=("openai", "anthropic"))
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"question": "pergunta", "enabled_providers": ["openai", "provider-fake"]},
        )
        debate_engine = app.state.components.service._runner._debate_engine

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "invalid_provider"
    assert "provider-fake" in body["error"]["details"]["unknown_providers"]
    assert debate_engine.calls == []


def test_create_run_blank_question_returns_422():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post("/runs", json={"question": "   ", "enabled_providers": ["openai"]})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"


def test_create_run_empty_question_returns_422():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post("/runs", json={"question": "", "enabled_providers": ["openai"]})

    assert resp.status_code == 422


def test_create_run_duplicate_provider_returns_422():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "openai"]}
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"


def test_create_run_empty_enabled_providers_returns_422():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post("/runs", json={"question": "pergunta", "enabled_providers": []})

    assert resp.status_code == 422


def test_create_run_insufficient_quorum_returns_409():
    exc = quorum_failure_exception()
    factory = make_components_factory(quorum_exc=exc)
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "insufficient_quorum"
    assert body["error"]["details"]["successful_count"] == exc.successful_count
    assert body["error"]["details"]["total_providers"] == exc.total_providers
    assert body["error"]["details"]["min_to_return"] == exc.min_to_return


def test_create_run_409_contains_persisted_run_id():
    exc = quorum_failure_exception()
    factory = make_components_factory(quorum_exc=exc)
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )
        run_id = resp.json()["error"]["details"]["run_id"]
        assert run_id is not None

        get_resp = client.get(f"/runs/{run_id}")

    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "insufficient_quorum"
    assert get_resp.json()["id"] == run_id


def test_create_run_does_not_leak_round_result_in_error_body():
    exc = quorum_failure_exception()
    factory = make_components_factory(quorum_exc=exc)
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )

    details = resp.json()["error"]["details"]
    assert "round_result" not in details
    assert "responses" not in details


def test_create_run_unexpected_internal_error_returns_500_generic():
    factory = make_components_factory()
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/runs", json={"question": "pergunta", "enabled_providers": ["openai"]})

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert "AssertionError" not in body["error"]["message"]
    assert "Traceback" not in str(body)
