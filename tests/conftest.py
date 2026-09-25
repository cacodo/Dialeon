"""Configuração de processo compartilhada pela suíte.

M3 (autoridade de Host): o `TestClient` do Starlette envia `Host: testserver`,
que nunca faz parte do default de produção (só loopback). A suíte declara
esse nome de teste explicitamente, como qualquer operador declararia um
hostname próprio -- em vez de enfraquecer o default. Os testes do próprio
default (tests/api/test_host_authority.py) removem esta variável.
"""

from __future__ import annotations

import os

TEST_ALLOWED_HOSTS = "testserver,localhost,127.0.0.1,::1"


def pytest_configure(config):  # noqa: ARG001
    os.environ.setdefault("ALLOWED_HOSTS", TEST_ALLOWED_HOSTS)
