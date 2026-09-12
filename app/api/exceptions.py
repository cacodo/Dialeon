"""
Exceções da boundary HTTP -- Etapa 11.

Nunca confundir com exceções do domínio/pipeline (`InsufficientQuorumError`,
`ValueError` de provider desconhecido em runtime profundo, etc.) -- essas
são específicas da camada HTTP, levantadas ANTES de qualquer chamada ao
`CouncilExecutionService`, validando o request em si.

Etapa 14 (T19A.1): `InvalidProviderError` foi removida daqui -- a
validação de provider desconhecido não é mais HTTP-specific, mora em
`app.application.errors.UnknownProviderError` (compartilhada com a CLI).
`error_handlers.py` traduz `UnknownProviderError` pra HTTP diretamente.
"""

from __future__ import annotations


class RunNotFoundError(Exception):
    """`run_id` não existe em nenhuma das duas tabelas raiz
    (council_runs/quorum_failures)."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"run não encontrada: {run_id!r}")
