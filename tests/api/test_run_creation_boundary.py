"""The paid Run boundary must reject browser simple requests before dispatch."""

from __future__ import annotations

import json
from importlib.metadata import version
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.routes import RunCreationRoute
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result


BODY = {"question": "Qual a capital do Brasil?", "enabled_providers": ["openai", "anthropic"]}


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, json.dumps(BODY)),
        ({"Content-Type": "text/plain"}, json.dumps(BODY)),
        ({"Content-Type": "application/x-www-form-urlencoded"}, "question=teste"),
        ({"Content-Type": "multipart/form-data; boundary=x"}, "--x--"),
        ({"Content-Type": "text/plain", "Origin": "http://evil.example"}, json.dumps(BODY)),
        ({"Content-Type": "text/plain", "Origin": "http://localhost:5173"}, json.dumps(BODY)),
    ],
)
def test_simple_or_missing_content_type_never_accepts_run(headers, body):
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    with TestClient(app) as client:
        service = app.state.components.service
        service.run = AsyncMock(wraps=service.run)
        response = client.post("/runs", content=body, headers=headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        service.run.assert_not_called()
        assert client.get("/runs").json()["runs"] == []
        assert app.state.components.service._runner._debate_engine.calls == []


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"Content-Type": "application/json"}, "{"),
        ({"Content-Type": "application/json"}, json.dumps({**BODY, "question": ""})),
    ],
)
def test_invalid_json_or_semantics_never_accepts_run(headers, body):
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    with TestClient(app) as client:
        service = app.state.components.service
        service.run = AsyncMock(wraps=service.run)
        response = client.post("/runs", content=body, headers=headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        service.run.assert_not_called()
        assert client.get("/runs").json()["runs"] == []
        assert service._runner._debate_engine.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},
        {"Content-Type": "application/json; charset=utf-8"},
        {"Content-Type": "application/vnd.dialeon+json"},
        {"Content-Type": "application/json", "Origin": "http://localhost:5173"},
    ],
)
def test_json_with_or_without_frontend_origin_still_runs(headers):
    result = full_council_run_result()
    app = create_app(
        settings=Settings(_env_file=None),
        components_factory=make_components_factory(
            debate_result=result.debate_result,
            judge_result=result.judge_result,
            editor_result=result.editor_result,
        ),
    )
    with TestClient(app) as client:
        service = app.state.components.service
        service.run = AsyncMock(wraps=service.run)
        response = client.post("/runs", content=json.dumps(BODY), headers=headers)
        assert response.status_code == 201
        service.run.assert_awaited_once()
        assert len(client.get("/runs").json()["runs"]) == 1


def test_cross_origin_json_preflight_is_not_authorized_by_cors():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    with TestClient(app) as client:
        service = app.state.components.service
        service.run = AsyncMock(wraps=service.run)
        response = client.options(
            "/runs",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 405
        assert "access-control-allow-origin" not in response.headers
        service.run.assert_not_called()
        assert client.get("/runs").json()["runs"] == []


class UvicornRootPathProbe:
    """Mimic Uvicorn's literal `root_path + path` ASGI scope construction."""

    def __init__(self, app):
        self.app = app
        self.body_reads = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        scope = dict(scope)
        scope["path"] = scope.get("root_path", "") + scope["path"]

        async def counted_receive():
            self.body_reads += 1
            return await receive()

        return await self.app(scope, counted_receive, send)


@pytest.mark.parametrize("root_path", ["", "/", "/api", "/api/"])
@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"],
)
def test_routed_run_creation_rejects_simple_media_before_body_or_execution(root_path, content_type):
    result = full_council_run_result()
    app = create_app(
        settings=Settings(_env_file=None),
        components_factory=make_components_factory(
            debate_result=result.debate_result,
            judge_result=result.judge_result,
            editor_result=result.editor_result,
        ),
    )
    probe = UvicornRootPathProbe(app)
    with TestClient(probe, root_path=root_path) as client:
        service = app.state.components.service
        repository = app.state.components.repository
        service.run = AsyncMock(wraps=service.run)
        repository.save_accepted = AsyncMock(wraps=repository.save_accepted)
        headers = {} if content_type is None else {"Content-Type": content_type}
        response = client.post("/runs", content=json.dumps(BODY), headers=headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert probe.body_reads == 0
        service.run.assert_not_called()
        repository.save_accepted.assert_not_called()
        assert service._runner._debate_engine.calls == []
        assert client.get("/runs").json()["runs"] == []


@pytest.mark.parametrize("root_path", ["", "/", "/api", "/api/"])
@pytest.mark.parametrize(
    "content_type", ["application/json", "application/json; charset=utf-8", "application/vnd.dialeon+json"]
)
def test_routed_run_creation_accepts_json_media(root_path, content_type):
    result = full_council_run_result()
    app = create_app(
        settings=Settings(_env_file=None),
        components_factory=make_components_factory(
            debate_result=result.debate_result,
            judge_result=result.judge_result,
            editor_result=result.editor_result,
        ),
    )
    probe = UvicornRootPathProbe(app)
    with TestClient(probe, root_path=root_path) as client:
        service = app.state.components.service
        repository = app.state.components.repository
        service.run = AsyncMock(wraps=service.run)
        repository.save_accepted = AsyncMock(wraps=repository.save_accepted)
        response = client.post(
            "/runs", content=json.dumps(BODY), headers={"Content-Type": content_type}
        )
        assert response.status_code == 201
        service.run.assert_awaited_once()
        repository.save_accepted.assert_awaited_once()
        assert len(service._runner._debate_engine.calls) == 1
        assert len(client.get("/runs").json()["runs"]) == 1


@pytest.mark.parametrize("root_path", ["/", "/api/"])
def test_floor_without_app_guard_would_dispatch_headerless_json(root_path, monkeypatch):
    # This control makes the floor job discriminatory: older FastAPI parses
    # headerless JSON, so removing our route guard must make the probe unsafe.
    if tuple(int(part) for part in version("fastapi").split(".")[:2]) >= (0, 132):
        pytest.skip("FastAPI itself rejects headerless JSON in this environment")
    monkeypatch.setattr(RunCreationRoute, "get_route_handler", APIRoute.get_route_handler)
    result = full_council_run_result()
    app = create_app(
        settings=Settings(_env_file=None),
        components_factory=make_components_factory(
            debate_result=result.debate_result,
            judge_result=result.judge_result,
            editor_result=result.editor_result,
        ),
    )
    with TestClient(UvicornRootPathProbe(app), root_path=root_path) as client:
        response = client.post("/runs", content=json.dumps(BODY))
        assert response.status_code == 201
        assert len(client.get("/runs").json()["runs"]) == 1
        assert len(app.state.components.service._runner._debate_engine.calls) == 1
