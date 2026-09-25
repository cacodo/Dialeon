"""
`create_app()` -- Etapa 11.

Testabilidade (Decision Delta secao 15): `components_factory` é o ponto
de injeção limpo -- testes passam uma factory que monta providers fake +
banco temporário, sem tocar `build_app_components` real (que constrói
providers de verdade a partir de `Settings`) e sem monkeypatch global.
`app.state.components` -- nunca um global mutável de módulo.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from app.bootstrap import AppComponents, build_app_components
from app.api.error_handlers import register_exception_handlers
from app.api.frontend_serving import mount_frontend
from app.api.openapi import install_public_contract_policy
from app.api.routes import router, run_creation_router
from app.config import Settings
from app.host_authority import HostAuthorityMiddleware
from app.version import get_product_version

ComponentsFactory = Callable[[Settings], Awaitable[AppComponents]]


def create_app(
    settings: Settings | None = None,
    components_factory: ComponentsFactory | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    """`frontend_dist` (Etapa 12, patch de continuação): mesmo padrão de
    injeção limpa de `components_factory` -- testes que precisam
    controlar exatamente qual build (ou ausência de build) está em jogo
    passam um `Path` explícito, em vez de depender do estado real de
    `frontend/dist/` no disco (que pode ou não existir dependendo de
    `npm run build` já ter rodado no ambiente). `None` usa o default
    (`DEFAULT_FRONTEND_DIST`, ver app/api/frontend_serving.py)."""
    settings = settings or Settings()
    factory: ComponentsFactory = components_factory or build_app_components

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        components = await factory(settings)
        app.state.components = components
        try:
            yield
        finally:
            await components.engine.dispose()

    # `info.version` acompanha a versão do PRODUTO (pyproject.toml) -- não
    # existe versão de API HTTP independente (ver app/version.py).
    app = FastAPI(title="LLM Council API", version=get_product_version(), lifespan=lifespan)
    # M3 (DNS rebinding) -- antes de qualquer rota: execução, histórico,
    # audit, OpenAPI e frontend estático. Ver app/host_authority.py.
    app.add_middleware(HostAuthorityMiddleware, allowed_hosts=settings.allowed_hosts)
    install_public_contract_policy(app)
    register_exception_handlers(app)
    app.include_router(router)
    app.include_router(run_creation_router)
    mount_frontend(app, dist_dir=frontend_dist)
    return app
