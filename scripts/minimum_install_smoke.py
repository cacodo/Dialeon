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

from app.api.app import create_app
from app.bootstrap import build_app_components
from app.config import Settings


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
for package in ("fastapi", "starlette", "pydantic", "google-genai", "openai", "httpx"):
    assert f"{package}>={metadata.version(package)}" in published, package


def settings(**keys):
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        openai_api_key=keys.get("openai_api_key"),
        anthropic_api_key=keys.get("anthropic_api_key"),
        google_api_key=keys.get("google_api_key"),
    )


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

print(
    "minimum wheel smoke passed",
    {
        name: metadata.version(name)
        for name in ("fastapi", "starlette", "pydantic", "google-genai", "openai", "httpx")
    },
)
