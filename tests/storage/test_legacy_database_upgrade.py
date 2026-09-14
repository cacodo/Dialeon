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
