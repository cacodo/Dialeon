"""
Testes de import-safety e comportamento real com env inválida -- Stage
14 (segunda revisão final).

Todos rodam em SUBPROCESS de verdade (`sys.executable -c "..."`), nunca
`monkeypatch` dentro do processo pytest -- o bug original (`settings =
Settings()` a nível de módulo em `app/config.py`) só se manifesta
durante o IMPORT do módulo, que já aconteceu uma vez no processo pytest
antes de qualquer teste rodar. Um teste com monkeypatch depois do
import não teria detectado isso -- só um processo novo, que ainda não
importou nada, reproduz o bug real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_python(code: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


_BAD_ENV = {"DEFAULT_MAX_COST_USD": "not-a-number"}


# ---------------------------------------------------------------------------
# Seção 3 -- import-safety
# ---------------------------------------------------------------------------


def test_import_app_config_never_instantiates_settings_with_invalid_env():
    result = _run_python("import app.config", _BAD_ENV)
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "ValidationError" not in result.stderr


def test_import_app_cli_main_never_instantiates_settings_with_invalid_env():
    result = _run_python("import app.cli.main", _BAD_ENV)
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    assert "ValidationError" not in result.stderr


def test_app_config_has_no_module_level_settings_singleton():
    """Confirma a remoção em si -- não algum outro global equivalente
    reintroduzido com nome diferente."""
    result = _run_python(
        "import app.config; assert not hasattr(app.config, 'settings'), "
        "'singleton module-level settings ainda existe'",
        _BAD_ENV,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"


# ---------------------------------------------------------------------------
# Seção 4 -- comportamento real do entrypoint com config inválida
# ---------------------------------------------------------------------------


def test_cli_with_invalid_env_human_mode_exit_1_generic_no_traceback():
    result = _run_python(
        "import sys; from app.cli.main import main; sys.exit(main(['providers']))", _BAD_ENV
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "Erro interno inesperado."
    assert "Traceback" not in result.stderr
    assert "ValidationError" not in result.stderr
    assert "not-a-number" not in result.stderr
    assert "File \"" not in result.stderr


def test_cli_with_invalid_env_json_mode_clean_json_only():
    result = _run_python(
        "import sys; from app.cli.main import main; sys.exit(main(['providers', '--json']))",
        _BAD_ENV,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    body = json.loads(result.stderr)  # levanta se houver qualquer texto extra
    assert body["error"]["code"] == "internal_error"
    assert "not-a-number" not in result.stderr
    assert "ValidationError" not in result.stderr


def test_cli_with_valid_env_still_works_in_fresh_process(tmp_path):
    """Controle negativo -- confirma que o teste acima falha por causa
    da env inválida especificamente, não por algum problema geral do
    subprocess/entrypoint."""
    db_path = tmp_path / "smoke.db"
    env = {"DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    result = _run_python(
        "import sys; from app.cli.main import main; sys.exit(main(['providers']))", env
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
