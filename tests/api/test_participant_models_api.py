"""Council Accepted Effective Participant Model Choice V1 -- contrato HTTP.

`participant_model_overrides` é opcional e só do Conselho, com a mesma
semântica na prévia e na criação; o mapa efetivo congelado aparece em
`config.participant_models` (detalhe e auditoria); runs anteriores mostram
`null` (não registrado)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.app import create_app
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from tests.api.helpers import make_components_factory
from tests.direct.fakes import ScriptedApiProvider
from tests.storage.fixtures import full_council_run_result, now, run_config

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)


def _providers():
    return {
        name: ScriptedApiProvider(name, [], default_model=f"{name}-configured")
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


def _create(client, **extra):
    return client.post(
        "/runs", json={"question": "Qual a capital?", "enabled_providers": ["openai", "gemini"], **extra}
    )


def test_old_request_body_freezes_the_configured_defaults():
    with _client(_providers()) as client:
        created = _create(client)

    assert created.status_code == 201
    assert created.json()["config"]["participant_models"] == [
        {"provider": "openai", "requested_model": "openai-configured", "origin": "configured_default"},
        {"provider": "gemini", "requested_model": "gemini-configured", "origin": "configured_default"},
    ]


def test_override_is_frozen_and_exposed_in_creation_detail_and_audit():
    with _client(_providers()) as client:
        created = _create(client, participant_model_overrides={"gemini": "gemini-explicit"}).json()
        detail = client.get(f"/runs/{created['id']}").json()
        audit = client.get(f"/runs/{created['id']}/audit").json()

    expected = [
        {"provider": "openai", "requested_model": "openai-configured", "origin": "configured_default"},
        {"provider": "gemini", "requested_model": "gemini-explicit", "origin": "run_override"},
    ]
    assert created["config"]["participant_models"] == expected
    assert detail["config"]["participant_models"] == expected
    assert audit["config"]["participant_models"] == expected


def test_preview_and_creation_share_the_same_planned_models():
    with _client(_providers()) as client:
        preview = client.post(
            "/runs/readiness",
            json={"enabled_providers": ["openai", "gemini"], "participant_model_overrides": {"openai": "gpt-x"}},
        ).json()
        created = _create(client, participant_model_overrides={"openai": "gpt-x"}).json()

    participants = [d for d in preview["dependencies"] if d["role"] == "participant"]
    assert [(d["provider"], d["configured_default_model"], d["planned_model"], d["planned_model_origin"]) for d in participants] == [
        ("openai", "openai-configured", "gpt-x", "run_override"),
        ("gemini", "gemini-configured", "gemini-configured", "configured_default"),
    ]
    assert created["council_admission"]["readiness"] == preview


@pytest.mark.parametrize(
    "overrides",
    [
        {"anthropic": "claude-x"},  # não é participante selecionado
        {"openai": ""},
        {"openai": " gpt-x"},
        {"openai": "gpt x"},
        {"openai": 5},
        ["openai", "gpt-x"],
    ],
)
def test_invalid_overrides_are_invalid_requests_with_no_side_effects(overrides):
    providers = _providers()
    with _client(providers) as client:
        created = _create(client, participant_model_overrides=overrides)
        preview = client.post(
            "/runs/readiness",
            json={"enabled_providers": ["openai", "gemini"], "participant_model_overrides": overrides},
        )
        runs = client.get("/runs").json()["runs"]

    for resp in (created, preview):
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_request"
    assert runs == []
    assert all(p.requests == [] for p in providers.values())


def test_direct_rejects_council_participant_model_overrides():
    with _client(_providers()) as client:
        resp = client.post(
            "/runs",
            json={
                "question": "q",
                "enabled_providers": ["openai"],
                "kind": "direct",
                "participant_model_overrides": {"openai": "gpt-x"},
            },
        )
        runs = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    assert runs == []


def test_a_run_accepted_before_this_field_reads_as_not_captured():
    with _client(_providers()) as client:
        repository = client.app.state.components.repository
        engine = client.app.state.components.engine
        client.portal.call(
            lambda: repository.save_accepted(
                "legacy-run", run_config=run_config(), started_at=now(), provider_execution_policy=POLICY
            )
        )

        async def drop_field():
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE accepted_runs SET run_config_json = "
                        "json_remove(run_config_json, '$.participant_models') WHERE id = 'legacy-run'"
                    )
                )

        client.portal.call(drop_field)
        detail = client.get("/runs/legacy-run").json()

    assert detail["status"] == "running"
    assert detail["config"]["participant_models"] is None  # nunca reconstruído do padrão atual


def test_openapi_documents_the_participant_model_contract():
    with _client(_providers()) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]

    for request in ("CreateRunRequest", "CouncilReadinessRequest"):
        assert "participant_model_overrides" in schemas[request]["properties"]
        assert "participant_model_overrides" not in schemas[request].get("required", [])
    assert "participant_models" in schemas["RunConfigPublic"]["properties"]
    assert "participant_models" not in schemas["RunConfigPublic"].get("required", [])
    assert set(schemas["ParticipantModelChoicePublic"]["properties"]) == {"provider", "requested_model", "origin"}
    assert set(schemas["ParticipantModelChoicePublic"]["properties"]["origin"]["enum"]) == {
        "configured_default",
        "run_override",
    }
    dependency = schemas["CouncilDependencyReadinessPublic"]["properties"]
    assert {"planned_model", "planned_model_origin"} <= set(dependency)
    assert schemas["CouncilReadinessPublic"]["properties"]["contract_version"]["enum"] == [
        "council_local_readiness_v1",
        "council_local_readiness_v2",
    ]


def test_invalid_override_errors_point_at_the_field():
    with _client(_providers()) as client:
        resp = _create(client, participant_model_overrides={"anthropic": "claude-x"})

    [error] = resp.json()["error"]["details"]["errors"]
    assert error["loc"][-1] == "participant_model_overrides"


@pytest.mark.parametrize("value", ["gpt\u202e-x", "gpt\u200b-x", "gpt\x85-x", "gpt\x9b-x"])
def test_invisible_or_control_characters_never_become_an_accepted_model(value):
    """Regressão: U+202E (e outros invisíveis/C1) passavam pela validação e o
    identificador era persistido exatamente como enviado (201)."""
    providers = _providers()
    with _client(providers) as client:
        created = _create(client, participant_model_overrides={"openai": value})
        preview = client.post(
            "/runs/readiness",
            json={"enabled_providers": ["openai", "gemini"], "participant_model_overrides": {"openai": value}},
        )
        runs = client.get("/runs").json()["runs"]

    for resp in (created, preview):
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "invalid_request"
        assert error["details"]["errors"][0]["loc"][-1] == "participant_model_overrides"
        # a mensagem nomeia o caractere por U+XXXX, nunca cru (o `input` do
        # handler genérico de validação ecoa o que o próprio cliente mandou)
        assert value not in error["details"]["errors"][0]["msg"]
    assert runs == []
    assert all(p.requests == [] for p in providers.values())


def test_visible_non_ascii_identifiers_are_still_accepted_verbatim():
    with _client(_providers()) as client:
        created = _create(client, participant_model_overrides={"openai": "modèle-é"})

    assert created.status_code == 201
    assert created.json()["config"]["participant_models"][0] == {
        "provider": "openai",
        "requested_model": "modèle-é",
        "origin": "run_override",
    }
