from __future__ import annotations

import pytest
from sqlalchemy import text

from app.storage.database import create_engine, init_db, make_session_factory


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

    await engine.dispose()
