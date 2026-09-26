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
from app.direct.models import DirectRunConfig, DirectRunStatus
from app.models.domain import ModelResponse
from app.models.provider_models import DefaultModelAuthoritySnapshot, ProviderExecutionPolicy
from app.orchestrator.config import RunConfig
from app.orchestrator.result import RoundResult

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Repair M2 -- estágio de uma falha terminal de accepted run: exceção do
# runner ("execution") vs. falha do `save_success` atômico depois de uma
# execução que terminou ("terminal_persistence").
FailureStage = Literal["execution", "terminal_persistence"]


class CompletedRunRecord(BaseModel):
    """Um run que chegou até o Editor -- reconstrução semanticamente
    equivalente ao `CouncilRunResult` original, montada a partir dos
    registros-fato no banco (nunca de um cache de totais).

    `provider_execution_policy` (T02.2) -- SIBLING de
    `council_run_result`, nunca um campo de `CouncilRunResult`: a
    política de execução de provider é uma autoridade de deployment
    inteiramente distinta de `RunConfig`/`CouncilRunner`, que nunca a
    conhecem (ver `app.models.provider_models.ProviderExecutionPolicy`).
    `None` só pra runs persistidos antes desta coluna existir."""

    model_config = _CONFIG

    status: Literal["completed"] = "completed"
    council_run_result: CouncilRunResult
    provider_execution_policy: ProviderExecutionPolicy | None = None
    # Provider Default-Model Snapshot Provenance V1 -- SIBLING de
    # `provider_execution_policy` acima, mesma disciplina exata: `None`
    # só pra runs persistidos antes desta coluna existir.
    default_model_authority_snapshot: DefaultModelAuthoritySnapshot | None = None


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
    # T02.2 -- ver docstring de CompletedRunRecord.provider_execution_policy.
    provider_execution_policy: ProviderExecutionPolicy | None = None
    # Provider Default-Model Snapshot Provenance V1 -- SIBLING de
    # `provider_execution_policy` acima, mesma disciplina exata: `None`
    # só pra runs persistidos antes desta coluna existir.
    default_model_authority_snapshot: DefaultModelAuthoritySnapshot | None = None


class AcceptedRunRecord(BaseModel):
    """T02.4 -- um run aceito (validação de provider já passou, id/
    started_at já autoritativos) que ainda NÃO tem registro terminal
    canônico. `status="running"`: honestamente incompleto -- em
    andamento, ou processo morreu antes de terminar (as duas situações
    são indistinguíveis por design, ver docstring de `AcceptedRunRow`;
    fora de escopo deste slice inventar como diferenciá-las). `status=
    "failed"`: exceção inesperada durante a execução, já sanitizada
    (`failure_classification`/`failure_message` nunca contêm traceback,
    segredo, ou texto cru do provider/LLM -- ver
    `CouncilExecutionService._sanitize_unexpected_failure`). Nunca tem
    claims/attempts/verdict/resposta final -- não existe persistência
    incremental neste slice (fora de escopo), então não há nada
    intermediário pra mostrar além de identidade + config aceita."""

    model_config = _CONFIG

    status: Literal["running", "failed"]
    id: str
    started_at: datetime
    run_config: RunConfig
    failed_at: datetime | None = None
    failure_classification: str | None = None
    failure_message: str | None = None
    # Repair M2 -- ver `AcceptedRunRow.failure_stage`. `None` em "running"
    # e em linhas "failed" legadas (estágio nunca registrado).
    failure_stage: FailureStage | None = None
    # T02.2 -- ver docstring de CompletedRunRecord.provider_execution_policy.
    # Novos accepted runs SEMPRE têm um valor concreto (`save_accepted`
    # exige o parâmetro); `None` só ocorre reconstruindo uma linha
    # legada pré-upgrade.
    provider_execution_policy: ProviderExecutionPolicy | None = None
    # Provider Default-Model Snapshot Provenance V1 -- SIBLING de
    # `provider_execution_policy` acima, mesma disciplina exata: `None`
    # só pra runs persistidos antes desta coluna existir.
    default_model_authority_snapshot: DefaultModelAuthoritySnapshot | None = None


class DirectAcceptedRunRecord(BaseModel):
    """Direct Answer Execution V1 -- run DIRETA aceita sem registro
    terminal: `running` (em andamento, ou o processo parou antes de
    terminar -- indistinguíveis, igual ao Conselho) ou `failed` (exceção
    inesperada, ou falha ao gravar o desfecho). Nunca tem resposta: nada
    intermediário é persistido. `kind` distingue da run do Conselho aceita
    (`AcceptedRunRecord`), que continua exatamente como era."""

    model_config = _CONFIG

    kind: Literal["direct"] = "direct"
    status: Literal["running", "failed"]
    id: str
    started_at: datetime
    config: DirectRunConfig
    failed_at: datetime | None = None
    failure_classification: str | None = None
    failure_message: str | None = None
    failure_stage: FailureStage | None = None
    provider_execution_policy: ProviderExecutionPolicy | None = None


class DirectRunRecord(BaseModel):
    """Direct Answer Execution V1 -- desfecho terminal de uma run direta:
    a única resposta do provider (`completed` com texto, ou `failed` por
    erro do provider, com o registro da chamada preservado)."""

    model_config = _CONFIG

    kind: Literal["direct"] = "direct"
    status: DirectRunStatus
    id: str
    started_at: datetime
    ended_at: datetime
    config: DirectRunConfig
    response: ModelResponse
    provider_execution_policy: ProviderExecutionPolicy


PersistedRun = (
    CompletedRunRecord
    | QuorumFailureRecord
    | AcceptedRunRecord
    | DirectRunRecord
    | DirectAcceptedRunRecord
)


class RunSummary(BaseModel):
    """Visão leve para listagem -- sem reconstruir a árvore inteira de
    cada run (`list_runs` não deveria custar o mesmo que N×`get_run`).

    T02.4: `ended_at=None` é o único valor honesto pra `status="running"`
    -- a execução não terminou, então não existe timestamp de fim
    nenhum pra reportar (nunca aproximado por `started_at` nem por
    "agora"). Pra `status="failed"`, `ended_at` é `failed_at`, o mesmo
    padrão já usado por `insufficient_quorum`.

    History Investigation-Identity V1 -- `question` é a pergunta
    CANÔNICA exatamente como persistida em `run_config_json["question"]`
    pra este lifecycle root (nenhuma reconstrução de `RunConfig`
    inteiro nem N+1: `list_runs` já tem o blob JSON de cada linha em
    mãos). Aditivo puro sobre um campo que sempre existiu, obrigatório e
    sem rename, desde o commit inicial deste repositório (ver
    `RunConfig.question`, app/orchestrator/config.py) -- nunca uma
    reconstrução de auditoria, nunca um título gerado."""

    model_config = _CONFIG

    id: str
    status: Literal["completed", "insufficient_quorum", "running", "failed"]
    started_at: datetime
    ended_at: datetime | None  # completed_at/failed_at, ou None se "running"
    question: str
    # Direct Answer Execution V1 -- tipo de run, vindo do fato persistido
    # (tabela terminal ou `accepted_runs.run_kind`), nunca inferido.
    kind: Literal["council", "direct"] = "council"
