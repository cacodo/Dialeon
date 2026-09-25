from __future__ import annotations

import json
import socket

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import _FakeRegistryProvider, make_components_factory


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_get_providers_returns_200():
    factory = make_components_factory(provider_names=("openai", "anthropic", "gemini"))
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.get("/providers")

    assert resp.status_code == 200
    assert resp.json() == {
        "providers": ["anthropic", "gemini", "openai"],
        "local_prerequisites": {"anthropic": "met", "gemini": "met", "openai": "met"},
    }


def test_get_providers_reflects_real_injected_components_not_hardcoded():
    factory = make_components_factory(provider_names=("only-one-provider",))
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        resp = client.get("/providers")

    assert resp.json()["providers"] == ["only-one-provider"]
    assert resp.json()["local_prerequisites"] == {"only-one-provider": "met"}


def test_the_providers_list_keeps_its_meaning_regardless_of_prerequisite_state():
    """`providers` continua sendo TODOS os adapters conhecidos -- um estado
    `missing`/`unknown` nunca tira um provider da lista (compatibilidade
    com clientes que só leem `providers`)."""
    factory = make_components_factory(
        provider_instances={
            "openai": _FakeRegistryProvider("m", local_prerequisite="missing"),
            "anthropic": _FakeRegistryProvider("m", local_prerequisite="met"),
            "futuro": _FakeRegistryProvider("m", local_prerequisite="unknown"),
        }
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        body = client.get("/providers").json()

    assert body["providers"] == ["anthropic", "futuro", "openai"]
    assert body["local_prerequisites"] == {"anthropic": "met", "futuro": "unknown", "openai": "missing"}


def test_get_providers_never_exposes_provider_instances_or_secrets():
    factory = make_components_factory(provider_names=("openai", "anthropic"))
    app = create_app(
        settings=Settings(_env_file=None, openai_api_key="sk-secreta-de-teste"),
        components_factory=factory,
    )

    with TestClient(app) as client:
        resp = client.get("/providers")

    assert "sk-secreta-de-teste" not in resp.text
    assert set(resp.json().keys()) == {"providers", "local_prerequisites"}
    assert all(isinstance(p, str) for p in resp.json()["providers"])


# ---------------------------------------------------------------------------
# Bootstrap REAL (providers de verdade construídos a partir de Settings, sem
# nenhuma chamada de rede): o estado vem da configuração JÁ resolvida.
# ---------------------------------------------------------------------------

SECRET = "sk-real-looking-SECRET-0123456789"


def _real_app(**keys) -> object:
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:", **keys)
    return create_app(settings=settings)


@pytest.fixture
def no_network(monkeypatch):
    """Qualquer tentativa de abrir conexão de rede falha o teste."""

    def refuse(*args, **kwargs):
        raise AssertionError("GET /providers tentou abrir uma conexão de rede")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.mark.parametrize(
    "value, expected",
    [
        (SECRET, "met"),
        (None, "missing"),
        ("", "missing"),
        ("   ", "missing"),
        ("\t\n ", "missing"),
    ],
)
def test_real_providers_report_local_prerequisites_from_resolved_settings(
    value, expected, no_network, monkeypatch
):
    # O ambiente do processo diz outra coisa -- e é ignorado: vale o Settings
    # resolvido com que o deployment foi composto.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-do-ambiente-que-nao-deve-contar")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = _real_app(openai_api_key=value, anthropic_api_key=SECRET, google_api_key=None)

    with TestClient(app) as client:
        body = client.get("/providers").json()

    assert body["providers"] == ["anthropic", "gemini", "openai"]
    assert body["local_prerequisites"] == {"anthropic": "met", "gemini": "missing", "openai": expected}


def test_prerequisite_state_is_fixed_at_composition_not_reread_per_request(no_network, monkeypatch):
    """Mudar o ambiente depois do startup não muda a resposta: a
    configuração só é relida ao reiniciar o processo."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = _real_app(openai_api_key=None, anthropic_api_key=SECRET)

    with TestClient(app) as client:
        before = client.get("/providers").json()
        monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        after = client.get("/providers").json()

    assert before["local_prerequisites"]["openai"] == "missing"
    assert after == before


def test_serialized_response_contains_no_credential_material(no_network):
    app = _real_app(openai_api_key=SECRET, anthropic_api_key="   ", google_api_key=None)

    with TestClient(app) as client:
        resp = client.get("/providers")

    text = resp.text
    for fragment in (SECRET, SECRET[:8], SECRET[-6:], str(len(SECRET))):
        assert fragment not in text
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "api_key", ".env"):
        assert name.lower() not in text.lower()
    body = json.loads(text)
    assert set(body) == {"providers", "local_prerequisites"}
    assert set(body["local_prerequisites"].values()) <= {"met", "missing", "unknown"}


def test_openapi_documents_the_three_states_only():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    schema = app.openapi()["components"]["schemas"]["ProvidersResponse"]

    assert schema["properties"]["local_prerequisites"]["additionalProperties"]["enum"] == [
        "met",
        "missing",
        "unknown",
    ]
    assert "providers" in schema["required"]
