"""Council Local Execution Readiness & Admission V1 -- persistência dos fatos
de aceite: coluna aditiva `council_admission_json` nas 3 tabelas de envelope
de aceite, sem backfill; NULL = fato não capturado; blob malformado falha
fechado; o valor do aceite é copiado verbatim pro registro terminal."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.council.readiness import (
    CouncilAdmission,
    CouncilExecutionDependencies,
    evaluate_council_readiness,
)
from app.models.provider_models import ProviderExecutionPolicy
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import AcceptedRunRecord, CompletedRunRecord, QuorumFailureRecord
from app.storage.repository import CouncilRepository
from tests.storage.fixtures import full_council_run_result, now, quorum_failure_exception, run_config

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
_TABLES = ("accepted_runs", "council_runs", "quorum_failures")


def _admission(**states) -> CouncilAdmission:
    rc = run_config()
    local = {name: "met" for name in rc.all_provider_authorities} | states
    readiness = evaluate_council_readiness(
        CouncilExecutionDependencies.from_run_config(rc),
        local_prerequisites=local,
        configured_default_models={name: f"{name}-configured" for name in rc.all_provider_authorities},
    )
    return CouncilAdmission(mode="standard", known_degradation_acknowledged=True, readiness=readiness)


async def _columns(engine, table):
    async with engine.connect() as conn:
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
    return {row[1] for row in rows}


@pytest.mark.asyncio
async def test_pre_v140_database_is_upgraded_additively_and_historical_runs_read_as_not_captured(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/legacy.db"
    engine = create_engine(db_url)
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    completed = full_council_run_result()
    await repository.save_accepted(
        completed.id, run_config=completed.run_config, started_at=now(), provider_execution_policy=POLICY
    )
    await repository.save_success(completed)
    await repository.save_accepted(
        "running-legacy", run_config=run_config(), started_at=now(), provider_execution_policy=POLICY
    )
    await repository.save_accepted(
        "quorum-legacy", run_config=run_config(), started_at=now(), provider_execution_policy=POLICY
    )
    await repository.save_quorum_failure(
        quorum_failure_exception(),
        run_config=run_config(),
        started_at=now(),
        failed_at=now(),
        run_id="quorum-legacy",
    )
    # banco anterior a este slice: nenhuma das 3 tabelas tem a coluna
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN council_admission_json"))
    await engine.dispose()

    engine = create_engine(db_url)
    await init_db(engine)  # upgrade aditivo, sem backfill
    repository = CouncilRepository(make_session_factory(engine))
    records = {rid: await repository.get_run(rid) for rid in (completed.id, "running-legacy", "quorum-legacy")}
    stored = {}
    async with engine.connect() as conn:
        for table in _TABLES:
            stored[table] = (
                await conn.execute(text(f"SELECT council_admission_json FROM {table}"))
            ).scalars().all()
    columns = {table: await _columns(engine, table) for table in _TABLES}
    await engine.dispose()

    assert all("council_admission_json" in cols for cols in columns.values())
    assert all(values and set(values) == {None} for values in stored.values())  # nunca backfillado
    assert isinstance(records[completed.id], CompletedRunRecord)
    assert isinstance(records["running-legacy"], AcceptedRunRecord)
    assert isinstance(records["quorum-legacy"], QuorumFailureRecord)
    assert {r.council_admission for r in records.values()} == {None}


@pytest.mark.asyncio
async def test_repeated_init_is_idempotent():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    await init_db(engine)
    columns = {table: await _columns(engine, table) for table in _TABLES}
    await engine.dispose()

    assert all("council_admission_json" in cols for cols in columns.values())


@pytest.mark.asyncio
async def test_accepted_facts_are_copied_verbatim_to_the_completed_record():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    completed = full_council_run_result()
    admission = _admission(anthropic="missing")
    await repository.save_accepted(
        completed.id,
        run_config=completed.run_config,
        started_at=now(),
        provider_execution_policy=POLICY,
        council_admission=admission,
    )
    await repository.save_success(completed)
    record = await repository.get_run(completed.id)
    async with engine.connect() as conn:
        accepted_left = (await conn.execute(text("SELECT COUNT(*) FROM accepted_runs"))).scalar_one()
    await engine.dispose()

    assert accepted_left == 0
    assert record.council_admission == admission


@pytest.mark.asyncio
async def test_a_malformed_persisted_admission_fails_closed_instead_of_being_normalized():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    await repository.save_accepted(
        "malformada",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=POLICY,
        council_admission=_admission(),
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE accepted_runs SET council_admission_json = "
                "json_set(council_admission_json, '$.readiness.dependencies[0].local_prerequisite', 'maybe')"
            )
        )

    with pytest.raises(ValidationError):
        await repository.get_run("malformada")
    await engine.dispose()
