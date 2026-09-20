"""
Versão do PRODUTO Dialeon -- única fonte prática: `pyproject.toml`.

Não existe uma versão de API HTTP independente: `info.version` do OpenAPI
acompanha a versão do produto/pacote (ver `app/api/app.py`).

Resolução (a mais simples que cobre os dois ambientes reais):

1. Checkout de código-fonte (`pyproject.toml` ao lado do pacote `app`, com
   `[project].name == "llm-council"`): lê a versão dali. É a fonte canônica e
   nunca fica defasada -- ao contrário dos metadados instalados, que num
   install editável só mudam quando o pacote é reinstalado, e podem até ser
   sombreados por um `llm_council.egg-info/` antigo no diretório de trabalho
   (`importlib.metadata` acharia `0.1.0` em vez da versão real).
2. Wheel/instalação normal (não há `pyproject.toml` ao lado do pacote): usa
   `importlib.metadata.version("llm-council")`.
3. Nenhum dos dois (ex.: código copiado solto): `UNKNOWN_VERSION`.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

DISTRIBUTION_NAME = "llm-council"
UNKNOWN_VERSION = "0.0.0+unknown"


def _version_from_source_tree() -> str | None:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if project.get("name") != DISTRIBUTION_NAME:
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version else None


def get_product_version() -> str:
    from_source = _version_from_source_tree()
    if from_source is not None:
        return from_source
    try:
        return metadata.version(DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION
