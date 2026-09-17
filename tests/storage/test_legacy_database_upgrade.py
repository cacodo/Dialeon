"""
Testes de compatibilidade de banco legado — Etapa 17A (B3).

Simula um banco Stage-16 genuíno: cria o schema atual via `init_db()`
normal, depois APAGA a coluna `had_uncertain_prior_attempts` das 5
tabelas manualmente (SQLite 3.35+ suporta `DROP COLUMN`) -- reproduz
fielmente "um banco criado antes da Etapa 17A", sem depender de nenhum
snapshot de schema congelado.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.storage.database import create_engine, init_db

_TABLES_AND_ATTEMPTS_COLUMN = [
    ("model_responses", "attempts"),
    ("claim_processing_attempts", "transport_attempts"),
    ("source_analysis_attempts", "transport_attempts"),
    ("judge_attempts", "transport_attempts"),
    ("editor_attempts", "transport_attempts"),
]


async def _make_legacy_db(db_path: str) -> None:
    """Cria o schema atual e remove a coluna nova das 5 tabelas,
    simulando fielmente um banco criado antes da Etapa 17A."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN had_uncertain_prior_attempts")
    conn.commit()
    conn.close()


def _seed_council_run(conn: sqlite3.Connection, run_id: str = "run1") -> None:
    conn.execute(
        "INSERT INTO council_runs (id, status, started_at, completed_at, run_config_json, "
        "claim_processor_provider, debate_cumulative_budget_exceeded, "
        "initial_insufficient_data_for_consensus, initial_budget_exceeded, judge_provider, "
        "judge_cumulative_budget_exceeded, editor_provider, editor_cumulative_budget_exceeded) "
        f"VALUES ('{run_id}','completed','2026-01-01','2026-01-01','{{}}','anthropic',0,0,0,"
        "'anthropic',0,'anthropic',0)"
    )


def _insert_model_response(
    conn: sqlite3.Connection, id_: str, status: str, attempts: int, position: int = 0
) -> None:
    conn.execute(
        "INSERT INTO model_responses (id, council_run_id, round_number, position, provider, "
        "requested_model, model, status, usage_present, latency_ms, attempts, created_at) "
        f"VALUES ('{id_}','run1',1,{position},'openai','gpt-5.5','gpt-5.5','{status}',0,100,"
        f"{attempts},'2026-01-01 00:00:00')"
    )


@pytest.mark.asyncio
async def test_legacy_database_gets_column_added(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    columns_before = {c[1] for c in conn.execute("PRAGMA table_info(model_responses)")}
    assert "had_uncertain_prior_attempts" not in columns_before
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns_after = {c[1] for c in conn.execute("PRAGMA table_info(model_responses)")}
    assert "had_uncertain_prior_attempts" in columns_after
    conn.close()


@pytest.mark.asyncio
async def test_legacy_backfill_derives_from_attempts_for_success_and_error(tmp_path):
    """attempts=1 => False; attempts>1 => True -- pra sucesso E erro,
    exatamente a mesma regra (o bit descreve incerteza de tentativa
    ANTERIOR, independente do desfecho final)."""
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn)
    _insert_model_response(conn, "mr_success_1", "success", attempts=1, position=0)
    _insert_model_response(conn, "mr_success_2", "success", attempts=2, position=1)
    _insert_model_response(conn, "mr_error_1", "error", attempts=1, position=2)
    _insert_model_response(conn, "mr_error_3", "error", attempts=3, position=3)
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    rows = dict(
        conn.execute(
            "SELECT id, had_uncertain_prior_attempts FROM model_responses"
        ).fetchall()
    )
    conn.close()

    assert rows["mr_success_1"] == 0
    assert rows["mr_success_2"] == 1
    assert rows["mr_error_1"] == 0
    assert rows["mr_error_3"] == 1


@pytest.mark.asyncio
async def test_legacy_upgrade_preserves_existing_rows(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn)
    _insert_model_response(conn, "mr1", "success", attempts=1)
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM model_responses").fetchone()[0]
    conn.close()
    assert count == 1  # nenhuma linha perdida


@pytest.mark.asyncio
async def test_native_stage17a_value_is_never_recomputed_on_reinit(tmp_path):
    """Uma vez que a coluna existe (banco já upgradado, ou nascido sob a
    Etapa 17A), `init_db()` NUNCA mais recalcula/sobrescreve o valor --
    ele passa a ser autoritativo."""
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn)
    _insert_model_response(conn, "mr1", "success", attempts=1)  # backfill produziria False
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)  # upgrade + backfill roda aqui, mr1 vira False
    await engine.dispose()

    # Simula um valor NATIVO real da Etapa 17A sendo escrito depois --
    # legítimo mesmo com attempts=1 (o bit tem fonte de verdade própria
    # a partir daqui, não é mais derivado de attempts).
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE model_responses SET had_uncertain_prior_attempts = 1 WHERE id = 'mr1'"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)  # rodar de novo -- não deve recalcular nada
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    value = conn.execute(
        "SELECT had_uncertain_prior_attempts FROM model_responses WHERE id = 'mr1'"
    ).fetchone()[0]
    conn.close()
    assert value == 1  # continua True -- NUNCA voltou a ser recalculado como False


@pytest.mark.asyncio
async def test_init_db_repeated_is_safe_and_idempotent(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn)
    _insert_model_response(conn, "mr1", "success", attempts=2)
    conn.commit()
    conn.close()

    for _ in range(3):
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)
        await engine.dispose()

    conn = sqlite3.connect(db_path)
    value = conn.execute(
        "SELECT had_uncertain_prior_attempts FROM model_responses WHERE id = 'mr1'"
    ).fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM model_responses").fetchone()[0]
    conn.close()
    assert value == 1
    assert count == 1  # nenhuma duplicação


@pytest.mark.asyncio
async def test_fresh_stage17a_database_does_not_run_legacy_backfill_path(tmp_path):
    """Um banco que nasce já sob a Etapa 17A tem a coluna desde a
    criação -- o branch de upgrade/backfill nunca é exercitado (a
    checagem de coluna existente já pula tudo)."""
    db_path = str(tmp_path / "fresh.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns = {c[1] for c in conn.execute("PRAGMA table_info(model_responses)")}
    conn.close()
    assert "had_uncertain_prior_attempts" in columns


@pytest.mark.asyncio
async def test_all_five_tables_upgraded_consistently(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "had_uncertain_prior_attempts" in columns, table
    conn.close()


# ---------------------------------------------------------------------------
# Etapa 17A.1 (Objetivo B) — upgrade de banco Stage-17A (tem
# had_uncertain_prior_attempts, ainda não tem provider_finish_reason)
# ---------------------------------------------------------------------------


async def _make_stage17a_db(db_path: str) -> None:
    """Cria o schema ATUAL (Etapa 17A.1) e remove só a coluna
    `provider_finish_reason` das 5 tabelas -- simula fielmente um banco
    já upgradado pela Etapa 17A (tem `had_uncertain_prior_attempts`),
    mas criado antes da Etapa 17A.1."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_finish_reason")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_stage17a_database_gets_finish_reason_column_added(tmp_path):
    db_path = str(tmp_path / "stage17a.db")
    await _make_stage17a_db(db_path)

    conn = sqlite3.connect(db_path)
    columns_before = {c[1] for c in conn.execute("PRAGMA table_info(model_responses)")}
    assert "had_uncertain_prior_attempts" in columns_before  # já tinha essa
    assert "provider_finish_reason" not in columns_before  # ainda não tinha essa
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns_after = {c[1] for c in conn.execute("PRAGMA table_info(model_responses)")}
    assert "provider_finish_reason" in columns_after
    conn.close()


@pytest.mark.asyncio
async def test_stage17a_upgrade_does_not_touch_had_uncertain_prior_attempts(tmp_path):
    """As duas colunas são upgrades INDEPENDENTES -- adicionar
    provider_finish_reason nunca deveria recalcular/tocar
    had_uncertain_prior_attempts (que já era autoritativo nesse banco)."""
    db_path = str(tmp_path / "stage17a.db")
    await _make_stage17a_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn)
    conn.execute(
        "INSERT INTO model_responses (id, council_run_id, round_number, position, provider, "
        "requested_model, model, status, usage_present, latency_ms, attempts, "
        "had_uncertain_prior_attempts, created_at) "
        "VALUES ('mr1','run1',1,0,'openai','gpt-5.5','gpt-5.5','success',0,100,1,"
        "1,'2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT had_uncertain_prior_attempts, provider_finish_reason FROM model_responses "
        "WHERE id = 'mr1'"
    ).fetchone()
    conn.close()
    assert row[0] == 1  # continua True -- nunca recalculado pra False (attempts=1 sugeriria False)
    assert row[1] is None  # coluna nova, sem informação derivável -- NULL honesto


# ---------------------------------------------------------------------------
# Cross-round claim reconciliation — upgrade de banco sem
# support_scope_model_count na tabela claims
# ---------------------------------------------------------------------------


async def _make_pre_reconciliation_db(db_path: str) -> None:
    """Cria o schema ATUAL e remove só a coluna
    `support_scope_model_count` da tabela `claims` -- simula fielmente um
    banco criado antes desta etapa (tem todas as outras colunas, inclusive
    `had_uncertain_prior_attempts`/`provider_finish_reason`, que não são
    afetadas por este upgrade -- upgrades independentes)."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE claims DROP COLUMN support_scope_model_count")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_pre_reconciliation_database_gets_support_scope_column_added(tmp_path):
    db_path = str(tmp_path / "pre_reconciliation.db")
    await _make_pre_reconciliation_db(db_path)

    conn = sqlite3.connect(db_path)
    columns_before = {c[1] for c in conn.execute("PRAGMA table_info(claims)")}
    assert "support_scope_model_count" not in columns_before
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns_after = {c[1] for c in conn.execute("PRAGMA table_info(claims)")}
    assert "support_scope_model_count" in columns_after
    conn.close()


@pytest.mark.asyncio
async def test_pre_reconciliation_claims_get_null_support_scope_not_a_guessed_value(tmp_path):
    """Nenhum backfill -- claims persistidas antes desta etapa nunca
    tiveram um universo de suporte cross-round real; NULL é o único
    valor honesto, nunca um número inventado."""
    db_path = str(tmp_path / "pre_reconciliation.db")
    await _make_pre_reconciliation_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO council_runs (id, status, started_at, completed_at, run_config_json, "
        "claim_processor_provider, debate_cumulative_budget_exceeded, "
        "initial_insufficient_data_for_consensus, initial_budget_exceeded, judge_provider, "
        "judge_cumulative_budget_exceeded, editor_provider, editor_cumulative_budget_exceeded) "
        "VALUES ('run1','completed','2026-01-01','2026-01-01','{}','anthropic',0,0,0,"
        "'anthropic',0,'anthropic',0)"
    )
    conn.execute(
        "INSERT INTO claims (id, council_run_id, position, text, round_introduced, status, "
        "total_models_in_round, created_at) "
        "VALUES ('claim1','run1',0,'Uma claim antiga.',1,'active',2,'2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    value = conn.execute(
        "SELECT support_scope_model_count FROM claims WHERE id = 'claim1'"
    ).fetchone()[0]
    conn.close()
    assert value is None


@pytest.mark.asyncio
async def test_reconciliation_upgrade_does_not_touch_other_legacy_columns(tmp_path):
    """Upgrades independentes -- adicionar support_scope_model_count
    nunca deveria recalcular/tocar had_uncertain_prior_attempts (já
    autoritativo neste banco)."""
    db_path = str(tmp_path / "pre_reconciliation.db")
    await _make_pre_reconciliation_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn)
    # Coluna `had_uncertain_prior_attempts` já existe (NOT NULL) neste
    # banco -- diferente de `_make_legacy_db`, `_make_pre_reconciliation_db`
    # não a remove, só `support_scope_model_count`. Precisa ser fornecida
    # explicitamente no INSERT (valor nativo, não backfill).
    conn.execute(
        "INSERT INTO model_responses (id, council_run_id, round_number, position, provider, "
        "requested_model, model, status, usage_present, latency_ms, attempts, "
        "had_uncertain_prior_attempts, created_at) "
        "VALUES ('mr1','run1',1,0,'openai','gpt-5.5','gpt-5.5','success',0,100,1,"
        "1,'2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    value = conn.execute(
        "SELECT had_uncertain_prior_attempts FROM model_responses WHERE id = 'mr1'"
    ).fetchone()[0]
    conn.close()
    assert value == 1  # nunca recalculado por causa do upgrade não relacionado


@pytest.mark.asyncio
async def test_fresh_database_does_not_run_pre_reconciliation_upgrade_path(tmp_path):
    """Um banco que nasce já com a coluna nunca exercita o branch de
    upgrade -- a checagem de coluna existente já pula tudo."""
    db_path = str(tmp_path / "fresh.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns = {c[1] for c in conn.execute("PRAGMA table_info(claims)")}
    conn.close()
    assert "support_scope_model_count" in columns


@pytest.mark.asyncio
async def test_both_legacy_upgrades_together_from_true_stage16_database(tmp_path):
    """Cenário mais antigo possível: banco Stage-16 puro (nenhuma das
    duas colunas), upgradado direto pra Etapa 17A.1 numa única chamada
    de init_db()."""
    db_path = str(tmp_path / "legacy.db")
    await _make_legacy_db(db_path)  # já remove had_uncertain_prior_attempts também

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_finish_reason")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "had_uncertain_prior_attempts" in columns, table
        assert "provider_finish_reason" in columns, table
    conn.close()


# ---------------------------------------------------------------------------
# T02.2 -- provider_execution_policy_json em council_runs/quorum_failures/
# accepted_runs
# ---------------------------------------------------------------------------

_PROVIDER_EXECUTION_POLICY_TABLES = ("council_runs", "quorum_failures", "accepted_runs")


async def _make_pre_t02_2_db(db_path: str) -> None:
    """Cria o schema ATUAL e remove só a coluna
    `provider_execution_policy_json` das 3 tabelas afetadas -- simula
    fielmente um banco criado antes do slice T02.2 (todas as outras
    colunas/upgrades legados permanecem intactos -- upgrades
    independentes, mesma disciplina de `_make_pre_reconciliation_db`)."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_execution_policy_json")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_pre_t02_2_database_gets_policy_column_added_to_all_three_tables(tmp_path):
    db_path = str(tmp_path / "pre_t02_2.db")
    await _make_pre_t02_2_db(db_path)

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns_before = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "provider_execution_policy_json" not in columns_before, table
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns_after = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "provider_execution_policy_json" in columns_after, table
    conn.close()


@pytest.mark.asyncio
async def test_pre_t02_2_council_run_gets_null_policy_never_current_settings_defaults(tmp_path):
    """Teste N (nível de schema) -- uma linha `council_runs` persistida
    antes desta coluna existir precisa reconstruir com
    `provider_execution_policy_json=NULL` depois do upgrade -- NUNCA os
    defaults ATUAIS de Settings (`provider_timeout_seconds=60`/
    `provider_max_retries=2`, que produziriam
    attempt_timeout_seconds=60.0/max_transport_attempts_per_completion=3)."""
    db_path = str(tmp_path / "pre_t02_2_with_row.db")
    await _make_pre_t02_2_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="legacy-run-1")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    (policy_json,) = conn.execute(
        "SELECT provider_execution_policy_json FROM council_runs WHERE id = 'legacy-run-1'"
    ).fetchone()
    conn.close()
    assert policy_json is None


@pytest.mark.asyncio
async def test_t02_2_upgrade_does_not_touch_other_legacy_columns(tmp_path):
    """Upgrades independentes -- adicionar `provider_execution_policy_json`
    não deve tocar `had_uncertain_prior_attempts`/`provider_finish_reason`/
    `support_scope_model_count` (já testados isoladamente acima)."""
    db_path = str(tmp_path / "pre_t02_2_full_legacy.db")
    await _make_legacy_db(db_path)  # remove had_uncertain_prior_attempts

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_finish_reason")
    conn.execute("ALTER TABLE claims DROP COLUMN support_scope_model_count")
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_execution_policy_json")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "had_uncertain_prior_attempts" in columns, table
        assert "provider_finish_reason" in columns, table
    claims_columns = {c[1] for c in conn.execute("PRAGMA table_info(claims)")}
    assert "support_scope_model_count" in claims_columns
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "provider_execution_policy_json" in columns, table
    conn.close()


# ---------------------------------------------------------------------------
# Model identity provenance -- model_identity_source (model_responses/
# claim_processing_attempts/source_analysis_attempts/judge_attempts/
# editor_attempts), judge_model_identity_source (judge_verdicts),
# editor_model_identity_source (final_answers)
# ---------------------------------------------------------------------------

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


async def _make_pre_model_identity_source_db(db_path: str) -> None:
    """Cria o schema ATUAL e remove só as colunas de
    model_identity_source das 8 tabelas afetadas -- simula fielmente um
    banco criado antes desta slice (upgrades independentes, mesma
    disciplina de `_make_pre_t02_2_db`). Repair pós-revisão independente
    (LOW #1) -- esta tupla é uma cópia INTENCIONALMENTE independente da
    de `app/storage/database.py` (mesma disciplina de
    `_TABLES_AND_ATTEMPTS_COLUMN`/`_PROVIDER_EXECUTION_POLICY_TABLES`
    acima -- importar a tupla de produção tornaria o teste tautológico:
    um bug na lista de produção nunca seria pego). `claim_supports`
    estava ausente aqui até este repair -- fazia
    `test_..._all_eight_tables` iterar só 7 pares (nunca provava nada
    sobre `claim_supports`) e os testes de história/idempotência de
    `claim_supports` inserirem numa tabela que NUNCA teve a coluna
    removida (a asserção `IS NULL` passava pelo motivo ERRADO -- a
    coluna nunca tinha sido tocada pelo upgrader de verdade)."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_pre_model_identity_source_database_gets_columns_added_to_all_eight_tables(tmp_path):
    db_path = str(tmp_path / "pre_model_identity_source.db")
    await _make_pre_model_identity_source_db(db_path)

    conn = sqlite3.connect(db_path)
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        columns_before = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert column not in columns_before, table
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        columns_after = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert column in columns_after, table
    conn.close()


@pytest.mark.asyncio
async def test_pre_model_identity_source_row_gets_null_never_backfilled_from_equality(tmp_path):
    """Uma linha `model_responses` persistida antes desta coluna existir
    -- com `model == requested_model` (o caso que uma inferência ingênua
    tentaria usar como prova de fallback) -- precisa reconstruir com
    `model_identity_source=NULL` depois do upgrade, NUNCA
    'requested_fallback' inferido dessa igualdade (ver docstring de
    `ModelIdentitySource`/`_upgrade_legacy_model_identity_source`)."""
    db_path = str(tmp_path / "pre_model_identity_source_with_row.db")
    await _make_pre_model_identity_source_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    # model == requested_model = "gpt-5.5" -- exatamente o caso que uma
    # inferência ingênua por igualdade tentaria usar como prova de
    # fallback. had_uncertain_prior_attempts precisa ser explícito aqui
    # (diferente de `_insert_model_response`): esta tabela já nasceu com
    # a coluna (create_all, NOT NULL sem DEFAULT) -- só as colunas de
    # model_identity_source foram removidas por `_make_pre_model_identity_source_db`.
    conn.execute(
        "INSERT INTO model_responses (id, council_run_id, round_number, position, provider, "
        "requested_model, model, status, usage_present, latency_ms, attempts, "
        "had_uncertain_prior_attempts, created_at) "
        "VALUES ('mr-legacy-1','run1',1,0,'openai','gpt-5.5','gpt-5.5','success',0,100,"
        "1,0,'2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    (model_identity_source,) = conn.execute(
        "SELECT model_identity_source FROM model_responses WHERE id = 'mr-legacy-1'"
    ).fetchone()
    conn.close()
    assert model_identity_source is None


@pytest.mark.asyncio
async def test_model_identity_source_upgrade_does_not_touch_other_legacy_columns(tmp_path):
    """Upgrades independentes -- adicionar as colunas de
    model_identity_source não deve tocar nenhum dos upgrades legados já
    testados isoladamente acima."""
    db_path = str(tmp_path / "pre_model_identity_source_full_legacy.db")
    await _make_legacy_db(db_path)  # remove had_uncertain_prior_attempts

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_finish_reason")
    conn.execute("ALTER TABLE claims DROP COLUMN support_scope_model_count")
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_execution_policy_json")
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "had_uncertain_prior_attempts" in columns, table
        assert "provider_finish_reason" in columns, table
    claims_columns = {c[1] for c in conn.execute("PRAGMA table_info(claims)")}
    assert "support_scope_model_count" in claims_columns
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "provider_execution_policy_json" in columns, table
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert column in columns, table
    conn.close()


@pytest.mark.asyncio
async def test_model_identity_source_upgrade_is_idempotent_across_repeated_init_db(tmp_path):
    """G -- rodar init_db() várias vezes sobre um banco pré-slice nunca
    duplica/recalcula as colunas de model_identity_source (mesma
    disciplina de `test_init_db_repeated_is_safe_and_idempotent`)."""
    db_path = str(tmp_path / "pre_model_identity_source_idempotent.db")
    await _make_pre_model_identity_source_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    conn.execute(
        "INSERT INTO claim_supports (claim_id, model_response_id, provider, model, position) "
        "VALUES ('claim1', 'mr-legacy-1', 'openai', 'gpt-5.5', 0)"
    )
    conn.commit()
    conn.close()

    for _ in range(3):
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)
        await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert column in columns, table
    (value,) = conn.execute(
        "SELECT model_identity_source FROM claim_supports WHERE claim_id = 'claim1'"
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM claim_supports").fetchone()[0]
    conn.close()
    assert value is None  # nunca recalculado/backfillado
    assert count == 1  # nenhuma duplicação


@pytest.mark.asyncio
async def test_pre_model_identity_source_claim_support_row_gets_null(tmp_path):
    """E -- uma linha `claim_supports` persistida antes desta coluna
    existir (model presente, sem nenhuma referência a
    model_identity_source) reconstrói com model_identity_source=NULL no
    nível do schema -- ver round-trip completo domínio/público em
    tests/storage/test_repository.py e tests/api/test_get_run_audit.py."""
    db_path = str(tmp_path / "pre_model_identity_source_claim_support.db")
    await _make_pre_model_identity_source_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    conn.execute(
        "INSERT INTO claim_supports (claim_id, model_response_id, provider, model, position) "
        "VALUES ('claim1', 'mr-legacy-1', 'openai', 'gpt-5.5', 0)"
    )
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    (model_identity_source,) = conn.execute(
        "SELECT model_identity_source FROM claim_supports WHERE claim_id = 'claim1'"
    ).fetchone()
    conn.close()
    assert model_identity_source is None


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1 -- request_provenance_json
# (model_responses/claim_processing_attempts/source_analysis_attempts/
# judge_attempts/editor_attempts)
# ---------------------------------------------------------------------------

_REQUEST_PROVENANCE_TABLES = (
    "model_responses",
    "claim_processing_attempts",
    "source_analysis_attempts",
    "judge_attempts",
    "editor_attempts",
)


def _insert_full_schema_model_response(conn: sqlite3.Connection, id_: str) -> None:
    """Insere um `model_responses` num banco onde SÓ
    `request_provenance_json` foi removida -- diferente de
    `_insert_model_response` (usada pelos upgrades que removem
    `had_uncertain_prior_attempts`), aqui essa coluna já existe NOT NULL
    sem default, então precisa ser fornecida explicitamente."""
    conn.execute(
        "INSERT INTO model_responses (id, council_run_id, round_number, position, provider, "
        "requested_model, model, status, usage_present, latency_ms, attempts, "
        "had_uncertain_prior_attempts, created_at) "
        f"VALUES ('{id_}','run1',1,0,'openai','gpt-5.5','gpt-5.5','success',0,100,"
        "1,0,'2026-01-01 00:00:00')"
    )


async def _make_pre_request_provenance_db(db_path: str) -> None:
    """Cria o schema ATUAL e remove só `request_provenance_json` das 5
    tabelas afetadas -- simula fielmente um banco criado antes desta
    slice (mesma disciplina de `_make_pre_model_identity_source_db`).
    Tupla independente de `app/storage/database.py::_REQUEST_PROVENANCE_TABLES`
    de propósito -- importar tornaria o teste tautológico."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _REQUEST_PROVENANCE_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN request_provenance_json")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_pre_request_provenance_database_gets_column_added_to_all_five_tables(tmp_path):
    db_path = str(tmp_path / "pre_request_provenance.db")
    await _make_pre_request_provenance_db(db_path)

    conn = sqlite3.connect(db_path)
    for table in _REQUEST_PROVENANCE_TABLES:
        columns_before = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "request_provenance_json" not in columns_before, table
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _REQUEST_PROVENANCE_TABLES:
        columns_after = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "request_provenance_json" in columns_after, table
    conn.close()


@pytest.mark.asyncio
async def test_pre_request_provenance_row_gets_null_never_backfilled(tmp_path):
    """Uma linha `model_responses` persistida antes desta coluna existir
    reconstrói com `request_provenance_json=NULL` depois do upgrade --
    nunca um valor v1 inferido/regenerado a partir dos builders atuais
    (ver seção 12/17 do contrato desta slice)."""
    db_path = str(tmp_path / "pre_request_provenance_with_row.db")
    await _make_pre_request_provenance_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    _insert_full_schema_model_response(conn, "mr-legacy-1")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    (request_provenance_json,) = conn.execute(
        "SELECT request_provenance_json FROM model_responses WHERE id = 'mr-legacy-1'"
    ).fetchone()
    conn.close()
    assert request_provenance_json is None


@pytest.mark.asyncio
async def test_request_provenance_upgrade_does_not_touch_other_legacy_columns(tmp_path):
    """Upgrades independentes -- adicionar `request_provenance_json` não
    deve tocar nenhum dos upgrades legados já testados isoladamente
    acima (mesma disciplina de
    `test_model_identity_source_upgrade_does_not_touch_other_legacy_columns`)."""
    db_path = str(tmp_path / "pre_request_provenance_full_legacy.db")
    await _make_legacy_db(db_path)  # remove had_uncertain_prior_attempts

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN provider_finish_reason")
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    for table in _REQUEST_PROVENANCE_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN request_provenance_json")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, _ in _TABLES_AND_ATTEMPTS_COLUMN:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "had_uncertain_prior_attempts" in columns, table
        assert "provider_finish_reason" in columns, table
    for table, column in _MODEL_IDENTITY_SOURCE_COLUMNS:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert column in columns, table
    for table in _REQUEST_PROVENANCE_TABLES:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "request_provenance_json" in columns, table
    conn.close()


@pytest.mark.asyncio
async def test_request_provenance_upgrade_is_idempotent_across_repeated_init_db(tmp_path):
    db_path = str(tmp_path / "pre_request_provenance_idempotent.db")
    await _make_pre_request_provenance_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    _insert_full_schema_model_response(conn, "mr-legacy-1")
    conn.commit()
    conn.close()

    for _ in range(3):
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)
        await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _REQUEST_PROVENANCE_TABLES:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "request_provenance_json" in columns, table
    (value,) = conn.execute(
        "SELECT request_provenance_json FROM model_responses WHERE id = 'mr-legacy-1'"
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM model_responses").fetchone()[0]
    conn.close()
    assert value is None
    assert count == 1


# ---------------------------------------------------------------------------
# Provider Default-Model Snapshot Provenance V1 -- mesmas 3 tabelas de
# _PROVIDER_EXECUTION_POLICY_TABLES (council_runs/quorum_failures/
# accepted_runs), nunca uma tupla nova/independente.
# ---------------------------------------------------------------------------


async def _make_pre_default_model_snapshot_db(db_path: str) -> None:
    """Cria o schema ATUAL e remove só `default_model_authority_snapshot_json`
    das 3 tabelas -- mesma disciplina de `_make_pre_t02_2_db`."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN default_model_authority_snapshot_json")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_pre_default_model_snapshot_database_gets_column_added_to_all_three_tables(tmp_path):
    db_path = str(tmp_path / "pre_default_model_snapshot.db")
    await _make_pre_default_model_snapshot_db(db_path)

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns_before = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "default_model_authority_snapshot_json" not in columns_before, table
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns_after = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "default_model_authority_snapshot_json" in columns_after, table
    conn.close()


@pytest.mark.asyncio
async def test_pre_default_model_snapshot_council_run_gets_null_never_current_registry(tmp_path):
    """Uma linha `council_runs` persistida antes desta coluna existir
    precisa reconstruir com `default_model_authority_snapshot_json=NULL`
    -- NUNCA um snapshot derivado do registry de provider atual."""
    db_path = str(tmp_path / "pre_default_model_snapshot_with_row.db")
    await _make_pre_default_model_snapshot_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="legacy-run-snapshot-1")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    (snapshot_json,) = conn.execute(
        "SELECT default_model_authority_snapshot_json FROM council_runs "
        "WHERE id = 'legacy-run-snapshot-1'"
    ).fetchone()
    conn.close()
    assert snapshot_json is None


@pytest.mark.asyncio
async def test_default_model_snapshot_upgrade_does_not_touch_provider_execution_policy_column(
    tmp_path,
):
    """Upgrades independentes -- adicionar `default_model_authority_snapshot_json`
    não deve tocar `provider_execution_policy_json` (já testado isoladamente
    acima) mesmo quando ambas as colunas estão ausentes ao mesmo tempo
    (ex.: banco genuinamente pré-T02.2 fazendo upgrade direto pra esta
    versão, pulando o estado intermediário)."""
    db_path = str(tmp_path / "pre_default_model_snapshot_full_legacy.db")
    await _make_pre_t02_2_db(db_path)  # remove provider_execution_policy_json

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN default_model_authority_snapshot_json")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "provider_execution_policy_json" in columns, table
        assert "default_model_authority_snapshot_json" in columns, table
    conn.close()


@pytest.mark.asyncio
async def test_default_model_snapshot_upgrade_is_idempotent_across_repeated_init_db(tmp_path):
    db_path = str(tmp_path / "pre_default_model_snapshot_idempotent.db")
    await _make_pre_default_model_snapshot_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="legacy-run-snapshot-2")
    conn.commit()
    conn.close()

    for _ in range(3):
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)
        await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table in _PROVIDER_EXECUTION_POLICY_TABLES:
        columns = {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        assert "default_model_authority_snapshot_json" in columns, table
    (value,) = conn.execute(
        "SELECT default_model_authority_snapshot_json FROM council_runs "
        "WHERE id = 'legacy-run-snapshot-2'"
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM council_runs").fetchone()[0]
    conn.close()
    assert value is None
    assert count == 1


# ---------------------------------------------------------------------------
# UI Slice 3 (Structured Final Answer) -- answer_blocks_json (final_answers)
# ---------------------------------------------------------------------------


async def _make_pre_answer_blocks_db(db_path: str) -> None:
    """Cria o schema ATUAL e remove só a coluna `answer_blocks_json` da
    tabela `final_answers` -- simula fielmente um banco criado antes da
    UI Slice 3 (todas as outras colunas/upgrades presentes e
    inalterados)."""
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE final_answers DROP COLUMN answer_blocks_json")
    conn.commit()
    conn.close()


def _seed_final_answer(conn: sqlite3.Connection, run_id: str, final_answer_id: str) -> None:
    conn.execute(
        "INSERT INTO final_answers (id, council_run_id, answer_text, limitations_json, "
        "status, editor_model, editor_model_identity_source, based_on_verdict_id, "
        "judge_confidence, created_at) "
        "VALUES (?, ?, 'Resposta antiga.', '[]', 'llm_composed', 'claude-legacy', "
        "'provider_reported', NULL, 0.8, '2026-01-01 00:00:00')",
        (final_answer_id, run_id),
    )


@pytest.mark.asyncio
async def test_pre_answer_blocks_database_gets_column_added(tmp_path):
    db_path = str(tmp_path / "pre_answer_blocks.db")
    await _make_pre_answer_blocks_db(db_path)

    conn = sqlite3.connect(db_path)
    columns_before = {c[1] for c in conn.execute("PRAGMA table_info(final_answers)")}
    assert "answer_blocks_json" not in columns_before
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns_after = {c[1] for c in conn.execute("PRAGMA table_info(final_answers)")}
    assert "answer_blocks_json" in columns_after
    conn.close()


@pytest.mark.asyncio
async def test_pre_answer_blocks_final_answer_gets_null_never_reconstructed_from_answer_text(tmp_path):
    """Nenhum backfill -- não existe forma honesta de reconstruir
    `answer_blocks` a partir de `answer_text` já persistido (ver
    docstring de `_upgrade_legacy_answer_blocks`, app/storage/database.py).
    NULL é o único valor honesto."""
    db_path = str(tmp_path / "pre_answer_blocks.db")
    await _make_pre_answer_blocks_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    _seed_final_answer(conn, run_id="run1", final_answer_id="fa1")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    value = conn.execute(
        "SELECT answer_blocks_json FROM final_answers WHERE id = 'fa1'"
    ).fetchone()[0]
    conn.close()
    assert value is None


@pytest.mark.asyncio
async def test_answer_blocks_upgrade_does_not_touch_other_legacy_columns(tmp_path):
    """Upgrades independentes -- adicionar `answer_blocks_json` nunca
    deveria tocar `answer_text`/`limitations_json`/`status` já
    persistidos."""
    db_path = str(tmp_path / "pre_answer_blocks.db")
    await _make_pre_answer_blocks_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    _seed_final_answer(conn, run_id="run1", final_answer_id="fa1")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    answer_text, status = conn.execute(
        "SELECT answer_text, status FROM final_answers WHERE id = 'fa1'"
    ).fetchone()
    conn.close()
    assert answer_text == "Resposta antiga."
    assert status == "llm_composed"


@pytest.mark.asyncio
async def test_fresh_database_does_not_run_pre_answer_blocks_upgrade_path(tmp_path):
    """Um banco que nasce já com a coluna (via `create_all()`) nunca
    exercita o branch de `ALTER TABLE` -- mesma disciplina das outras
    verificações `test_fresh_database_does_not_run_*_upgrade_path`."""
    db_path = str(tmp_path / "fresh.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns = {c[1] for c in conn.execute("PRAGMA table_info(final_answers)")}
    conn.close()
    assert "answer_blocks_json" in columns


@pytest.mark.asyncio
async def test_answer_blocks_upgrade_is_idempotent_across_repeated_init_db(tmp_path):
    db_path = str(tmp_path / "pre_answer_blocks_idempotent.db")
    await _make_pre_answer_blocks_db(db_path)

    conn = sqlite3.connect(db_path)
    _seed_council_run(conn, run_id="run1")
    _seed_final_answer(conn, run_id="run1", final_answer_id="fa1")
    conn.commit()
    conn.close()

    for _ in range(3):
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)
        await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns = {c[1] for c in conn.execute("PRAGMA table_info(final_answers)")}
    (value,) = conn.execute(
        "SELECT answer_blocks_json FROM final_answers WHERE id = 'fa1'"
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM final_answers").fetchone()[0]
    conn.close()
    assert "answer_blocks_json" in columns
    assert value is None
    assert count == 1
