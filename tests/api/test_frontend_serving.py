from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.frontend_serving import DEFAULT_FRONTEND_DIST, mount_frontend
from app.config import Settings
from tests.api.helpers import make_components_factory


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_no_build_does_not_break_api(tmp_path):
    """Isola explicitamente o cenário "sem build" via `frontend_dist`
    apontando pra um caminho que não existe -- independente de o
    ambiente real ter ou não `frontend/dist/` gerado (Etapa 12, patch de
    continuação: create_app ganhou `frontend_dist` injetável exatamente
    pra permitir isso)."""
    app = create_app(
        settings=_settings(),
        components_factory=make_components_factory(),
        frontend_dist=tmp_path / "sem-build",
    )

    with TestClient(app) as client:
        api_resp = client.get("/runs")
        root_resp = client.get("/")

    assert api_resp.status_code == 200
    assert root_resp.status_code == 404


def test_mount_frontend_returns_false_without_build(tmp_path):
    app = FastAPI()
    mounted = mount_frontend(app, dist_dir=tmp_path / "nao-existe")
    assert mounted is False


def test_mount_frontend_serves_index_and_assets(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>Dialeon</body></html>")
    (dist / "assets" / "app.js").write_text("console.log('ok');")

    app = create_app(
        settings=_settings(), components_factory=make_components_factory(), frontend_dist=dist
    )

    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        app_page = client.get("/app")
        deep_link = client.get("/app/runs/some-id")
        asset = client.get("/app/assets/app.js")
        api_still_works = client.get("/runs")
        providers_still_works = client.get("/providers")

    assert root.status_code in (302, 307)
    assert root.headers["location"] == "/app"
    assert app_page.status_code == 200
    assert "Dialeon" in app_page.text
    assert deep_link.status_code == 200
    assert "Dialeon" in deep_link.text
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert api_still_works.status_code == 200
    assert providers_still_works.status_code == 200


def test_docs_and_openapi_not_swallowed_by_spa_fallback(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>")

    app = create_app(
        settings=_settings(), components_factory=make_components_factory(), frontend_dist=dist
    )

    with TestClient(app) as client:
        openapi = client.get("/openapi.json")
        docs = client.get("/docs")

    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == "LLM Council API"
    assert docs.status_code == 200
    assert "SPA" not in docs.text


@pytest.mark.skipif(
    not DEFAULT_FRONTEND_DIST.exists(),
    reason="frontend/dist não existe -- rode 'npm run build' em frontend/ antes deste teste",
)
def test_real_production_build_integration():
    """Integração real, não fixture artificial (Decision Delta, patch
    final, item 2): usa o `frontend/dist` GENUÍNO gerado por `npm run
    build`, servido pelo FastAPI real via `create_app()` -- sem `dist_dir`
    customizado, sem HTML fabricado no teste."""
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        app_page = client.get("/app")
        history_page = client.get("/app/runs")
        deep_link = client.get("/app/runs/some-real-id")
        api_runs = client.get("/runs")
        api_providers = client.get("/providers")
        docs = client.get("/docs")
        openapi = client.get("/openapi.json")

    assert root.status_code in (302, 307)
    assert root.headers["location"] == "/app"

    assert app_page.status_code == 200
    assert "text/html" in app_page.headers["content-type"]

    assert history_page.status_code == 200
    assert history_page.text == app_page.text  # mesmo index.html, SPA fallback

    assert deep_link.status_code == 200
    assert deep_link.text == app_page.text  # reload direto funciona

    assert api_runs.status_code == 200
    assert api_providers.status_code == 200

    assert docs.status_code == 200
    assert "Dialeon" not in docs.text
    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == "LLM Council API"

    # os assets referenciados pelo index.html real precisam existir e
    # ser servidos com content-type correto
    import re

    js_paths = re.findall(r'src="(/app/assets/[^"]+\.js)"', app_page.text)
    css_paths = re.findall(r'href="(/app/assets/[^"]+\.css)"', app_page.text)
    assert js_paths, "index.html real não referencia nenhum asset .js"
    assert css_paths, "index.html real não referencia nenhum asset .css"

    with TestClient(app) as client:
        for path in js_paths:
            resp = client.get(path)
            assert resp.status_code == 200
            assert "javascript" in resp.headers["content-type"]
        for path in css_paths:
            resp = client.get(path)
            assert resp.status_code == 200
            assert "css" in resp.headers["content-type"]


def test_mount_frontend_uses_default_dist_when_none_passed():
    """Confirma que `mount_frontend(app)` sem argumento usa
    DEFAULT_FRONTEND_DIST -- resolvido para o bundle empacotado
    (`app/frontend_dist/`) se presente (instalação de release), senão pra
    `frontend/dist/` relativo à raiz do repo (checkout de desenvolvimento,
    o caso deste teste) -- não precisa de dist_dir explícito em produção."""
    if not DEFAULT_FRONTEND_DIST.exists():
        pytest.skip("frontend/dist não existe -- rode 'npm run build' em frontend/ antes deste teste")

    app = FastAPI()
    mounted = mount_frontend(app)
    assert mounted is True
