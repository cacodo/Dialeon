"""Leitura de runs do banco LOCAL do desenvolvedor para os testes de "evidência
local" (todos `skipif` quando o banco não existe).

O banco real NUNCA é aberto para escrita: ele é COPIADO para um diretório
temporário e a cópia recebe `init_db` (os upgrades aditivos de schema que o
bootstrap real aplicaria), de modo que o teste não depende do estado de
migração do arquivo do desenvolvedor."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.storage.database import create_engine, init_db
from app.storage.repository import CouncilRepository


async def load_local_run(db_path: Path, run_id: str, workdir: Path):
    copy = workdir / "local_evidence_copy.db"
    shutil.copyfile(db_path, copy)
    engine = create_engine(f"sqlite+aiosqlite:///{copy}")
    try:
        await init_db(engine)
        repo = CouncilRepository(async_sessionmaker(engine, expire_on_commit=False))
        return await repo.get_run(run_id)
    finally:
        await engine.dispose()
