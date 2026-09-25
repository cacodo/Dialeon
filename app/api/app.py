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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.bootstrap import AppComponents, build_app_components
from app.api.error_handlers import register_exception_handlers
from app.api.frontend_serving import mount_frontend
from app.api.openapi import install_public_contract_policy
from app.api.routes import router
from app.config import Settings
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

    @app.middleware("http")
    async def require_json_for_run_creation(request: Request, call_next):
        # A browser can send text/plain, form data, or no Content-Type as a
        # simple/no-cors POST. Reject those before FastAPI parses the body or
        # the service can accept a potentially paid Run. JSON requires a
        # browser preflight across origins; this app does not enable CORS.
        path = request.scope["path"]
        root_path = request.scope.get("root_path", "").rstrip("/")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path) :]
        if request.method == "POST" and path == "/runs":
            content_type = (
                request.headers.get("content-type", "").partition(";")[0].strip().lower()
            )
            if content_type != "application/json" and not (
                content_type.startswith("application/") and content_type.endswith("+json")
            ):
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "invalid_request",
                            "message": "Content-Type JSON obrigatório.",
                            "details": None,
                        }
                    },
                )
        return await call_next(request)

    install_public_contract_policy(app)
    register_exception_handlers(app)
    app.include_router(router)
    mount_frontend(app, dist_dir=frontend_dist)
    return app
