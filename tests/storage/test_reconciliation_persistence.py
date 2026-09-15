"""
Persistência de Cross-Channel Reconciliation V1 -- round-trip completo via
o repositório real, ordem EXATA de `source_claim_result_ids`, versão de
contrato, e compatibilidade com um banco genuinamente anterior a este
slice (três tabelas TOTALMENTE NOVAS, nunca uma coluna adicionada a
tabela existente -- ver docstring de `SourceJudgeReconciliationRow`,
app/storage/models.py).

Repair (revisão adversarial), seções 10/11/12 da missão de reparo --
esta árvore de persistência não tinha nenhum teste dedicado antes deste
arquivo (só exercício incidental via `full_council_run_result()` com um
único claim, nos testes gerais de round-trip de `test_repository.py`).
"""

from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.debate.claims import get_current_claims
from app.reconciliation.models import CONTRACT_VERSION
from app.reconciliation.reconcile import reconcile_source_and_judge
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.models import (
    ClaimReconciliationOutcomeRow,
    ClaimReconciliationSourceResultRow,
)
from app.storage.repository import CouncilRepository
from tests.editor.fixtures import source_analysis_result, source_relation
from tests.storage.fixtures import full_council_run_result, very_rich_council_run_result

_RECONCILIATION_TABLES = [
    "source_judge_reconciliations",
    "claim_reconciliation_outcomes",
    "claim_reconciliation_source_results",
]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Round-trip básico
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_round_trip_preserves_all_fields(tmp_path):
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    repo = CouncilRepository(make_session_factory(engine))

    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    original = result.reconciliation
    reloaded = loaded.reconciliation

    assert reloaded is not None
    assert reloaded.contract_version == original.contract_version == CONTRACT_VERSION
    assert reloaded.status == original.status
    assert len(reloaded.claim_outcomes) == len(original.claim_outcomes)
    for expected, actual in zip(original.claim_outcomes, reloaded.claim_outcomes):
        assert actual.claim_id == expected.claim_id
        assert actual.judge_verdict_id == expected.judge_verdict_id
        assert actual.source_state == expected.source_state
        assert actual.channel_relationship == expected.channel_relationship
        assert actual.source_claim_result_ids == expected.source_claim_result_ids

    await engine.dispose()


# ---------------------------------------------------------------------------
# Seção 11 -- ordem NÃO-lexicográfica de source_claim_result_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_multi_id_round_trip_preserves_nonlexical_order(tmp_path):
    """Um outcome com múltiplos source_claim_result_ids em ordem
    deliberadamente NÃO-lexicográfica ("z-id" antes de "a-id") precisa
    sobreviver EXATAMENTE ao round-trip real via repositório -- nunca
    reordenada por PK/ordenação implícita do SQLite (que ordenaria
    "a-id" antes de "z-id" alfabeticamente)."""
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    repo = CouncilRepository(make_session_factory(engine))

    base = full_council_run_result()
    c1 = base.debate_result.claims[0]
    rel_z = source_relation(c1.id, "supports", id="z-id", excerpt="trecho z")
    rel_a = source_relation(c1.id, "supports", id="a-id", excerpt="trecho a")
    sa = source_analysis_result([rel_z, rel_a])
    reconciliation = reconcile_source_and_judge(
        get_current_claims(base.debate_result.claims), base.judge_result, sa
    )
    assert reconciliation.claim_outcomes[0].source_claim_result_ids == ("z-id", "a-id")

    result = base.model_copy(
        update={"source_analysis_result": sa, "reconciliation": reconciliation}
    )
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    reloaded_outcome = loaded.reconciliation.claim_outcomes[0]

    assert reloaded_outcome.source_claim_result_ids == ("z-id", "a-id")
    assert reloaded_outcome.source_state == reconciliation.claim_outcomes[0].source_state
    assert (
        reloaded_outcome.channel_relationship
        == reconciliation.claim_outcomes[0].channel_relationship
    )
    assert reloaded_outcome.claim_id == c1.id
    assert reloaded_outcome.judge_verdict_id == base.judge_result.verdict.id

    await engine.dispose()


# ---------------------------------------------------------------------------
# Seção 12 -- contract_version desconhecido/futuro falha fechado na
# reconstrução, nunca substituído silenciosamente pelo default v1 atual.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconstruction_fails_closed_on_unsupported_contract_version(tmp_path):
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    repo = CouncilRepository(make_session_factory(engine))

    result = full_council_run_result()
    await repo.save_success(result)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE source_judge_reconciliations SET contract_version = 'source_judge_reconciliation_v2'"
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_absence_of_root_row_is_none_never_confused_with_bad_version(tmp_path):
    """Ausência histórica da linha raiz (`None`, ver seção 10 abaixo) é
    um caso ESTRUTURALMENTE diferente de uma linha presente com
    contract_version inválido -- o primeiro reconstrói honestamente como
    `None`, o segundo falha fechado. Nunca confundidos."""
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    repo = CouncilRepository(make_session_factory(engine))

    result = full_council_run_result()
    await repo.save_success(result)

    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM claim_reconciliation_source_results")
    conn.execute("DELETE FROM claim_reconciliation_outcomes")
    conn.execute("DELETE FROM source_judge_reconciliations")
    conn.commit()
    conn.close()

    loaded = (await repo.get_run(result.id)).council_run_result
    assert loaded.reconciliation is None

    await engine.dispose()


# ---------------------------------------------------------------------------
# Seção 10 -- upgrade de banco genuinamente pré-slice: as 3 tabelas de
# reconciliação NÃO EXISTEM (nunca uma coluna adicionada a tabela
# existente) -- `init_db()` precisa criá-las de graça via `create_all()`,
# sem tocar em nenhum dado histórico, sem inventar nenhuma linha nova.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_reconciliation_database_gets_the_three_new_tables(tmp_path):
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)

    repo = CouncilRepository(make_session_factory(engine))
    result = full_council_run_result()
    await repo.save_success(result)
    await engine.dispose()

    # Simula um banco genuinamente anterior a este slice: as 3 tabelas
    # nunca existiram (DROP, não só linhas vazias) -- exatamente o estado
    # real de um banco Stage-anterior, nunca fabricado a partir do schema
    # atual com linhas apagadas.
    conn = sqlite3.connect(db_path)
    tables_before = _table_names(conn)
    assert set(_RECONCILIATION_TABLES) <= tables_before
    table_count_with_reconciliation = len(tables_before)
    for table in _RECONCILIATION_TABLES:
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    tables_after_drop = _table_names(conn)
    assert table_count_with_reconciliation - len(tables_after_drop) == 3
    assert not (set(_RECONCILIATION_TABLES) & tables_after_drop)
    # A run histórica (council_runs/claims/judge_verdicts/final_answers
    # etc.) continua intacta -- só as 3 tabelas de reconciliação sumiram.
    historical_claim_count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    assert historical_claim_count == len(result.debate_result.claims)
    conn.close()

    # Reinicialização real -- create_all() de graça, nenhum
    # `_upgrade_legacy_*` necessário (tabelas totalmente novas).
    engine2 = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine2)

    conn = sqlite3.connect(db_path)
    tables_after_reinit = _table_names(conn)
    assert set(_RECONCILIATION_TABLES) <= tables_after_reinit
    # Nenhuma linha foi inventada/"backfilled" pras 3 tabelas novas --
    # a run histórica (persistida ANTES do DROP simular ausência) não
    # ganha reconciliação nenhuma retroativamente.
    for table in _RECONCILIATION_TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0
    conn.close()

    repo2 = CouncilRepository(make_session_factory(engine2))
    loaded = (await repo2.get_run(result.id)).council_run_result
    assert loaded.reconciliation is None
    # Claims/veredito/resposta final históricos continuam intactos.
    assert len(loaded.debate_result.claims) == len(result.debate_result.claims)
    assert loaded.judge_result.verdict is not None
    await engine2.dispose()

    # Idempotência -- reinicializar de novo não duplica/altera nada.
    engine3 = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine3)
    conn = sqlite3.connect(db_path)
    for table in _RECONCILIATION_TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0
    assert _table_names(conn) == tables_after_reinit
    conn.close()
    await engine3.dispose()


# ---------------------------------------------------------------------------
# Seção 9 -- constraints de unicidade a nível de banco nas 3 tabelas NOVAS
# (só puderam ser adicionadas agora, antes do primeiro commit deste
# slice -- ver docstrings de UniqueConstraint em app/storage/models.py).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_claim_id_within_same_reconciliation_violates_db_constraint(tmp_path):
    """A: um outcome por claim dentro de uma mesma reconciliação --
    UniqueConstraint(reconciliation_id, claim_id)."""
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    session_factory = make_session_factory(engine)
    repo = CouncilRepository(session_factory)

    result = full_council_run_result()
    await repo.save_success(result)
    reconciliation_id = result.reconciliation.id
    existing_claim_id = result.reconciliation.claim_outcomes[0].claim_id

    async with session_factory() as session:
        session.add(
            ClaimReconciliationOutcomeRow(
                id=str(uuid4()),
                reconciliation_id=reconciliation_id,
                claim_id=existing_claim_id,  # duplicado -- mesma claim
                position=99,
                judge_verdict_id=None,
                source_state="not_supplied",
                channel_relationship="not_comparable",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_position_within_same_reconciliation_violates_db_constraint(tmp_path):
    """B: um outcome por posição dentro de uma mesma reconciliação --
    UniqueConstraint(reconciliation_id, position). Usa
    `very_rich_council_run_result()` (>=2 claims correntes reais) pra ter
    um segundo claim_id genuíno disponível."""
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    session_factory = make_session_factory(engine)
    repo = CouncilRepository(session_factory)

    result = very_rich_council_run_result()
    await repo.save_success(result)
    reconciliation_id = result.reconciliation.id
    outcomes = result.reconciliation.claim_outcomes
    assert len(outcomes) >= 2
    other_claim_id = outcomes[1].claim_id

    async with session_factory() as session:
        session.add(
            ClaimReconciliationOutcomeRow(
                id=str(uuid4()),
                reconciliation_id=reconciliation_id,
                claim_id=other_claim_id,
                position=0,  # duplicado -- mesma posição que outcomes[0]
                judge_verdict_id=None,
                source_state="not_supplied",
                channel_relationship="not_comparable",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_position_within_same_outcome_source_results_violates_db_constraint(
    tmp_path,
):
    """C: um link de source-result por posição dentro de um mesmo
    outcome -- UniqueConstraint(outcome_id, position). Usa
    `very_rich_council_run_result()` (>=2 SourceClaimAnalysisResult
    reais) pra ter um segundo source_claim_result_id genuíno."""
    db_path = str(tmp_path / "run.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    session_factory = make_session_factory(engine)
    repo = CouncilRepository(session_factory)

    result = very_rich_council_run_result()
    await repo.save_success(result)

    async with session_factory() as session:
        link_rows = (
            (await session.execute(select(ClaimReconciliationSourceResultRow)))
            .scalars()
            .all()
        )
        assert len(link_rows) >= 1
        existing_link = link_rows[0]
        all_source_result_ids = {
            r.id for r in result.source_analysis_result.claim_results
        }
        # Um source_claim_result_id real, mas DIFERENTE do já vinculado a
        # este outcome (nunca o mesmo -- isso violaria a PK composta, não
        # a constraint nova sob teste aqui).
        other_source_result_id = next(
            iter(all_source_result_ids - {existing_link.source_claim_result_id})
        )

        session.add(
            ClaimReconciliationSourceResultRow(
                outcome_id=existing_link.outcome_id,
                source_claim_result_id=other_source_result_id,
                position=existing_link.position,  # duplicado
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()
