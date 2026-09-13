from __future__ import annotations

import pytest
from sqlalchemy import text

from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.models import Base


@pytest.mark.asyncio
async def test_foreign_keys_are_actually_enforced():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA foreign_keys"))
        assert result.scalar() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_is_idempotent():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    await init_db(engine)
    await init_db(engine)

    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result.all()}

    assert "council_runs" in tables
    assert "model_responses" in tables
    assert "quorum_failures" in tables
    assert "accepted_runs" in tables  # T02.4

    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_creates_accepted_runs_table_on_pre_t02_4_database():
    """T02.4 -- `accepted_runs` é uma tabela NOVA (nunca existiu antes
    deste slice), não uma coluna nova numa tabela existente. Um banco
    criado por uma versão anterior do código (sem esta tabela) precisa
    ganhá-la ao rodar `init_db()` de novo, sem nenhum ajuste manual --
    `create_all()` já cobre isso nativamente (`CREATE TABLE IF NOT
    EXISTS`), diferente do caso de coluna nova (que exige os
    `_upgrade_legacy_*` direcionados)."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")

    pre_t02_4_tables = [
        table for name, table in Base.metadata.tables.items() if name != "accepted_runs"
    ]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=pre_t02_4_tables)
        )

    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables_before = {row[0] for row in result.all()}
    assert "accepted_runs" not in tables_before
    assert "council_runs" in tables_before  # confirma que o banco "antigo" é genuíno

    await init_db(engine)

    async with session_factory() as session:
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables_after = {row[0] for row in result.all()}
    assert "accepted_runs" in tables_after

    await engine.dispose()
