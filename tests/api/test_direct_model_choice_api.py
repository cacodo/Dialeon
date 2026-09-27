"""Direct Accepted Effective Model Choice V1 -- contrato HTTP.

`requested_model` é opcional e só da resposta direta: omitido (ou `null`) =
o padrão configurado do provider; presente = o modelo pedido nesta run. O
modelo e a origem congelados aparecem em `config` (criação, detalhe e
auditoria). O Conselho recusa o campo; a resposta direta continua recusando
`participant_model_overrides`."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.app import create_app
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from app.providers.errors import ProviderAPIError
from tests.api.helpers import make_components_factory
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import full_council_run_result

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)


def _providers(openai_script=None):
    return {
        "openai": ScriptedApiProvider("openai", openai_script or [ok()], default_model="gpt-conf"),
        "gemini": ScriptedApiProvider("gemini", [ok()], default_model="gemini-conf"),
    }


def _client(providers):
    result = full_council_run_result()
    factory = make_components_factory(
        provider_instances=providers,
        provider_execution_policy=POLICY,
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    return TestClient(create_app(settings=Settings(_env_file=None), components_factory=factory))


def _direct(client, **extra):
    return client.post(
        "/runs",
        json={"question": "Qual a capital?", "enabled_providers": ["openai"], "kind": "direct", **extra},
    )


@pytest.mark.parametrize("extra", [{}, {"requested_model": None}])
def test_omitted_model_keeps_the_existing_direct_request_and_freezes_the_default(extra):
    providers = _providers()
    with _client(providers) as client:
        created = _direct(client, **extra)

    assert created.status_code == 201
    config = created.json()["config"]
    assert (config["requested_model"], config["requested_model_origin"]) == ("gpt-conf", "configured_default")
    assert [r.model for r in providers["openai"].requests] == ["gpt-conf"]


def test_explicit_model_is_frozen_and_exposed_in_creation_detail_and_audit():
    providers = _providers([ok(observed_model="gpt-explicit-2026")])
    with _client(providers) as client:
        created = _direct(client, requested_model="gpt-explicit").json()
        detail = client.get(f"/runs/{created['id']}").json()
        audit = client.get(f"/runs/{created['id']}/audit").json()

    for body in (created, detail, audit):
        assert body["config"]["requested_model"] == "gpt-explicit"
        assert body["config"]["requested_model_origin"] == "run_override"
        assert body["response"]["requested_model"] == "gpt-explicit"
        assert body["response"]["model"] == "gpt-explicit-2026"
        assert body["response"]["model_identity_source"] == "provider_reported"
        assert body["response"]["request_provenance"]["contract_version"] == "direct_answer_v1"
    assert [r.model for r in providers["openai"].requests] == ["gpt-explicit"]
    assert providers["gemini"].requests == []


def test_remote_rejection_of_the_chosen_model_is_a_created_failed_run_without_substitution():
    providers = _providers([ProviderAPIError("openai: status=404: model not found", retryable=False)])
    with _client(providers) as client:
        created = _direct(client, requested_model="gpt-nope")

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "failed" and body["failure_reason"] == "api_error"
    assert body["config"]["requested_model"] == "gpt-nope"
    assert body["response"]["requested_model"] == "gpt-nope"
    assert body["response"]["cost_usd"] is None
    assert body["accounting"]["has_unknown_accounting_components"] is True
    assert [r.model for r in providers["openai"].requests] == ["gpt-nope"]
    assert providers["gemini"].requests == []


@pytest.mark.parametrize(
    "value",
    ["", "   ", " gpt-x", "gpt x", 5, ["gpt-x"], "gpt‮-x", "gpt​-x", "gpt\x85-x", "gpt͏x", "gpt️x",
     "g" * 257],
)
def test_invalid_models_are_invalid_requests_with_no_run_and_no_call(value):
    providers = _providers()
    with _client(providers) as client:
        resp = _direct(client, requested_model=value)
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"]["errors"][0]["loc"][-1] == "requested_model"
    if isinstance(value, str) and any(not c.isprintable() for c in value):
        assert value not in error["details"]["errors"][0]["msg"]
    assert runs == []
    assert all(p.requests == [] for p in providers.values())


def test_council_rejects_requested_model_without_side_effects():
    providers = _providers()
    with _client(providers) as client:
        resp = client.post(
            "/runs",
            json={"question": "q", "enabled_providers": ["openai", "gemini"], "requested_model": "gpt-x"},
        )
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    assert "participant_model_overrides" in resp.json()["error"]["details"]["errors"][0]["msg"]
    assert runs == []
    assert all(p.requests == [] for p in providers.values())


def test_council_readiness_preview_rejects_requested_model():
    with _client(_providers()) as client:
        resp = client.post("/runs/readiness", json={"enabled_providers": ["openai"], "requested_model": "gpt-x"})

    assert resp.status_code == 422


def test_direct_still_rejects_participant_model_overrides_and_points_to_requested_model():
    providers = _providers()
    with _client(providers) as client:
        resp = _direct(client, participant_model_overrides={"openai": "gpt-x"})
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert "requested_model" in resp.json()["error"]["details"]["errors"][0]["msg"]
    assert runs == []
    assert providers["openai"].requests == []


def test_a_pre_slice_direct_run_is_served_as_configured_default():
    with _client(_providers()) as client:
        created = _direct(client).json()
        engine = client.app.state.components.engine

        async def drop_origin():
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE direct_runs SET run_config_json = "
                        "json_remove(run_config_json, '$.requested_model_origin') WHERE id = :id"
                    ),
                    {"id": created["id"]},
                )

        client.portal.call(drop_origin)
        detail = client.get(f"/runs/{created['id']}").json()
        audit = client.get(f"/runs/{created['id']}/audit").json()

    for body in (detail, audit):
        assert body["config"]["requested_model"] == "gpt-conf"
        assert body["config"]["requested_model_origin"] == "configured_default"


def test_openapi_documents_the_direct_model_contract():
    with _client(_providers()) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]

    request = schemas["CreateRunRequest"]
    assert "requested_model" in request["properties"]
    assert "requested_model" not in request.get("required", [])
    config = schemas["DirectRunConfigPublic"]
    assert "requested_model_origin" in config["required"]
    assert set(config["properties"]["requested_model_origin"]["enum"]) == {"configured_default", "run_override"}
    # a prévia do Conselho não ganhou o campo
    assert "requested_model" not in schemas["CouncilReadinessRequest"]["properties"]


def test_the_service_boundary_rejection_has_the_same_http_shape(monkeypatch):
    """A validação da boundary do service vale mesmo quando o schema não a
    faz (chamadores diretos do service): mesmo 422 apontando pro campo."""
    import app.presentation.schemas as schemas

    monkeypatch.setattr(schemas, "validate_model_override_identifier", lambda value: value)
    providers = _providers()
    with _client(providers) as client:
        resp = _direct(client, requested_model="gpt‮-x")
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    [error] = resp.json()["error"]["details"]["errors"]
    assert (error["loc"], error["type"]) == (["requested_model"], "invalid_direct_model")
    assert "‮" not in error["msg"] and "U+202E" in error["msg"]
    assert runs == []
    assert providers["openai"].requests == []
