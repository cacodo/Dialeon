"""The paid Run boundary must reject browser simple requests before dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
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


def test_json_guard_applies_when_app_is_mounted_under_root_path():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    with TestClient(app, root_path="/api") as client:
        service = app.state.components.service
        service.run = AsyncMock(wraps=service.run)
        response = client.post("/api/runs", content=json.dumps(BODY))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        service.run.assert_not_called()
        assert client.get("/api/runs").json()["runs"] == []
