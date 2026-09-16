"""
Engine/sessão async e inicialização de schema -- Etapa 10.

Reusa `Settings.database_url` (já existia desde a Etapa 1, nunca lido em
código real até agora). Sem Alembic: não existe banco em produção nem
schema anterior pra migrar, então `Base.metadata.create_all` (idempotente
-- "IF NOT EXISTS" nas tabelas) é suficiente pro MVP. Registrado como
dívida explícita para quando existir schema publicado/evolução real (ver
relatório da Etapa 10).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event, inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.storage.models import Base


def create_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url)

    # SQLite NÃO aplica FOREIGN KEY (nem o CHECK de model_responses) por
    # padrão -- precisa ser ligado explicitamente por conexão, ou os
    # ForeignKey/CheckConstraint do schema (models.py) existem só no
    # papel, sem nenhuma garantia real no banco. Achado real durante os
    # testes de atomicidade (Etapa 10) -- sem isso, uma violação de
    # integridade referencial simplesmente não é detectada.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def init_db(engine: AsyncEngine) -> None:
    """Cria as tabelas que ainda não existem. Idempotente -- seguro
    chamar toda vez que a aplicação inicia.

    Etapa 17A: também roda um ajuste de schema DIRECIONADO e explícito
    (não um framework de migration genérico) pra bancos criados antes da
    Etapa 17A (Stage 15/16), que não têm a coluna
    `had_uncertain_prior_attempts` nas 5 tabelas afetadas. `create_all()`
    nunca adiciona coluna a tabela já existente -- sem isso, um banco
    Stage-16 reaberto sob código Stage-17A quebraria em TODO SELECT/INSERT
    dessas 5 tabelas (reproduzido de verdade nesta mesma investigação),
    não só nos casos-limite novos.

    Etapa 17A.1: mesmo tratamento pra `provider_finish_reason` (Objetivo
    B) -- só que sem backfill nenhum, ver docstring de
    `_upgrade_legacy_provider_finish_reason`.

    Cross-round claim reconciliation: mesmo tratamento pra
    `support_scope_model_count` (tabela `claims`) -- também sem backfill,
    ver docstring de `_upgrade_legacy_support_scope_model_count`.

    T02.2: mesmo tratamento pra `provider_execution_policy_json`
    (`council_runs`/`quorum_failures`/`accepted_runs`) -- também sem
    backfill, ver docstring de
    `_upgrade_legacy_provider_execution_policy`.

    Model identity provenance: mesmo tratamento pra
    `model_identity_source`/`judge_model_identity_source`/
    `editor_model_identity_source` -- também sem backfill, ver docstring
    de `_upgrade_legacy_model_identity_source`.

    Provider-Neutral Request Provenance V1: mesmo tratamento pra
    `request_provenance_json` nas mesmas 5 tabelas de
    `_upgrade_legacy_model_identity_source` -- também sem backfill, ver
    docstring de `_upgrade_legacy_request_provenance`.

    Provider Default-Model Snapshot Provenance V1: mesmo tratamento pra
    `default_model_authority_snapshot_json` nas mesmas 3 tabelas de
    `_upgrade_legacy_provider_execution_policy` -- também sem backfill,
    ver docstring de `_upgrade_legacy_default_model_authority_snapshot`."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_upgrade_legacy_had_uncertain_prior_attempts)
        await conn.run_sync(_upgrade_legacy_provider_finish_reason)
        await conn.run_sync(_upgrade_legacy_support_scope_model_count)
        await conn.run_sync(_upgrade_legacy_provider_execution_policy)
        await conn.run_sync(_upgrade_legacy_model_identity_source)
        await conn.run_sync(_upgrade_legacy_request_provenance)
        await conn.run_sync(_upgrade_legacy_default_model_authority_snapshot)


_HAD_UNCERTAIN_PRIOR_ATTEMPTS_TABLES = (
    ("model_responses", "attempts"),
    ("claim_processing_attempts", "transport_attempts"),
    ("source_analysis_attempts", "transport_attempts"),
    ("judge_attempts", "transport_attempts"),
    ("editor_attempts", "transport_attempts"),
)


def _upgrade_legacy_had_uncertain_prior_attempts(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- Etapa 17A, achado B3.

    Pra cada uma das 5 tabelas afetadas: se a coluna
    `had_uncertain_prior_attempts` já existe (banco criado sob Etapa 17A,
    ou já upgradado antes), NÃO FAZ NADA -- os valores já persistidos são
    autoritativos, nunca recalculados/sobrescritos aqui (um Run real da
    Etapa 17A pode legitimamente ter `had_uncertain_prior_attempts=True`
    mesmo com `attempts=1`, nada a ver com o backfill legado). Só roda o
    `ALTER TABLE` + backfill UMA VEZ, no exato momento em que a coluna é
    adicionada, nunca de novo depois.

    Backfill (achado da investigação real dos caminhos de retry
    pré-Etapa-17A): `attempts`/`transport_attempts` só incrementa dentro
    do loop de `LLMProvider.complete()`, uma vez por `_call_api()`
    genuína -- nenhuma falha 100% local (API key ausente) incrementa.
    Logo `attempts > 1` prova estruturalmente que uma tentativa anterior
    discou de verdade e falhou, para QUALQUER status final (sucesso ou
    erro) -- a regra vale igual pros dois casos, não só sucesso.
    """
    inspector = sa_inspect(sync_conn)
    for table_name, attempts_column in _HAD_UNCERTAIN_PRIOR_ATTEMPTS_TABLES:
        if table_name not in inspector.get_table_names():
            continue  # tabela nova (Etapa 17A), create_all() já a criou com a coluna
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "had_uncertain_prior_attempts" in existing_columns:
            continue  # já upgradado (ou banco nasceu na Etapa 17A) -- nunca recalcula

        sync_conn.execute(
            text(
                f"ALTER TABLE {table_name} "
                "ADD COLUMN had_uncertain_prior_attempts BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        # Backfill único, associado à criação da coluna -- nunca roda de
        # novo numa próxima inicialização, porque a checagem acima já
        # detecta a coluna existindo e pula tudo.
        sync_conn.execute(
            text(
                f"UPDATE {table_name} SET had_uncertain_prior_attempts = "
                f"CASE WHEN {attempts_column} > 1 THEN 1 ELSE 0 END"
            )
        )


_PROVIDER_FINISH_REASON_TABLES = (
    "model_responses",
    "claim_processing_attempts",
    "source_analysis_attempts",
    "judge_attempts",
    "editor_attempts",
)


def _upgrade_legacy_provider_finish_reason(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- Etapa 17A.1, Objetivo B.

    Mesma disciplina de `_upgrade_legacy_had_uncertain_prior_attempts`:
    só `ALTER TABLE` quando a coluna genuinamente não existe (checagem
    via `PRAGMA table_info`), nunca recalcula um valor já persistido.

    Diferente daquele caso, AQUI não há nenhum backfill a fazer -- não
    existe informação derivável de nenhuma coluna existente pra
    reconstruir um `finish_reason` histórico que nunca foi capturado
    (nem `attempts`, nem `status`, nem nenhum outro campo já persistido
    contêm essa informação). Linhas antigas ficam com
    `provider_finish_reason=NULL` -- desconhecido, honesto, nunca
    inventado. SQLite já usa `NULL` como valor implícito pra linhas
    existentes quando um `ALTER TABLE ADD COLUMN` não declara `DEFAULT`
    e a coluna é nullable, então nenhum `UPDATE` é necessário aqui."""
    inspector = sa_inspect(sync_conn)
    for table_name in _PROVIDER_FINISH_REASON_TABLES:
        if table_name not in inspector.get_table_names():
            continue  # tabela nova (Etapa 17A.1), create_all() já a criou com a coluna
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "provider_finish_reason" in existing_columns:
            continue  # já upgradado (ou banco nasceu na Etapa 17A.1) -- nunca recalcula

        sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN provider_finish_reason TEXT"))


def _upgrade_legacy_support_scope_model_count(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- cross-round claim reconciliation.

    Mesma disciplina de `_upgrade_legacy_provider_finish_reason`: só
    `ALTER TABLE` quando a coluna genuinamente não existe, nunca
    recalcula um valor já persistido. Igual àquele caso, AQUI também não
    há nenhum backfill a fazer -- `support_scope_model_count=NULL` é o
    valor HONESTO pra toda claim persistida antes desta etapa (nenhuma
    delas jamais teve um universo de suporte cross-round real a
    registrar; inventar um número agora seria pior que deixar `NULL`).
    SQLite usa `NULL` implicitamente pra linhas existentes quando um
    `ALTER TABLE ADD COLUMN` não declara `DEFAULT` numa coluna nullable,
    então nenhum `UPDATE` é necessário."""
    inspector = sa_inspect(sync_conn)
    table_name = "claims"
    if table_name not in inspector.get_table_names():
        return  # tabela nova (já nasce com a coluna via create_all())
    existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
    if "support_scope_model_count" in existing_columns:
        return  # já upgradado (ou banco já nasceu com a coluna) -- nunca recalcula

    sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN support_scope_model_count INTEGER"))


_PROVIDER_EXECUTION_POLICY_TABLES = ("council_runs", "quorum_failures", "accepted_runs")


def _upgrade_legacy_provider_execution_policy(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- T02.2 (provenance de política de
    execução de provider). Mesma disciplina de
    `_upgrade_legacy_provider_finish_reason`/
    `_upgrade_legacy_support_scope_model_count`: só `ALTER TABLE` quando
    a coluna genuinamente não existe (checagem via `PRAGMA table_info`),
    nunca recalcula um valor já persistido.

    SEM backfill, deliberadamente: `provider_execution_policy_json=NULL`
    é o valor HONESTO pra toda linha persistida antes desta etapa --
    NENHUM valor atual de `Settings`
    (`provider_timeout_seconds`/`provider_max_retries`) pode ser
    retroativamente atribuído a uma execução histórica como se fosse o
    que ESTAVA de fato configurado no deployment no momento daquela
    execução (pode ter sido qualquer outro valor -- não temos como
    saber qual, e o próprio ponto desta feature é parar de perder essa
    informação DAQUI EM DIANTE, não fingir tê-la reconstruído
    retroativamente). Inventar um valor aqui seria pior que deixar
    `NULL`. SQLite usa `NULL` implicitamente pra linhas existentes
    quando um `ALTER TABLE ADD COLUMN` não declara `DEFAULT` numa
    coluna nullable, então nenhum `UPDATE` é necessário."""
    inspector = sa_inspect(sync_conn)
    for table_name in _PROVIDER_EXECUTION_POLICY_TABLES:
        if table_name not in inspector.get_table_names():
            continue  # tabela nova, create_all() já a criou com a coluna
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "provider_execution_policy_json" in existing_columns:
            continue  # já upgradado (ou banco já nasceu com a coluna) -- nunca recalcula

        sync_conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN provider_execution_policy_json TEXT")
        )


def _upgrade_legacy_default_model_authority_snapshot(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- Provider Default-Model Snapshot
    Provenance V1. Mesma disciplina de
    `_upgrade_legacy_provider_execution_policy` (mesmas 3 tabelas, e
    reusa a MESMA tupla `_PROVIDER_EXECUTION_POLICY_TABLES` -- nenhuma
    tupla nova/independente, já que as duas colunas compartilham
    EXATAMENTE o mesmo conjunto de tabelas de envelope de aceite): só
    `ALTER TABLE` quando a coluna genuinamente não existe, nunca
    recalcula/backfilla um valor a partir de `Settings`/registry de
    provider atual -- `default_model_authority_snapshot_json=NULL` é o
    valor HONESTO pra toda linha persistida antes desta etapa (nenhuma
    execução histórica pode ter esse fato reconstituído retroativamente
    de forma confiável)."""
    inspector = sa_inspect(sync_conn)
    for table_name in _PROVIDER_EXECUTION_POLICY_TABLES:
        if table_name not in inspector.get_table_names():
            continue  # tabela nova, create_all() já a criou com a coluna
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "default_model_authority_snapshot_json" in existing_columns:
            continue  # já upgradado (ou banco já nasceu com a coluna) -- nunca recalcula

        sync_conn.execute(
            text(
                f"ALTER TABLE {table_name} ADD COLUMN "
                "default_model_authority_snapshot_json TEXT"
            )
        )


# (table_name, column_name) -- 6 tabelas usam o nome genérico
# "model_identity_source" (copiado verbatim de
# ProviderResponse.model_identity_source); judge_verdicts/final_answers
# usam os nomes espelhados dos campos que eles mesmos já têm
# (judge_model/editor_model), nunca o genérico "model_identity_source"
# solto -- mesma disciplina de nomenclatura já aplicada aos próprios
# campos de domínio (JudgeVerdict.judge_model_identity_source,
# FinalAnswer.editor_model_identity_source). `claim_supports` (repair
# pós-revisão) usa o nome genérico -- ver ClaimSupport.model_identity_source
# (app/models/domain.py): sempre COPIADO verbatim do ModelResponse
# referenciado por model_response_id, nunca uma segunda resolução.
_MODEL_IDENTITY_SOURCE_COLUMNS = (
    ("model_responses", "model_identity_source"),
    ("claim_processing_attempts", "model_identity_source"),
    ("source_analysis_attempts", "model_identity_source"),
    ("judge_attempts", "model_identity_source"),
    ("editor_attempts", "model_identity_source"),
    ("claim_supports", "model_identity_source"),
    ("judge_verdicts", "judge_model_identity_source"),
    ("final_answers", "editor_model_identity_source"),
)


def _upgrade_legacy_model_identity_source(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- provenance de identidade de
    modelo (distingue `model`/`judge_model`/`editor_model`
    provider-reported de requested-model fallback, ver
    `app.models.provider_models.ModelIdentitySource`). Mesma disciplina
    de `_upgrade_legacy_provider_execution_policy`: só `ALTER TABLE`
    quando a coluna genuinamente não existe (checagem via `PRAGMA
    table_info`), nunca recalcula um valor já persistido.

    SEM backfill, deliberadamente: `NULL` é o valor HONESTO pra toda
    linha persistida antes desta coluna existir -- não existe forma de
    provar retroativamente, a partir de nenhuma coluna já persistida
    (inclusive `model == requested_model`, que nunca prova observação vs.
    fallback -- ver docstring de `ModelIdentitySource`), se aquele
    identificador foi efetivamente reportado pelo provider ou substituído
    pelo requested. Inventar um valor aqui seria pior que deixar `NULL`.
    SQLite usa `NULL` implicitamente pra linhas existentes quando um
    `ALTER TABLE ADD COLUMN` não declara `DEFAULT` numa coluna nullable,
    então nenhum `UPDATE` é necessário."""
    inspector = sa_inspect(sync_conn)
    for table_name, column_name in _MODEL_IDENTITY_SOURCE_COLUMNS:
        if table_name not in inspector.get_table_names():
            continue  # tabela nova, create_all() já a criou com a coluna
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            continue  # já upgradado (ou banco já nasceu com a coluna) -- nunca recalcula

        sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} TEXT"))


# Provider-Neutral Request Provenance V1 -- exatamente as 5 tabelas de
# "registro de UMA chamada real de provider" (ModelResponse + os 4
# *Attempt) -- as MESMAS 5 primeiras entradas de
# `_MODEL_IDENTITY_SOURCE_COLUMNS` acima. Nunca `claim_supports`/
# `judge_verdicts`/`final_answers` -- esses são saídas semânticas
# DERIVADAS de uma chamada, nunca o registro de aceite/tentativa da
# chamada em si (ver seção 9 do contrato desta slice: não duplicar
# provenance de tentativa aceita em saídas derivadas).
_REQUEST_PROVENANCE_TABLES = (
    "model_responses",
    "claim_processing_attempts",
    "source_analysis_attempts",
    "judge_attempts",
    "editor_attempts",
)


def _upgrade_legacy_request_provenance(sync_conn) -> None:  # noqa: ANN001
    """Ajuste de schema direcionado -- Provider-Neutral Request
    Provenance V1 (ver `app.models.request_provenance.RequestProvenance`).
    Mesma disciplina de `_upgrade_legacy_model_identity_source`: só
    `ALTER TABLE` quando a coluna genuinamente não existe (checagem via
    `PRAGMA table_info`), nunca recalcula um valor já persistido.

    SEM backfill, deliberadamente: `NULL` é o valor HONESTO pra toda
    linha persistida antes desta coluna existir -- não existe forma de
    reconstruir retroativamente o `CompletionRequest` exato que gerou
    uma resposta/tentativa histórica (o código dos builders muda com o
    tempo; regenerar um request "parecido" hoje NUNCA seria a mesma
    provenance daquele momento). Inventar um valor aqui seria
    exatamente o tipo de reinterpretação histórica que este slice
    proíbe. SQLite usa `NULL` implicitamente pra linhas existentes
    quando um `ALTER TABLE ADD COLUMN` não declara `DEFAULT` numa
    coluna nullable, então nenhum `UPDATE` é necessário."""
    inspector = sa_inspect(sync_conn)
    for table_name in _REQUEST_PROVENANCE_TABLES:
        if table_name not in inspector.get_table_names():
            continue  # tabela nova, create_all() já a criou com a coluna
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        if "request_provenance_json" in existing_columns:
            continue  # já upgradado (ou banco já nasceu com a coluna) -- nunca recalcula

        sync_conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN request_provenance_json TEXT")
        )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """UMA transação atômica por bloco -- commit no final, rollback
    completo se qualquer coisa dentro do bloco levantar (Decision Delta
    §6: sem persistência incremental, salvar tudo de uma vez ou nada)."""
    async with session_factory() as session:
        async with session.begin():
            yield session


async def build_default_engine(settings: Settings | None = None) -> AsyncEngine:
    """Conveniência pra quem só quer `Settings.database_url` sem montar
    engine/sessionmaker na mão."""
    settings = settings or Settings()
    engine = create_engine(settings.database_url)
    await init_db(engine)
    return engine
