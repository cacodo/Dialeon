from __future__ import annotations

import pytest_asyncio

from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.repository import CouncilRepository


@pytest_asyncio.fixture
async def engine():
    eng = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def repo(engine):
    return CouncilRepository(make_session_factory(engine))
