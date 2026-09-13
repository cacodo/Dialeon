from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient

from app import bootstrap
from app.api.app import create_app
from app.bootstrap import ConfigurationError
from app.config import Settings
from tests.api.helpers import make_components_factory


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_bootstrap_module_does_not_import_fastapi():
    source = inspect.getsource(bootstrap)
    tree = ast.parse(source)
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("fastapi") for m in imported_modules)


def test_api_app_module_does_not_duplicate_wiring():
    import app.api.app as api_app_module

    source = inspect.getsource(api_app_module)
    assert "build_all_providers(" not in source
    assert "DebateEngine(" not in source
    assert "SingleJudge(" not in source
    assert "CouncilRunner(" not in source
    assert "from app.bootstrap import" in source


def test_api_routes_module_does_not_duplicate_wiring():
    import app.api.routes as routes_module

    source = inspect.getsource(routes_module)
    assert "build_all_providers(" not in source
    assert "DebateEngine(" not in source
    assert "CouncilRunner(" not in source


def test_create_app_and_lifespan_still_work():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200


def test_engine_still_disposed_after_relocation():
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncEngine

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with patch.object(AsyncEngine, "dispose", autospec=True, wraps=AsyncEngine.dispose) as mocked:
        with TestClient(app):
            assert mocked.call_count == 0
        assert mocked.call_count == 1


def test_invalid_internal_config_still_fails_as_configuration_error_not_http():
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, default_judge_provider="provider-que-nao-existe")
    app = create_app(settings=settings, components_factory=build_app_components)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


def test_invalid_source_analyzer_config_fails_as_configuration_error_not_http():
    """T02.4 repair (MEDIUM, teste B) -- `default_source_analyzer_provider`
    inválido precisa ser rejeitado no MESMO ponto/contrato que
    `default_judge_provider`/`default_claim_processor_provider`/
    `default_editor_provider` já eram (ver
    `_validate_internal_provider_config`, app/bootstrap.py) -- ANTES do
    startup completar, nunca só quando Source Analysis rodar em runtime
    profundo. Achado da revisão independente: esta chave estava ausente
    da checagem original."""
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, default_source_analyzer_provider="provider-que-nao-existe")
    app = create_app(settings=settings, components_factory=build_app_components)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


@pytest.mark.asyncio
async def test_engine_disposed_when_failure_occurs_after_creation_before_return(monkeypatch):
    """Stage 14 (segunda revisão): se `init_db()` (ou qualquer passo
    entre a criação do engine e o `return AppComponents(...)`) falhar, o
    engine já criado precisa ser descartado -- senão fica órfão até o
    GC decidir (SQLAlchemy emite warning; em backends não-SQLite pode
    significar uma conexão de rede genuinamente aberta). A exceção
    ORIGINAL precisa continuar propagando intacta, não substituída por
    outra coisa."""
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.bootstrap import build_app_components

    async def _broken_init_db(engine):
        raise RuntimeError("falha real durante init_db, engine já existe")

    monkeypatch.setattr("app.bootstrap.init_db", _broken_init_db)

    with patch.object(AsyncEngine, "dispose", autospec=True, wraps=AsyncEngine.dispose) as mocked:
        with pytest.raises(RuntimeError, match="falha real durante init_db"):
            await build_app_components(_settings())
        assert mocked.call_count == 1
