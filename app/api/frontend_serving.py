"""
Serving do frontend -- Etapa 12.

Namespace `/app` inteiramente separado da API (`/runs`, `/providers`,
`/docs`, `/openapi.json`, `/redoc`) -- Decision Delta secao 4, sem
catch-all que possa engolir endpoints da API (o fallback SPA só existe
sob `/app`, nunca na raiz do roteador).

Se `frontend/dist/` não existir (build não rodado -- ex.: ambiente de
desenvolvimento do backend, ou suíte de testes do backend sozinha), este
módulo simplesmente não registra nada -- a API continua funcionando
normalmente sem frontend (Decision Delta secao 29: "backend/testes não
devem quebrar desnecessariamente").
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def mount_frontend(app: FastAPI, dist_dir: Path | None = None) -> bool:
    """Monta o build estático em `/app`, se ele existir. Devolve `True`
    se montou, `False` se não havia build pra servir (não é erro)."""
    dist_dir = dist_dir or DEFAULT_FRONTEND_DIST
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        return False

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/app/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    async def _root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/app")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str = "") -> FileResponse:
        return FileResponse(index_path)

    return True
