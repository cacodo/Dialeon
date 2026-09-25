"""LOW M3-A: `ALLOWED_HOSTS` pertence só à borda HTTP (validado em
`create_app`). Um valor malformado não pode derrubar comandos da CLI, que
nunca servem HTTP -- e continua impedindo a API de ser criada
(tests/api/test_host_authority.py)."""

from __future__ import annotations

import json

import pytest

from app.cli.main import main
from tests.api.test_host_authority import MALFORMED_ENV


@pytest.fixture
def offline_cli_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(key, "")


@pytest.mark.parametrize("env", MALFORMED_ENV)
def test_cli_commands_ignore_an_http_only_setting(offline_cli_env, monkeypatch, capsys, env):
    monkeypatch.setenv("ALLOWED_HOSTS", env)

    assert main(["providers", "--json"]) == 0
    providers = json.loads(capsys.readouterr().out)
    assert set(providers["providers"]) == {"openai", "anthropic", "gemini"}

    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == []
