"""
Versão do produto (`app/version.py`): uma única fonte prática --
`pyproject.toml` num checkout; metadados instalados numa instalação normal.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from app import version as version_module
from app.version import UNKNOWN_VERSION, get_product_version

REPO = Path(__file__).resolve().parents[1]


def test_source_tree_version_is_pyproject_version():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert get_product_version() == project["version"]


def test_source_tree_wins_over_stale_installed_metadata(monkeypatch):
    """O caso real que motivou o design: um `llm_council.egg-info/` antigo no
    diretório de trabalho faz `importlib.metadata` devolver uma versão
    defasada (ex.: `0.1.0`); a fonte canônica não pode ser sombreada."""
    monkeypatch.setattr(metadata, "version", lambda name: "0.0.1-stale")

    assert get_product_version() == tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_wheel_style_install_falls_back_to_installed_metadata(monkeypatch):
    monkeypatch.setattr(version_module, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(metadata, "version", lambda name: "9.8.7")

    assert get_product_version() == "9.8.7"


def test_unknown_when_neither_source_nor_metadata_exists(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(metadata, "version", missing)

    assert get_product_version() == UNKNOWN_VERSION


@pytest.mark.parametrize(
    "content",
    ['[project]\nname = "outro-projeto"\nversion = "3.0.0"\n', "isto não é toml [[[", '[project]\nname = "llm-council"\n'],
)
def test_foreign_or_malformed_pyproject_is_ignored(monkeypatch, tmp_path, content):
    (tmp_path / "app").mkdir()
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    monkeypatch.setattr(version_module, "__file__", str(tmp_path / "app" / "version.py"))

    assert version_module._version_from_source_tree() is None
