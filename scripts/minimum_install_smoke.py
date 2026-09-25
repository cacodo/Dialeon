"""Offline smoke for a wheel installed outside the source tree at dependency floors.

Run from another working directory, with this repository absent from PYTHONPATH.
"""

import asyncio
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from unittest.mock import AsyncMock

import app
from fastapi.testclient import TestClient
from packaging.requirements import Requirement
from packaging.version import Version

from app.api.app import create_app
from app.bootstrap import build_app_components
from app.config import Settings
from app.orchestrator.config import RunConfig
from app.storage import models as _storage_models  # noqa: F401 - import maps the real ORM


SOURCE_ROOT = Path(__file__).resolve().parent.parent
assert not Path(app.__file__).resolve().is_relative_to(SOURCE_ROOT), app.__file__
subprocess.run(
    [str(Path(sys.executable).parent / "dialeon"), "--help"],
    check=True,
    capture_output=True,
    text=True,
)
with (SOURCE_ROOT / "pyproject.toml").open("rb") as project_file:
    declared = set(tomllib.load(project_file)["project"]["dependencies"])
published = {
    requirement
    for requirement in metadata.requires("llm-council")
    if "; extra ==" not in requirement
}
assert published == declared, (published - declared, declared - published)
minimum_pins = [
    line.strip()
    for line in (SOURCE_ROOT / "scripts/minimum-direct-requirements.txt").read_text().splitlines()
    if line.strip() and not line.startswith("#")
]
assert len(minimum_pins) == len(declared)
declared_requirements = {Requirement(value).name: Requirement(value) for value in declared}
for pin in minimum_pins:
    pinned = Requirement(pin)
    required = declared_requirements[pinned.name]
    assert pinned.extras == required.extras, pin
    assert len(required.specifier) == len(pinned.specifier) == 1, pin
    floor = next(iter(pinned.specifier))
    declared_floor = next(iter(required.specifier))
    assert floor.operator == "==" and declared_floor.operator == ">=", pin
    assert Version(floor.version) == Version(declared_floor.version), pin
    assert Version(metadata.version(pinned.name)) == Version(floor.version), pin


def settings(**keys):
    values = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "openai_api_key": None,
        "anthropic_api_key": None,
        "google_api_key": None,
    }
    values.update(keys)
    return Settings(_env_file=None, **values)


configured = settings(orchestrator_round_dispatch_timeout_seconds=42.0)
assert configured.orchestrator_round_dispatch_timeout_seconds == 42.0
assert RunConfig.from_settings(
    configured, question="q", enabled_providers=["openai", "anthropic"]
).round_dispatch_timeout_seconds == 42.0


async def factory_smoke():
    for keys in (
        {},
        {"openai_api_key": "dummy"},
        {"anthropic_api_key": "dummy"},
        {"google_api_key": "dummy"},
    ):
        components = await build_app_components(settings(**keys))
        try:
            assert set(components.providers) == {"openai", "anthropic", "gemini"}
            assert all(provider.default_model for provider in components.providers.values())
        finally:
            await components.engine.dispose()


asyncio.run(factory_smoke())

with TestClient(create_app(settings=settings())) as client:
    assert client.get("/providers").status_code == 200
    service = client.app.state.components.service
    service.run = AsyncMock(wraps=service.run)
    body = {"question": "q", "enabled_providers": ["openai", "anthropic"]}
    raw = json.dumps(body)
    for headers in (
        {},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/x-www-form-urlencoded"},
    ):
        response = client.post("/runs", content=raw, headers=headers)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_request"
    assert service.run.await_count == 0
    assert client.get("/runs").json()["runs"] == []

    response = client.post("/runs", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    response = client.post("/runs", json={**body, "question": ""})
    assert response.status_code == 422, response.text
    assert service.run.await_count == 0

    response = client.post("/runs", json={**body, "enabled_providers": ["unknown"]})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_provider"
    assert service.run.await_count == 1
    assert client.get("/runs").json()["runs"] == []


class UvicornRootPathScope:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["path"] = scope.get("root_path", "") + scope["path"]
        await self.application(scope, receive, send)


for root_path in ("", "/", "/api", "/api/"):
    application = create_app(settings=settings())
    with TestClient(UvicornRootPathScope(application), root_path=root_path) as client:
        service = application.state.components.service
        service.run = AsyncMock(side_effect=AssertionError("rejected request reached service"))
        response = client.post("/runs", content=raw)
        assert response.status_code == 422, (root_path, response.text)
        service.run.assert_not_called()
        assert client.get("/runs").json()["runs"] == []

print(
    "minimum wheel smoke passed",
    {
        name: metadata.version(name)
        for name in ("fastapi", "starlette", "pydantic", "pydantic-settings", "SQLAlchemy", "google-genai", "openai", "httpx")
    },
)
