"""Offline smoke for a wheel installed outside the source tree at dependency floors.

Run from another working directory, with this repository absent from PYTHONPATH.
"""

import asyncio
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from unittest.mock import AsyncMock

import app
import httpx
import uvicorn
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.bootstrap import build_app_components
from app.config import Settings
from app.orchestrator.config import RunConfig
from app.storage import models as _storage_models  # noqa: F401 - import maps the real ORM


# Exercise the shipped Host policy (loopback only), not a developer override.
os.environ.pop("ALLOWED_HOSTS", None)

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

# Stdlib only: the declared requirements are all `name[extras]>=X.Y[.Z]` and
# the pins `name[extras]==X.Y[.Z]`; anything else fails loudly here.
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[([a-z0-9,_-]+)\])?(==|>=)([0-9]+(?:\.[0-9]+)*)$")


def parse_requirement(text):
    match = _REQUIREMENT.match(text)
    assert match, f"unsupported requirement syntax: {text!r}"
    name, extras, operator, version = match.groups()
    return name.lower(), frozenset((extras or "").split(",")) - {""}, operator, release(version)


def release(version):
    assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version), f"not a plain release: {version!r}"
    parts = [int(part) for part in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


declared_requirements = {parse_requirement(value)[0]: parse_requirement(value) for value in declared}
for pin in minimum_pins:
    name, extras, operator, floor = parse_requirement(pin)
    _, required_extras, required_operator, declared_floor = declared_requirements[name]
    assert extras == required_extras, pin
    assert operator == "==" and required_operator == ">=", pin
    assert floor == declared_floor, pin
    assert release(metadata.version(name)) == floor, pin


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

with TestClient(create_app(settings=settings()), base_url="http://localhost") as client:
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
    with TestClient(
        UvicornRootPathScope(application), root_path=root_path, base_url="http://localhost"
    ) as client:
        service = application.state.components.service
        service.run = AsyncMock(side_effect=AssertionError("rejected request reached service"))
        response = client.post("/runs", content=raw)
        assert response.status_code == 422, (root_path, response.text)
        service.run.assert_not_called()
        assert client.get("/runs").json()["runs"] == []


async def uvicorn_smoke():
    """Real uvicorn serving the documented target
    (`uvicorn app.api.app:create_app --factory`) on an ephemeral loopback
    port: starts, answers, rejects a simple POST before any Run exists, and
    shuts down cleanly. No provider can be called: keys are blank and the
    database is in memory."""
    os.environ.update(
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        GOOGLE_API_KEY="",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            "app.api.app:create_app", factory=True, host="127.0.0.1", port=0, log_level="warning"
        )
    )
    serving = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(15):
            while not server.started:
                assert not serving.done(), "uvicorn exited during startup"
                await asyncio.sleep(0.05)
        host, port = server.servers[0].sockets[0].getsockname()[:2]
        assert host == "127.0.0.1", host
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            providers = await client.get("/providers")
            assert providers.status_code == 200, providers.text
            assert set(providers.json()["providers"]) == {"openai", "anthropic", "gemini"}
            rejected = await client.post(
                "/runs", content='{"question": "q"}', headers={"Content-Type": "text/plain"}
            )
            assert rejected.status_code == 422, rejected.text
            assert (await client.get("/runs")).json()["runs"] == []
            # M3: DNS rebinding -- a page's own hostname never reaches a route.
            rebound = await client.get(
                "/runs", headers={"Host": f"attacker.example:{port}", "Origin": f"http://attacker.example:{port}"}
            )
            assert (rebound.status_code, rebound.text) == (400, "Invalid host header"), rebound.text
    finally:
        server.should_exit = True
        async with asyncio.timeout(15):
            await serving
    assert serving.exception() is None


asyncio.run(uvicorn_smoke())

print(
    "minimum wheel smoke passed",
    {
        name: metadata.version(name)
        for name in ("fastapi", "starlette", "uvicorn", "pydantic", "pydantic-settings", "SQLAlchemy", "google-genai", "openai", "httpx")
    },
)
