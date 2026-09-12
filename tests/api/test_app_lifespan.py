from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.bootstrap import ConfigurationError
from app.config import Settings
from tests.api.helpers import make_components_factory


def test_app_starts_and_stops_with_test_components():
    settings = Settings(_env_file=None)
    factory = make_components_factory()
    app = create_app(settings=settings, components_factory=factory)

    with TestClient(app) as client:
        resp = client.get("/runs")
        assert resp.status_code == 200


def test_lifespan_disposes_engine_on_shutdown():
    from unittest.mock import AsyncMock, patch

    from sqlalchemy.ext.asyncio import AsyncEngine

    settings = Settings(_env_file=None)
    factory = make_components_factory()
    app = create_app(settings=settings, components_factory=factory)

    with patch.object(AsyncEngine, "dispose", autospec=True, wraps=AsyncEngine.dispose) as mocked:
        with TestClient(app):
            assert mocked.call_count == 0  # ainda não chamado dentro do bloco
        assert mocked.call_count == 1  # chamado exatamente uma vez no shutdown


def test_invalid_internal_provider_config_fails_at_startup():
    """Settings apontando pra um judge_provider que não existe no
    registry construído e erro de CONFIGURACAO da aplicacao -- deve
    falhar no lifespan, nunca virar 422 de request (Decision Delta
    secao 4)."""
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, default_judge_provider="provider-que-nao-existe")
    app = create_app(settings=settings, components_factory=build_app_components)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass
