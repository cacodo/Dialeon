"""
Serving do frontend -- Etapa 12; localização do build resolvida a partir de
dois locais candidatos desde a Release Packaging Contract V1.

Namespace `/app` inteiramente separado da API (`/runs`, `/providers`,
`/docs`, `/openapi.json`, `/redoc`) -- Decision Delta secao 4, sem
catch-all que possa engolir endpoints da API (o fallback SPA só existe
sob `/app`, nunca na raiz do roteador).

Se nenhum build existir em nenhum dos dois locais candidatos (build não
rodado -- ex.: ambiente de desenvolvimento do backend, ou suíte de testes
do backend sozinha), este módulo simplesmente não registra nada -- a API
continua funcionando normalmente sem frontend (Decision Delta secao 29:
"backend/testes não devem quebrar desnecessariamente").

Release Packaging Contract V1 -- `DEFAULT_FRONTEND_DIST` tenta, nesta
ordem:

1. `_PACKAGE_BUNDLED_FRONTEND_DIST` (`app/frontend_dist/`, sibling deste
   módulo dentro do PRÓPRIO pacote `app`) -- é aqui que o frontend
   compilado é empacotado dentro do wheel/sdist instalado (ver
   `[tool.setuptools.package-data]`, pyproject.toml, e
   `scripts/sync_frontend_dist.py`, que popula este diretório ANTES do
   build de release). Único local que existe de fato num artefato Python
   instalado -- `frontend/` (código-fonte) nunca é empacotado.
2. `_DEV_CHECKOUT_FRONTEND_DIST` (`frontend/dist/`, sibling de `app/` na
   raiz do repositório) -- fluxo de desenvolvimento de sempre (`npm run
   build` direto em `frontend/`), preservado sem nenhuma mudança de
   comportamento.

Nenhum dos dois caminhos é resolvido via `Path` absoluto de máquina de
desenvolvedor -- ambos são relativos a `Path(__file__)` deste módulo,
válidos tanto em checkout de fonte quanto em instalação de pacote. Um
`dist_dir` passado explicitamente (ver `mount_frontend`/`create_app`)
nunca consulta esses candidatos -- continua a mesma injeção direta de
sempre, usada pelos testes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

_PACKAGE_BUNDLED_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend_dist"
_DEV_CHECKOUT_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _resolve_default_frontend_dist(
    candidates: tuple[Path, ...] = (_PACKAGE_BUNDLED_FRONTEND_DIST, _DEV_CHECKOUT_FRONTEND_DIST),
) -> Path:
    """Primeiro candidato com um `index.html` real vence. Se nenhum
    candidato tiver build nenhum, devolve o ÚLTIMO candidato (o caminho de
    desenvolvimento histórico) inalterado -- `mount_frontend` já trata
    "caminho sem build" graciosamente (devolve `False`, não levanta), então
    esta função nunca precisa sinalizar ausência de forma especial; ela só
    decide QUAL caminho checar primeiro quando mais de um pode existir."""
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[-1]


DEFAULT_FRONTEND_DIST = _resolve_default_frontend_dist()


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
