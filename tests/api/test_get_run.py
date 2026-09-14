from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import (
    error_model_response,
    full_council_run_result,
    model_response,
    now,
    quorum_failure_exception,
    run_config,
)


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _seed_success(components, result) -> str:
    await components.repository.save_success(result)
    return result.id


async def _seed_accepted_then_success(components, result) -> str:
    """T02.2 -- fluxo completo aceite->sucesso, pra provar que a política
    resolvida (`components.provider_execution_policy`) chega ao detail
    público -- diferente de `_seed_success` isolado (sem accepted_runs
    prévio), que produz `provider_execution_policy=None` de propósito."""
    await components.repository.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=components.provider_execution_policy,
    )
    await components.repository.save_success(result)
    return result.id


async def _seed_quorum_failure(components, exc) -> str:
    return await components.repository.save_quorum_failure(
        exc, run_config=run_config(), started_at=now(), failed_at=now()
    )


async def _seed_accepted(components, run_id: str) -> str:
    await components.repository.save_accepted(
        run_id,
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )
    return run_id


async def _seed_unexpected_failure(components, run_id: str) -> str:
    await components.repository.save_accepted(
        run_id,
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )
    await components.repository.save_unexpected_failure(
        run_id,
        failed_at=now(),
        failure_classification="WeirdBug",
        failure_message="Erro interno inesperado durante a execução.",
    )
    return run_id


def test_get_run_completed():
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["id"] == run_id
    assert body["final_answer"]["answer_text"] == result.final_answer.answer_text
    assert "judge_reasoning" not in body["final_answer"]
    assert set(body.keys()) == {
        "status", "id", "started_at", "completed_at", "final_answer", "accounting", "config",
        "provider_execution_policy",
    }
    # T02.2 -- sem accepted_runs prévio (_seed_success isolado), o valor
    # honesto é None, nunca um default inventado.
    assert body["provider_execution_policy"] is None


def test_get_run_completed_with_known_policy_displays_it():
    """T02.2, teste P -- fluxo completo (aceite -> sucesso) expõe a
    política resolvida corretamente, como SIBLING de `config` (nunca
    dentro dele)."""
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_accepted_then_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    body = resp.json()
    assert body["provider_execution_policy"] == {
        "attempt_timeout_seconds": 30.0,
        "max_transport_attempts_per_completion": 2,
    }
    assert "provider_execution_policy" not in body["config"]


def test_get_run_completed_accounting_matches_domain():
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    accounting = resp.json()["accounting"]
    assert accounting["total_input_tokens"] == result.total_input_tokens
    assert accounting["total_output_tokens"] == result.total_output_tokens
    assert accounting["estimated_cost_usd"] == result.total_cost_usd
    assert accounting["has_unknown_accounting_components"] == result.has_unknown_accounting_components


def test_get_run_completed_preserves_none_cost():
    result = full_council_run_result()
    error_mr = error_model_response("gemini")
    initial = result.debate_result.initial_result.model_copy(
        update={
            "responses": [*result.debate_result.initial_result.responses, error_mr],
            "has_unknown_accounting_components": True,
        }
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    assert resp.json()["accounting"]["has_unknown_accounting_components"] is True


def test_get_run_completed_preserves_zero_cost():
    result = full_council_run_result()
    zero_mr = model_response("local", model="self-hosted", cost_usd=0.0)
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, zero_mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    # total_cost_usd continua um float exato, sem esconder o zero
    assert resp.json()["accounting"]["estimated_cost_usd"] == result.total_cost_usd


def test_get_run_insufficient_quorum():
    exc = quorum_failure_exception()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_quorum_failure, app.state.components, exc)
        resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "insufficient_quorum"
    assert body["id"] == run_id
    assert body["successful_count"] == exc.successful_count
    assert body["total_providers"] == exc.total_providers
    assert body["min_to_return"] == exc.min_to_return
    assert "final_answer" not in body  # nunca fingir completed


def test_get_run_running():
    """T02.4, teste I -- um run aceito, ainda sem desfecho terminal,
    discrimina corretamente (`status="running"`) e nunca inventa
    `final_answer`/`accounting` que não existem."""
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_accepted, app.state.components, "run-running-1")
        resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["id"] == run_id
    assert "final_answer" not in body
    assert "accounting" not in body
    assert set(body.keys()) == {"status", "id", "started_at", "config", "provider_execution_policy"}


def test_get_run_failed():
    """T02.4, teste I/F -- um run com falha inesperada discrimina
    corretamente (`status="failed"`) e só expõe informação já
    sanitizada (classificação/mensagem fixas -- nunca traceback)."""
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(
            _seed_unexpected_failure, app.state.components, "run-failed-1"
        )
        resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["id"] == run_id
    assert body["failure_reason"] == "WeirdBug"
    assert body["message"] == "Erro interno inesperado durante a execução."
    assert "final_answer" not in body
    assert "accounting" not in body
    assert set(body.keys()) == {
        "status", "id", "started_at", "failed_at", "failure_reason", "message", "config",
        "provider_execution_policy",
    }


def test_get_run_not_found_returns_404():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        resp = client.get("/runs/id-que-nao-existe")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"
