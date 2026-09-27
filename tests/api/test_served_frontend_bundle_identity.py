"""Identidade do frontend servido em `/app`.

`app/frontend_dist/` (bundle sincronizado por `scripts/sync_frontend_dist.py`)
tem precedência sobre `frontend/dist/` (build de produção). Um bundle
sincronizado ANTIGO continua sendo servido depois de um build novo -- foi o
que fez uma validação humana ver um `/app` sem uma capacidade que o fonte,
os testes de componente e `frontend/dist/` já tinham.

Estes testes conferem a identidade ESTRUTURAL do que `/app` serve (o
`index.html` e os bytes de cada asset que ele referencia, pela camada real
de serving), nunca texto de UI dentro de JavaScript minificado."""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.frontend_serving import (
    _DEV_CHECKOUT_FRONTEND_DIST,
    _PACKAGE_BUNDLED_FRONTEND_DIST,
    DEFAULT_FRONTEND_DIST,
    _resolve_default_frontend_dist,
)
from app.config import Settings
from tests.api.helpers import make_components_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sync_frontend_dist", REPO_ROOT / "scripts" / "sync_frontend_dist.py")
sync_frontend_dist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_frontend_dist)

_ASSET_REFERENCE = re.compile(r'(?:src|href)="(/app/assets/[^"]+)"')


def _build(root: Path, js_name: str, js: str) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        f'<script type="module" src="/app/assets/{js_name}"></script>'
        '<link rel="stylesheet" href="/app/assets/index.css">'
    )
    (root / "assets" / js_name).write_text(js)
    (root / "assets" / "index.css").write_text("body{}")
    return root


def served_bundle_differences(frontend_dist: Path | None, intended: Path) -> list[str]:
    """Pede `/app` pela camada real de serving (`frontend_dist=None` = a
    resolução padrão), segue cada asset referenciado e compara com o build
    pretendido (`intended`). Lista vazia = `/app` serve exatamente ele."""
    app = create_app(
        settings=Settings(_env_file=None),
        components_factory=make_components_factory(),
        frontend_dist=frontend_dist,
    )
    problems: list[str] = []
    with TestClient(app, base_url="http://localhost") as client:
        index = client.get("/app")
        if index.status_code != 200:
            return [f"/app devolveu {index.status_code}"]
        if index.content != (intended / "index.html").read_bytes():
            problems.append("index.html servido não é o do build")
        references = _ASSET_REFERENCE.findall(index.text)
        if not references:
            problems.append("index.html servido não referencia nenhum asset")
        for reference in references:
            asset = client.get(reference)
            local = intended / reference.removeprefix("/app/")
            if asset.status_code != 200:
                problems.append(f"{reference}: {asset.status_code}")
            elif not local.is_file():
                problems.append(f"{reference}: não existe no build")
            elif asset.content != local.read_bytes():
                problems.append(f"{reference}: bytes diferentes do build")
    return problems


# ---------------------------------------------------------------------------
# Sincronização: substituição exata e conferência
# ---------------------------------------------------------------------------


def test_sync_replaces_the_bundle_exactly_including_stale_files(tmp_path):
    built = _build(tmp_path / "dist", "index-new.js", "novo")
    synced = _build(tmp_path / "frontend_dist", "index-old.js", "antigo")

    assert sync_frontend_dist.sync(built, synced) == []
    assert sorted(p.name for p in (synced / "assets").iterdir()) == ["index-new.js", "index.css"]
    assert sync_frontend_dist.bundle_differences(built, synced) == []


def test_check_reports_a_stale_synchronized_bundle(tmp_path):
    built = _build(tmp_path / "dist", "index-new.js", "novo")
    synced = _build(tmp_path / "frontend_dist", "index-old.js", "antigo")

    assert sync_frontend_dist.bundle_differences(built, synced) == [
        "só no build: assets/index-new.js",
        "só no bundle sincronizado: assets/index-old.js",
        "conteúdo diferente: index.html",
    ]


def test_check_reports_same_name_with_different_bytes_and_missing_builds(tmp_path):
    built = _build(tmp_path / "dist", "index-a.js", "um")
    synced = _build(tmp_path / "frontend_dist", "index-a.js", "outro")

    assert sync_frontend_dist.bundle_differences(built, synced) == ["conteúdo diferente: assets/index-a.js"]
    assert sync_frontend_dist.bundle_differences(built, tmp_path / "nada") == [f"sem index.html: {tmp_path / 'nada'}"]


def test_check_mode_exits_1_on_mismatch_and_0_after_sync(tmp_path, monkeypatch, capsys):
    built = _build(tmp_path / "dist", "index-new.js", "novo")
    synced = _build(tmp_path / "frontend_dist", "index-old.js", "antigo")
    monkeypatch.setattr(sync_frontend_dist, "SOURCE", built)
    monkeypatch.setattr(sync_frontend_dist, "TARGET", synced)

    assert sync_frontend_dist.main(["--check"]) == 1
    assert "index-old.js" in capsys.readouterr().err
    assert sync_frontend_dist.main([]) == 0
    assert sync_frontend_dist.main(["--check"]) == 0
    assert sync_frontend_dist.main(["--outra-coisa"]) == 2


# ---------------------------------------------------------------------------
# /app pela camada real de serving
# ---------------------------------------------------------------------------


def test_after_sync_app_serves_the_intended_build_and_its_assets(tmp_path):
    built = _build(tmp_path / "dist", "index-new.js", "novo")
    synced = tmp_path / "frontend_dist"
    sync_frontend_dist.sync(built, synced)

    assert served_bundle_differences(synced, built) == []


def test_a_stale_preferred_bundle_is_detected(tmp_path):
    """O cenário exato da falha: build novo em `frontend/dist`, bundle antigo
    em `app/frontend_dist`, que a precedência faz vencer."""
    built = _build(tmp_path / "dist", "index-new.js", "novo")
    stale = _build(tmp_path / "frontend_dist", "index-old.js", "antigo")

    served = _resolve_default_frontend_dist((stale, built))

    assert served == stale  # precedência intencional, inalterada
    assert served_bundle_differences(served, built) == [
        "index.html servido não é o do build",
        "/app/assets/index-old.js: não existe no build",
    ]


def test_a_same_named_asset_with_stale_bytes_is_detected(tmp_path):
    built = _build(tmp_path / "dist", "index-a.js", "novo")
    stale = _build(tmp_path / "frontend_dist", "index-a.js", "antigo")

    assert served_bundle_differences(stale, built) == ["/app/assets/index-a.js: bytes diferentes do build"]


def test_serving_precedence_is_unchanged():
    default = inspect.signature(_resolve_default_frontend_dist).parameters["candidates"].default
    assert default == (_PACKAGE_BUNDLED_FRONTEND_DIST, _DEV_CHECKOUT_FRONTEND_DIST)


# ---------------------------------------------------------------------------
# Este checkout: com build E bundle sincronizado presentes, /app (resolução
# padrão) precisa servir exatamente o build atual.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_DEV_CHECKOUT_FRONTEND_DIST / "index.html").is_file()
    or not (_PACKAGE_BUNDLED_FRONTEND_DIST / "index.html").is_file(),
    reason="sem build de frontend e bundle sincronizado ao mesmo tempo neste checkout",
)
def test_this_checkout_serves_the_current_frontend_build_at_app():
    assert DEFAULT_FRONTEND_DIST == _PACKAGE_BUNDLED_FRONTEND_DIST
    differences = served_bundle_differences(None, _DEV_CHECKOUT_FRONTEND_DIST)
    assert differences == [], (
        "app/frontend_dist (servido em /app) não é o build atual de frontend/dist -- rode "
        f"`python scripts/sync_frontend_dist.py`: {differences}"
    )
