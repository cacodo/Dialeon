from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.presentation import schemas as api_schemas
from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, quorum_failure_exception, run_config, now


def _settings_with_fake_keys() -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-super-secreta-openai",
        anthropic_api_key="sk-super-secreta-anthropic",
        google_api_key="sk-super-secreta-google",
    )


async def _seed_success(components, result) -> str:
    await components.repository.save_success(result)
    return result.id


async def _seed_quorum_failure(components, exc) -> str:
    return await components.repository.save_quorum_failure(
        exc, run_config=run_config(), started_at=now(), failed_at=now()
    )


def test_no_api_key_appears_in_create_run_response():
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings_with_fake_keys(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )

    body_text = resp.text
    assert "sk-super-secreta-openai" not in body_text
    assert "sk-super-secreta-anthropic" not in body_text
    assert "sk-super-secreta-google" not in body_text


def test_no_api_key_appears_in_audit_response():
    result = full_council_run_result()
    app = create_app(
        settings=_settings_with_fake_keys(), components_factory=make_components_factory()
    )

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body_text = resp.text
    assert "sk-super-secreta-openai" not in body_text
    assert "sk-super-secreta-anthropic" not in body_text
    assert "sk-super-secreta-google" not in body_text


def test_no_api_key_appears_in_error_responses():
    exc = quorum_failure_exception()
    factory = make_components_factory(quorum_exc=exc)
    app = create_app(settings=_settings_with_fake_keys(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )

    assert "sk-super-secreta" not in resp.text


def test_settings_is_never_a_response_schema():
    """Nenhum schema HTTP público deve ser (ou envolver diretamente)
    Settings -- confirmado estruturalmente, não só por não aparecer nos
    testes acima."""
    import app.config

    public_schemas = [
        obj
        for name, obj in vars(api_schemas).items()
        if inspect.isclass(obj) and issubclass(obj, __import__("pydantic").BaseModel)
    ]
    assert app.config.Settings not in public_schemas
    for schema in public_schemas:
        for field in schema.model_fields.values():
            assert field.annotation is not app.config.Settings


def test_500_does_not_leak_internal_message():
    factory = make_components_factory()  # fakes sem result nem exc -> AssertionError real
    app = create_app(settings=_settings_with_fake_keys(), components_factory=factory)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/runs", json={"question": "pergunta", "enabled_providers": ["openai"]})

    assert resp.status_code == 500
    body_text = resp.text
    assert "Traceback" not in body_text
    assert "assert" not in body_text.lower()
    assert "File \"" not in body_text


def test_api_schemas_are_not_trivial_aliases_of_domain_or_storage():
    """As classes de schemas HTTP não podem SER as próprias classes de
    domínio/storage reexportadas (Decision Delta secao 16/revisao
    final) -- confirma que são tipos genuinamente distintos."""
    from app.council.result import CouncilRunResult
    from app.storage.records import CompletedRunRecord, QuorumFailureRecord, RunSummary

    domain_and_storage_types = {CouncilRunResult, CompletedRunRecord, QuorumFailureRecord, RunSummary}

    public_schemas = {
        obj
        for name, obj in vars(api_schemas).items()
        if inspect.isclass(obj) and issubclass(obj, __import__("pydantic").BaseModel)
    }

    assert public_schemas.isdisjoint(domain_and_storage_types)


def test_run_summary_response_is_a_distinct_type_from_storage_run_summary():
    from app.storage.records import RunSummary

    assert api_schemas.RunSummaryResponse is not RunSummary
