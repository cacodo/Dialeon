"""Direct Answer Execution V1 -- contrato HTTP.

`POST /runs` sem `kind` continua sendo o Conselho; `kind="direct"` é opt-in.
As respostas diretas sempre carregam `kind="direct"`; as do Conselho não
mudaram de forma.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from app.providers.errors import ProviderTimeoutError
from tests.api.helpers import make_components_factory
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import full_council_run_result

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
COUNCIL_KEYS = {
    "status",
    "id",
    "started_at",
    "completed_at",
    "final_answer",
    "accounting",
    "config",
    "provider_execution_policy",
    "default_model_authority_snapshot",
}


def _client(**providers):
    result = full_council_run_result()
    factory = make_components_factory(
        provider_instances=providers,
        provider_execution_policy=POLICY,
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    return TestClient(create_app(settings=Settings(_env_file=None), components_factory=factory))


def _direct(client, **overrides):
    body = {"question": "Qual a capital do Brasil?", "enabled_providers": ["openai"], "kind": "direct"}
    body.update(overrides)
    return client.post("/runs", json=body)


def test_omitting_kind_keeps_the_council_contract_exactly():
    openai = ScriptedApiProvider("openai", [])
    with _client(openai=openai, anthropic=ScriptedApiProvider("anthropic", [])) as client:
        resp = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        listed = client.get("/runs").json()["runs"]

    assert resp.status_code == 201
    assert set(resp.json()) == COUNCIL_KEYS  # sem `kind`: forma do Conselho inalterada
    assert resp.json()["final_answer"]["answer_text"]
    assert openai.requests == []  # o Conselho (fakes) respondeu; nenhuma chamada direta
    assert [run["kind"] for run in listed] == ["council"]


def test_explicit_council_kind_is_the_same_as_omitting_it():
    with _client(openai=ScriptedApiProvider("openai", []), anthropic=ScriptedApiProvider("anthropic", [])) as client:
        resp = client.post("/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "council"})

    assert resp.status_code == 201
    assert set(resp.json()) == COUNCIL_KEYS


def test_direct_run_creates_detail_audit_and_list_entries_with_an_explicit_kind():
    openai = ScriptedApiProvider("openai", [ok("Brasília.", observed_model="gpt-conf-2026")], default_model="gpt-conf")
    with _client(openai=openai) as client:
        created = _direct(client)
        body = created.json()
        detail = client.get(f"/runs/{body['id']}").json()
        audit = client.get(f"/runs/{body['id']}/audit").json()
        listed = client.get("/runs").json()["runs"]

    assert created.status_code == 201
    assert body["kind"] == "direct" and body["status"] == "completed"
    assert body["answer"] == "Brasília."
    assert body["config"] == {
        "question": "Qual a capital do Brasil?",
        "provider": "openai",
        "requested_model": "gpt-conf",
        "max_output_tokens": Settings(_env_file=None).default_max_output_tokens_per_call,
    }
    assert body["response"]["requested_model"] == "gpt-conf"
    assert body["response"]["model"] == "gpt-conf-2026"
    assert body["response"]["model_identity_source"] == "provider_reported"
    assert body["response"]["request_provenance"]["contract_version"] == "direct_answer_v1"
    assert body["accounting"]["has_unknown_accounting_components"] is True  # sem preço conhecido
    # nada do Conselho é fabricado
    for council_only in ("final_answer", "claims", "judge_verdict", "successful_count", "quorum"):
        assert council_only not in body
    assert detail == body
    assert audit == body
    assert listed[0]["kind"] == "direct" and listed[0]["id"] == body["id"]
    assert len(openai.requests) == 1


def test_direct_provider_failure_is_a_created_failed_run_never_a_council_fallback():
    openai = ScriptedApiProvider("openai", [ProviderTimeoutError("timeout")])
    anthropic = ScriptedApiProvider("anthropic", [ok()])
    with _client(openai=openai, anthropic=anthropic) as client:
        created = _direct(client)
        detail = client.get(f"/runs/{created.json()['id']}").json()

    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "direct" and body["status"] == "failed"
    assert body["failure_stage"] == "provider"
    assert body["failure_reason"] == "timeout"
    assert body["message"] == "A chamada ao provider excedeu o tempo limite."
    assert body["response"]["error"]["type"] == "timeout"
    assert "answer" not in body
    assert detail == body
    assert anthropic.requests == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled_providers": []},
        {"enabled_providers": ["openai", "anthropic"]},
        {"question": ""},
        {"question": "   "},
        {"source_text": "um texto de referência"},
        {"kind": "Direct"},
        {"kind": "automatic"},
        {"kind": None},
    ],
)
def test_invalid_direct_requests_are_rejected_before_acceptance_and_dispatch(overrides):
    openai = ScriptedApiProvider("openai", [ok()])
    anthropic = ScriptedApiProvider("anthropic", [ok()])
    with _client(openai=openai, anthropic=anthropic) as client:
        resp = _direct(client, **overrides)
        listed = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"
    assert listed == []
    assert openai.requests == [] and anthropic.requests == []


def test_blank_source_counts_as_absent_like_in_council():
    openai = ScriptedApiProvider("openai", [ok()])
    with _client(openai=openai) as client:
        resp = _direct(client, source_text="   ")

    assert resp.status_code == 201
    assert resp.json()["kind"] == "direct"


def test_unknown_direct_provider_is_invalid_provider():
    with _client(openai=ScriptedApiProvider("openai", [ok()])) as client:
        resp = _direct(client, enabled_providers=["inexistente"])
        listed = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_provider"
    assert resp.json()["error"]["details"]["unknown_providers"] == ["inexistente"]
    assert listed == []


def test_missing_prerequisite_is_rejected_with_its_own_code_and_no_attempt():
    openai = ScriptedApiProvider("openai", [ok()], api_key="   ")
    with _client(openai=openai) as client:
        resp = _direct(client)
        listed = client.get("/runs").json()["runs"]

    assert resp.status_code == 422
    assert resp.json()["error"] == {
        "code": "provider_prerequisites_missing",
        "message": "O provider escolhido não tem a configuração local necessária.",
        "details": {"provider": "openai"},
    }
    assert openai.requests == []
    assert listed == []


def test_openapi_documents_the_direct_shapes_with_their_kind():
    with _client(openai=ScriptedApiProvider("openai", [])) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]

    for name in ("DirectCompletedRunResponse", "DirectFailedRunResponse", "DirectRunningRunResponse"):
        assert schemas[name]["properties"]["kind"]["const"] == "direct", name
    for name in ("CompletedRunResponse", "QuorumFailureRunResponse", "RunningRunResponse", "FailedRunResponse"):
        assert "kind" not in schemas[name]["properties"], name
    assert schemas["RunSummaryResponse"]["properties"]["kind"]["enum"] == ["council", "direct"]
    completed = schemas["DirectCompletedRunResponse"]["properties"]
    assert {"answer", "response", "accounting", "config"} <= set(completed)
    assert not {"final_answer", "claims", "judge_verdict"} & set(completed)
