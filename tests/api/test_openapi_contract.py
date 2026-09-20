"""
Contrato PÚBLICO do OpenAPI gerado (política 1.x):

- `info.version` acompanha a versão do produto (`pyproject.toml`);
- RESPOSTAS são publicadas como extensíveis (clientes ignoram chaves
  desconhecidas) -- nenhum objeto de resposta é `additionalProperties: false`;
- REQUESTS continuam estritos (schema E runtime);
- `ErrorResponse` está no documento e os erros reais de cada rota estão
  declarados; o 422 falso do framework (`HTTPValidationError`) não existe.

Testa o documento GERADO, não só os modelos Pydantic. Nenhum provider.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.presentation.schemas import (
    CompletedRunAudit,
    CompletedRunResponse,
    CreateRunRequest,
    ErrorResponse,
    ProvidersResponse,
    RunListResponse,
)
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, quorum_failure_exception

REPO = Path(__file__).resolve().parents[2]
REF = "#/components/schemas/"


@pytest.fixture(scope="module")
def openapi() -> dict:
    return create_app(settings=Settings(_env_file=None), components_factory=make_components_factory()).openapi()


def _schemas(openapi) -> dict:
    return openapi["components"]["schemas"]


def _response_schema_ref(operation: dict, status: str) -> str | None:
    content = operation["responses"][status].get("content", {}).get("application/json", {})
    return content.get("schema", {}).get("$ref")


# ---------------------------------------------------------------------------
# 1. Versão
# ---------------------------------------------------------------------------


def test_openapi_info_version_is_the_product_version(openapi):
    pyproject_version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

    assert openapi["info"]["version"] == pyproject_version
    assert openapi["info"]["version"] != "0.1.0"  # o default do FastAPI, nunca intencional


# ---------------------------------------------------------------------------
# 2. Respostas extensíveis
# ---------------------------------------------------------------------------


def test_no_published_response_object_is_closed(openapi):
    closed = [name for name, schema in _schemas(openapi).items() if schema.get("additionalProperties") is False]

    assert closed == ["CreateRunRequest"]  # o ÚNICO objeto fechado é o request


def test_representative_response_schemas_are_open_including_nested_and_embedded_models(openapi):
    schemas = _schemas(openapi)

    for name in (
        "CompletedRunResponse",
        "QuorumFailureRunResponse",
        "RunningRunResponse",
        "FailedRunResponse",
        "CompletedRunAudit",
        "QuorumFailureAudit",
        "RunListResponse",
        "ProvidersResponse",
        "ErrorResponse",
        "ErrorBody",
        "ClaimPublic",  # aninhado
        "ProviderExecutionPolicy",  # modelo de domínio embutido na resposta
        "RequestProvenance",  # idem
    ):
        assert name in schemas, name
        assert "additionalProperties" not in schemas[name] or schemas[name]["additionalProperties"] is not False, name


def test_response_openness_is_publication_only_runtime_models_still_forbid_extras():
    for model in (CompletedRunResponse, CompletedRunAudit, RunListResponse, ProvidersResponse, ErrorResponse):
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_the_openapi_policy_is_idempotent_and_cached(openapi):
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())

    assert app.openapi() == app.openapi() == openapi


# ---------------------------------------------------------------------------
# 3. Requests estritos
# ---------------------------------------------------------------------------


def test_request_schema_remains_closed_in_the_document(openapi):
    schema = _schemas(openapi)["CreateRunRequest"]

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"question", "enabled_providers", "source_text"}
    assert set(schema["required"]) == {"question", "enabled_providers"}


def test_unknown_request_field_is_still_rejected_at_runtime():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())

    with TestClient(app) as client:
        response = client.post(
            "/runs", json={"question": "q", "enabled_providers": ["openai"], "surprise": True}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert CreateRunRequest.model_config.get("extra") == "forbid"


# ---------------------------------------------------------------------------
# 4/5. Erros documentados (só o comportamento REAL)
# ---------------------------------------------------------------------------


def test_error_response_is_a_documented_component(openapi):
    schemas = _schemas(openapi)

    assert "ErrorResponse" in schemas and "ErrorBody" in schemas
    assert set(schemas["ErrorBody"]["properties"]["code"]["enum"]) == {
        "invalid_provider",
        "invalid_request",
        "insufficient_quorum",
        "run_not_found",
        "internal_error",
    }


def test_the_false_framework_422_is_gone(openapi):
    assert "HTTPValidationError" not in _schemas(openapi)
    assert "ValidationError" not in _schemas(openapi)


@pytest.mark.parametrize(
    "method, path, expected",
    [
        ("get", "/providers", {"200", "500"}),
        ("post", "/runs", {"201", "409", "422", "500"}),
        ("get", "/runs", {"200", "422", "500"}),
        ("get", "/runs/{run_id}", {"200", "404", "500"}),
        ("get", "/runs/{run_id}/audit", {"200", "404", "500"}),
    ],
)
def test_each_operation_documents_exactly_its_real_status_codes(openapi, method, path, expected):
    responses = openapi["paths"][path][method]["responses"]

    assert set(responses) == expected
    for status in expected - {"200", "201"}:
        assert _response_schema_ref(openapi["paths"][path][method], status) == REF + "ErrorResponse"


def test_successful_response_schemas_are_still_documented(openapi):
    paths = openapi["paths"]

    assert _response_schema_ref(paths["/providers"]["get"], "200") == REF + "ProvidersResponse"
    assert _response_schema_ref(paths["/runs"]["get"], "200") == REF + "RunListResponse"
    for operation, union in (
        (paths["/runs"]["post"], "201"),
        (paths["/runs/{run_id}"]["get"], "200"),
    ):
        one_of = operation["responses"][union]["content"]["application/json"]["schema"]["oneOf"]
        assert {o["$ref"] for o in one_of} == {
            REF + n
            for n in ("CompletedRunResponse", "QuorumFailureRunResponse", "RunningRunResponse", "FailedRunResponse")
        }
    audit_one_of = paths["/runs/{run_id}/audit"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["oneOf"]
    assert {REF + "CompletedRunAudit", REF + "QuorumFailureAudit"} <= {o["$ref"] for o in audit_one_of}


# ---------------------------------------------------------------------------
# 6. Runtime inalterado (status + corpo dos erros documentados)
# ---------------------------------------------------------------------------


def test_missing_run_keeps_404_and_the_documented_error_body():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())

    with TestClient(app) as client:
        for path in ("/runs/nao-existe", "/runs/nao-existe/audit"):
            response = client.get(path)
            assert response.status_code == 404
            body = ErrorResponse.model_validate(response.json())
            assert body.error.code == "run_not_found" and body.error.details is None


def test_insufficient_quorum_keeps_409_and_the_documented_error_body():
    exc = quorum_failure_exception()
    app = create_app(
        settings=Settings(_env_file=None), components_factory=make_components_factory(quorum_exc=exc)
    )

    with TestClient(app) as client:
        response = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})

    assert response.status_code == 409
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "insufficient_quorum"
    assert body.error.details["run_id"] is not None


def test_invalid_provider_and_bad_query_keep_422_with_the_documented_body():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())

    with TestClient(app) as client:
        unknown = client.post("/runs", json={"question": "q", "enabled_providers": ["nope"]})
        bad_limit = client.get("/runs", params={"limit": 0})

    assert unknown.status_code == 422 and bad_limit.status_code == 422
    assert ErrorResponse.model_validate(unknown.json()).error.code == "invalid_provider"
    assert ErrorResponse.model_validate(bad_limit.json()).error.code == "invalid_request"


def test_successful_run_response_is_unchanged_and_valid():
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=Settings(_env_file=None), components_factory=factory)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        fetched = client.get(f"/runs/{created.json()['id']}")

    assert created.status_code == 201 and fetched.status_code == 200
    assert CompletedRunResponse.model_validate(created.json()).status == "completed"
    assert CompletedRunResponse.model_validate(fetched.json()).final_answer.answer_text == (
        result.final_answer.answer_text
    )
