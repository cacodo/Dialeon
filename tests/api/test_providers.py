from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_get_providers_returns_200():
    factory = make_components_factory(provider_names=("openai", "anthropic", "gemini"))
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.get("/providers")

    assert resp.status_code == 200
    assert resp.json() == {"providers": ["anthropic", "gemini", "openai"]}


def test_get_providers_reflects_real_injected_components_not_hardcoded():
    factory = make_components_factory(provider_names=("only-one-provider",))
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.get("/providers")

    assert resp.json() == {"providers": ["only-one-provider"]}


def test_get_providers_never_exposes_provider_instances_or_secrets():
    factory = make_components_factory(provider_names=("openai", "anthropic"))
    app = create_app(
        settings=Settings(_env_file=None, openai_api_key="sk-secreta-de-teste"),
        components_factory=factory,
    )

    with TestClient(app) as client:
        resp = client.get("/providers")

    assert "sk-secreta-de-teste" not in resp.text
    assert set(resp.json().keys()) == {"providers"}
    assert all(isinstance(p, str) for p in resp.json()["providers"])
