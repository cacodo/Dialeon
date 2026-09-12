"""
Tipos de retorno do `CouncilRepository` -- Etapa 10.

Um run persistido é UM entre dois formatos, nunca um forçado a fingir ser o
outro (Decision Delta §10: "não force uma failed run a fingir ser
CouncilRunResult"): `CompletedRunRecord` (chegou ao Editor) ou
`QuorumFailureRecord` (abortou em `InsufficientQuorumError`). `status`
discrimina os dois sem precisar de `isinstance`/hierarquia de exceptions.

Nenhum dos dois exige identidade Python (`is`) com o que foi originalmente
persistido -- só equivalência semântica dos dados.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.council.result import CouncilRunResult
from app.orchestrator.config import RunConfig
from app.orchestrator.result import RoundResult

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class CompletedRunRecord(BaseModel):
    """Um run que chegou até o Editor -- reconstrução semanticamente
    equivalente ao `CouncilRunResult` original, montada a partir dos
    registros-fato no banco (nunca de um cache de totais)."""

    model_config = _CONFIG

    status: Literal["completed"] = "completed"
    council_run_result: CouncilRunResult


class QuorumFailureRecord(BaseModel):
    """Um run que abortou em `InsufficientQuorumError` antes de qualquer
    processamento de claims -- nunca tem claims/attempts/verdict/resposta
    final, porque o pipeline não chegou lá."""

    model_config = _CONFIG

    status: Literal["insufficient_quorum"] = "insufficient_quorum"
    id: str
    started_at: datetime
    failed_at: datetime
    run_config: RunConfig
    successful_count: int
    total_providers: int
    min_to_return: int
    round_result: RoundResult


PersistedRun = CompletedRunRecord | QuorumFailureRecord


class RunSummary(BaseModel):
    """Visão leve para listagem -- sem reconstruir a árvore inteira de
    cada run (`list_runs` não deveria custar o mesmo que N×`get_run`)."""

    model_config = _CONFIG

    id: str
    status: Literal["completed", "insufficient_quorum"]
    started_at: datetime
    ended_at: datetime  # completed_at (sucesso) ou failed_at (quorum failure)
