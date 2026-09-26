"""Council Local Execution Readiness & Admission V1 -- contrato HTTP.

`POST /runs/readiness` é uma prévia opcional, sem efeito; `POST /runs` sem os
campos novos continua com o aceite da v1.3.0; `readiness_admission="strict"`
recusa (422 `council_prerequisites_missing`) antes de qualquer registro ou
chamada; os fatos de aceite aparecem em detalhe e auditoria.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from tests.api.helpers import make_components_factory
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import full_council_run_result

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
SECRET = "sk-live-SECRET-never-shown-0123456789"


def _providers(**states):
    """Os papéis internos padrão de `Settings` são todos "anthropic"."""
    return {
        name: ScriptedApiProvider(
            name, [], api_key=SECRET, default_model=f"{name}-configured", prerequisite=states.get(name)
        )
        for name in ("openai", "anthropic", "gemini")
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


def _preview(client, **body):
    return client.post("/runs/readiness", json={"enabled_providers": ["openai", "gemini"], **body})


def _create(client, **body):
    return client.post(
        "/runs", json={"question": "Qual a capital do Brasil?", "enabled_providers": ["openai", "gemini"], **body}
    )


def _roles(readiness):
    return [(d["role"], d["provider"], d["local_prerequisite"], d["applicability"]) for d in readiness["dependencies"]]


# ---------------------------------------------------------------------------
# Prévia
# ---------------------------------------------------------------------------


def test_preview_reports_every_dependency_with_its_configured_model_and_no_secret():
    providers = _providers()
    with _client(providers) as client:
        resp = _preview(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_version"] == "council_local_readiness_v1"
    assert body["summary"] == "all_met"
    assert body["strict_admission"] == "admissible"
    assert _roles(body) == [
        ("participant", "openai", "met", "selected"),
        ("participant", "gemini", "met", "selected"),
        ("claim_extraction", "anthropic", "met", "potential"),
        ("source_analysis", "anthropic", "met", "not_applicable"),
        ("judge", "anthropic", "met", "potential"),
        ("editor", "anthropic", "met", "potential"),
        ("semantic_review", "anthropic", "met", "potential"),
    ]
    assert {d["configured_default_model"] for d in body["dependencies"]} == {
        "openai-configured",
        "gemini-configured",
        "anthropic-configured",
    }
    assert SECRET not in resp.text
    assert all(p.requests == [] for p in providers.values())


def test_preview_with_source_makes_source_analysis_applicable():
    with _client(_providers()) as client:
        body = _preview(client, source_supplied=True).json()

    assert ("source_analysis", "anthropic", "met", "potential") in _roles(body)


def test_preview_discloses_known_degradation_and_unknown_distinctly():
    with _client(_providers(anthropic="missing", gemini="unknown")) as client:
        body = _preview(client).json()

    assert body["summary"] == "some_missing"
    assert body["strict_admission"] == "blocked"
    states = {(d["role"], d["provider"]): d["local_prerequisite"] for d in body["dependencies"]}
    assert states[("participant", "gemini")] == "unknown"  # nunca virou "missing"
    assert states[("claim_extraction", "anthropic")] == "missing"


def test_preview_creates_no_run():
    with _client(_providers(anthropic="missing")) as client:
        _preview(client)
        runs = client.get("/runs").json()["runs"]

    assert runs == []


@pytest.mark.parametrize(
    "body, code",
    [
        ({"enabled_providers": ["openai", "nope"]}, "invalid_provider"),
        ({"enabled_providers": []}, "invalid_request"),
        ({"enabled_providers": ["openai", "openai"]}, "invalid_request"),
        ({"enabled_providers": ["openai"], "surprise": True}, "invalid_request"),
        ({"enabled_providers": ["openai"], "source_text": "x"}, "invalid_request"),
    ],
)
def test_preview_rejects_invalid_requests_like_creation(body, code):
    with _client(_providers()) as client:
        resp = client.post("/runs/readiness", json=body)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == code


def test_preview_requires_a_json_content_type_like_creation():
    with _client(_providers()) as client:
        resp = client.post(
            "/runs/readiness", content='{"enabled_providers": ["openai"]}', headers={"Content-Type": "text/plain"}
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Admissão
# ---------------------------------------------------------------------------


def test_strict_rejection_happens_before_any_run_or_call_and_carries_the_readiness():
    providers = _providers(anthropic="missing")
    with _client(providers) as client:
        preview = _preview(client).json()
        resp = _create(client, readiness_admission="strict")
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "council_prerequisites_missing"
    assert error["details"]["readiness"] == preview  # prévia == avaliação usada na admissão
    assert runs == []
    assert all(p.requests == [] for p in providers.values())
    assert SECRET not in resp.text


def test_strict_admits_unknown_and_all_met():
    with _client(_providers(anthropic="unknown")) as client:
        resp = _create(client, readiness_admission="strict")

    assert resp.status_code == 201
    admission = resp.json()["council_admission"]
    assert admission["mode"] == "strict"
    assert admission["readiness"]["summary"] == "some_unknown"


def test_legacy_request_keeps_the_v130_acceptance_even_with_known_degradation():
    with _client(_providers(anthropic="missing")) as client:
        preview = _preview(client).json()
        resp = _create(client)

    assert resp.status_code == 201
    admission = resp.json()["council_admission"]
    assert admission == {
        "contract_version": "council_admission_v1",
        "mode": "standard",
        "known_degradation_acknowledged": False,
        "readiness": preview,
    }


def test_acknowledgement_is_recorded_and_exposed_in_detail_and_audit():
    with _client(_providers(anthropic="missing")) as client:
        created = _create(client, acknowledge_known_degradation=True).json()
        detail = client.get(f"/runs/{created['id']}").json()
        audit = client.get(f"/runs/{created['id']}/audit").json()

    assert created["council_admission"]["known_degradation_acknowledged"] is True
    assert detail["council_admission"] == created["council_admission"]
    assert audit["council_admission"] == created["council_admission"]
    assert "council_admission" not in detail["config"]  # sibling, nunca dentro de RunConfigPublic


@pytest.mark.parametrize(
    "extra",
    [
        {"readiness_admission": "strict", "acknowledge_known_degradation": True},
        {"readiness_admission": "lenient"},
        {"acknowledge_known_degradation": "yes"},
    ],
)
def test_contradictory_or_malformed_admission_is_an_invalid_request(extra):
    with _client(_providers()) as client:
        resp = _create(client, **extra)
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    assert runs == []


# ---------------------------------------------------------------------------
# Resposta direta inalterada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [{"readiness_admission": "strict"}, {"readiness_admission": "standard"}, {"acknowledge_known_degradation": False}],
)
def test_direct_runs_do_not_accept_council_admission_fields(extra):
    with _client(_providers()) as client:
        resp = client.post(
            "/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct", **extra}
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"


def test_direct_run_is_unchanged_and_has_no_council_admission():
    providers = _providers(anthropic="missing")
    providers["openai"].script.append(ok("Brasília."))
    with _client(providers) as client:
        resp = client.post("/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"})

    assert resp.status_code == 201
    assert resp.json()["kind"] == "direct"
    assert resp.json()["answer"] == "Brasília."
    assert "council_admission" not in resp.json()


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_openapi_documents_the_preview_and_the_additive_fields():
    with _client(_providers()) as client:
        openapi = client.get("/openapi.json").json()

    operation = openapi["paths"]["/runs/readiness"]["post"]
    assert set(operation["responses"]) == {"200", "422", "500"}
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CouncilReadinessRequest"
    )
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CouncilReadinessPublic"
    )
    schemas = openapi["components"]["schemas"]
    assert schemas["CouncilReadinessRequest"]["additionalProperties"] is False  # request estrito
    assert "additionalProperties" not in schemas["CouncilReadinessPublic"]  # resposta aberta
    assert set(schemas["CouncilReadinessRequest"]["required"]) == {"enabled_providers"}
    create = schemas["CreateRunRequest"]
    assert set(create["required"]) == {"question", "enabled_providers"}  # campos novos opcionais
    for name in ("CompletedRunResponse", "RunningRunResponse", "FailedRunResponse", "QuorumFailureRunResponse",
                 "CompletedRunAudit", "QuorumFailureAudit"):
        assert "council_admission" in schemas[name]["properties"], name
        assert "council_admission" not in schemas[name].get("required", []), name
    assert "council_prerequisites_missing" in schemas["ErrorBody"]["properties"]["code"]["enum"]
