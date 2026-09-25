"""Repair M2 -- contrato de fragmento de auditoria no domínio e na
persistence boundary.

- Construção validada (provider path, reload, programática) degrada o
  fragmento fora do contrato no próprio domínio, com motivo explícito.
- Construção que CONTORNA a validação (`model_copy(update=...)`) nunca
  leva o fragmento ao bind JSON: o serializer degrada explicitamente.
- Save + commit + reload funcionam com a representação degradada.
- Bancos legados ganham as colunas novas com NULL (nunca reinterpretados).
"""

from __future__ import annotations

import json
import math
import sqlite3

import pytest
from pydantic import ValidationError

from app.presentation.mappers import completed_run_audit
from app.debate.numeric_verification import DeterministicVerificationAttempt, build_verification_attempt
from app.source_analysis.analyzer import _build_claim_results
from app.source_analysis.models import RejectedSourceEntry
from app.source_analysis.schemas import SourceAnalysisOutput
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import CompletedRunRecord
from app.storage.repository import CouncilRepository
from tests.storage.fixtures import now, run_config, very_rich_council_run_result
from tests.api.helpers import TEST_PROVIDER_EXECUTION_POLICY


def _nested(depth: int) -> list:
    value: list = []
    for _ in range(depth - 1):
        value = [value]
    return value


DEEP = _nested(970)


# ---------------------------------------------------------------------------
# Domínio
# ---------------------------------------------------------------------------


def test_provider_path_numeric_attempt_is_degraded_explicitly():
    attempt = build_verification_attempt("claim-1", DEEP)

    assert attempt.state == "invalid_proposal"
    assert attempt.raw_proposal is None
    assert attempt.raw_proposal_omitted_reason == "complexity_limit_exceeded"


def test_provider_path_numeric_attempt_with_non_json_float_is_degraded():
    attempt = build_verification_attempt("claim-1", {"left": math.nan})

    assert attempt.raw_proposal is None
    assert attempt.raw_proposal_omitted_reason == "non_json_value"


def test_normal_numeric_attempts_are_unchanged():
    invalid = build_verification_attempt("c", {"left": "x"})
    valid = build_verification_attempt(
        "c", {"left": "2", "operator": "+", "right": "2", "asserted_result": "4"}
    )

    assert (invalid.raw_proposal, invalid.raw_proposal_omitted_reason) == ({"left": "x"}, None)
    assert valid.state == "supports"
    assert (valid.raw_proposal, valid.raw_proposal_omitted_reason) == (None, None)


def test_programmatic_numeric_construction_goes_through_the_same_contract():
    attempt = DeterministicVerificationAttempt(
        claim_id="c", state="invalid_proposal", raw_proposal={"deep": DEEP}
    )

    assert attempt.raw_proposal is None
    assert attempt.raw_proposal_omitted_reason == "complexity_limit_exceeded"


@pytest.mark.parametrize(
    "kwargs",
    [
        # omissão declarada não pode carregar o fragmento
        {"state": "invalid_proposal", "raw_proposal": [1], "raw_proposal_omitted_reason": "complexity_limit_exceeded"},
        # só invalid_proposal tem proposta crua a omitir
        {"state": "supports", "raw_proposal_omitted_reason": "complexity_limit_exceeded"},
        # motivo fora do vocabulário estável
        {"state": "invalid_proposal", "raw_proposal_omitted_reason": "RecursionError"},
    ],
)
def test_incoherent_numeric_omission_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        DeterministicVerificationAttempt(claim_id="c", **kwargs)


def test_rejected_source_entry_programmatic_construction_is_degraded():
    entry = RejectedSourceEntry(claim_id=None, reason="invalid_entry", raw_entry={"x": DEEP})

    assert entry.raw_entry is None
    assert entry.raw_entry_omitted_reason == "complexity_limit_exceeded"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reason": "omitted_by_model", "raw_entry_omitted_reason": "complexity_limit_exceeded"},
        {"reason": "invalid_entry", "raw_entry": {"a": 1}, "raw_entry_omitted_reason": "non_json_value"},
    ],
)
def test_incoherent_source_omission_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        RejectedSourceEntry(claim_id=None, **kwargs)


def test_analyzer_degrades_only_the_offending_entries():
    ok = {"claim_id": "c1", "relation": "unresolved"}
    duplicate_a = {"claim_id": "c2", "relation": "unresolved"}
    duplicate_b = {"claim_id": "c2", "relation": "supports", "deep": DEEP}
    unknown_deep = {"claim_id": "zzz", "deep": DEEP}
    unknown_ok = {"claim_id": "yyy", "relation": "supports"}
    parsed = SourceAnalysisOutput(
        claim_relations=[ok, duplicate_a, duplicate_b, unknown_deep, unknown_ok]
    )

    results = _build_claim_results(parsed, {"c1", "c2"}, "fonte")

    by_key = {(r.kind, r.claim_id, getattr(r, "reason", None)): r for r in results if r.claim_id != "zzz"}
    assert by_key[("relation", "c1", None)].relation == "unresolved"
    duplicate = by_key[("rejected", "c2", "duplicate_claim_id")]
    assert (duplicate.raw_entry, duplicate.raw_entry_omitted_reason) == (None, "complexity_limit_exceeded")
    unknown = [r for r in results if r.kind == "rejected" and r.claim_id is None]
    assert [(u.raw_entry, u.raw_entry_omitted_reason) for u in unknown] == [
        (None, "complexity_limit_exceeded"),
        (unknown_ok, None),
    ]


# ---------------------------------------------------------------------------
# Persistence boundary: bypass da validação + save + reload
# ---------------------------------------------------------------------------


def _bypass(result, *, raw_proposal, raw_entry):
    """Injeta fragmentos fora do contrato SEM passar pela validação
    (`model_copy(update=...)` não valida) -- simula construção que
    contorna o caminho normal."""
    debate = result.debate_result
    numeric = list(debate.numeric_verification_attempts)
    index = next(i for i, a in enumerate(numeric) if a.state == "invalid_proposal")
    numeric[index] = numeric[index].model_copy(update={"raw_proposal": raw_proposal})
    source = result.source_analysis_result
    claims = list(source.claim_results)
    rejected_index = next(
        i for i, c in enumerate(claims) if c.kind == "rejected" and c.reason == "invalid_entry"
    )
    claims[rejected_index] = claims[rejected_index].model_copy(update={"raw_entry": raw_entry})
    bypassed = result.model_copy(
        update={
            "debate_result": debate.model_copy(update={"numeric_verification_attempts": numeric}),
            "source_analysis_result": source.model_copy(update={"claim_results": claims}),
        }
    )
    assert bypassed.debate_result.numeric_verification_attempts[index].raw_proposal is raw_proposal
    return bypassed, numeric[index].id, claims[rejected_index].id


@pytest.mark.parametrize(
    ("fragment", "reason"),
    [(DEEP, "complexity_limit_exceeded"), ({"x": math.inf}, "non_json_value")],
)
async def test_bypassed_fragment_is_degraded_before_bind_and_reloads(repo, fragment, reason):
    original = very_rich_council_run_result()
    bypassed, attempt_id, entry_id = _bypass(original, raw_proposal=fragment, raw_entry={"x": fragment})

    await repo.save_success(bypassed)
    record = await repo.get_run(bypassed.id)

    assert isinstance(record, CompletedRunRecord)
    reloaded = record.council_run_result
    attempt = next(a for a in reloaded.debate_result.numeric_verification_attempts if a.id == attempt_id)
    assert (attempt.raw_proposal, attempt.raw_proposal_omitted_reason) == (None, reason)
    entry = next(c for c in reloaded.source_analysis_result.claim_results if c.id == entry_id)
    assert (entry.raw_entry, entry.raw_entry_omitted_reason) == (None, reason)
    # o resto do histórico é idêntico ao original
    assert len(reloaded.debate_result.claim_processing_attempts) == len(
        original.debate_result.claim_processing_attempts
    )
    assert reloaded.total_cost_usd == pytest.approx(original.total_cost_usd)
    assert reloaded.model_dump(mode="json")["source_analysis_result"]["attempts"] == original.model_dump(
        mode="json"
    )["source_analysis_result"]["attempts"]


async def test_normal_fragments_roundtrip_unchanged(repo):
    original = very_rich_council_run_result()

    await repo.save_success(original)
    record = await repo.get_run(original.id)

    assert record.council_run_result.model_dump(mode="json") == original.model_dump(mode="json")
    assert any(
        a.raw_proposal is not None for a in record.council_run_result.debate_result.numeric_verification_attempts
    )


# ---------------------------------------------------------------------------
# Banco legado
# ---------------------------------------------------------------------------


async def test_legacy_database_gains_columns_as_null_without_reinterpretation(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    repo = CouncilRepository(make_session_factory(engine))
    original = very_rich_council_run_result()
    await repo.save_success(original)
    await repo.save_accepted(
        "legacy-failed", run_config=run_config(), started_at=now(),
        provider_execution_policy=TEST_PROVIDER_EXECUTION_POLICY,
    )
    await repo.save_unexpected_failure(
        "legacy-failed", failed_at=now(), failure_classification="WeirdBug",
        failure_message="Erro interno inesperado durante a execução.",
    )
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    for table, column in (
        ("deterministic_verification_attempts", "raw_proposal_omitted_reason"),
        ("source_claim_analysis_results", "raw_entry_omitted_reason"),
        ("accepted_runs", "failure_stage"),
    ):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
        await init_db(engine)  # idempotente
        repo = CouncilRepository(make_session_factory(engine))
        completed = await repo.get_run(original.id)
        failed = await repo.get_run("legacy-failed")
    finally:
        await engine.dispose()

    assert completed.council_run_result.model_dump(mode="json") == original.model_dump(mode="json")
    result = completed.council_run_result
    assert all(a.raw_proposal_omitted_reason is None for a in result.debate_result.numeric_verification_attempts)
    assert all(
        c.raw_entry_omitted_reason is None
        for c in result.source_analysis_result.claim_results
        if c.kind == "rejected"
    )
    assert failed.status == "failed"
    assert failed.failure_stage is None  # nunca registrado -> desconhecido, não inferido


_LEGACY_TEXTS = {
    # Gravável pelo baseline em 3.13/3.14; o decoder JSON genérico do
    # SQLAlchemy estoura RecursionError relendo isto em 3.11.
    "deep_1500": ("[" * 1500 + "]" * 1500, "complexity_limit_exceeded"),
    "deep_200000": ("[" * 200_000 + "]" * 200_000, "complexity_limit_exceeded"),
    "deep_300_dict": ('{"a": ' * 300 + "1" + "}" * 300, "complexity_limit_exceeded"),
    # Escape de substituto isolado: é o que o bind grava (ensure_ascii).
    "surrogate_value": ('{"x": "\\ud800"}', "non_json_value"),
    "surrogate_key": ('{"\\udfff": 1}', "non_json_value"),
    # Inteiro que só um processo com limite de dígitos elevado gravaria.
    "int_5001_digits": ('{"x": 1' + "0" * 5000 + "}", "non_json_value"),
    "int_above_int64": ('{"x": 100000000000000000000}', "non_json_value"),
}


async def _legacy_db_with_raw_texts(tmp_path, text: str, *, drop_reason_columns=("raw_proposal_omitted_reason", "raw_entry_omitted_reason")):
    db_path = tmp_path / "legacy-raw.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    original = very_rich_council_run_result()
    await CouncilRepository(make_session_factory(engine)).save_success(original)
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    tables = {
        "raw_proposal_omitted_reason": "deterministic_verification_attempts",
        "raw_entry_omitted_reason": "source_claim_analysis_results",
    }
    for column in drop_reason_columns:
        conn.execute(f"ALTER TABLE {tables[column]} DROP COLUMN {column}")
    conn.execute(
        "UPDATE deterministic_verification_attempts SET raw_proposal_json = ? WHERE state = 'invalid_proposal'",
        (text,),
    )
    conn.execute(
        "UPDATE source_claim_analysis_results SET raw_entry_json = ? WHERE reason = 'invalid_entry'",
        (text,),
    )
    conn.commit()
    conn.close()
    return db_path, original


def _raw_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return (
            conn.execute(
                "SELECT id, raw_proposal_json, raw_proposal_omitted_reason FROM deterministic_verification_attempts ORDER BY id"
            ).fetchall(),
            conn.execute(
                "SELECT id, raw_entry_json, raw_entry_omitted_reason FROM source_claim_analysis_results ORDER BY id"
            ).fetchall(),
        )
    finally:
        conn.close()


@pytest.mark.parametrize("name", sorted(_LEGACY_TEXTS))
async def test_legacy_row_outside_the_contract_is_read_degraded_and_never_rewritten(tmp_path, name):
    """Antes do repair, fragmentos fora do contrato podiam estar gravados
    (em 3.13/3.14 o save aceitava milhares de níveis). A leitura decodifica
    sob controle da aplicação ANTES do json.loads recursivo: nenhum
    RecursionError em nenhum Python, domínio válido, degradação explícita,
    API renderizável -- e a linha histórica fica byte a byte como estava."""
    text, reason = _LEGACY_TEXTS[name]
    db_path, original = await _legacy_db_with_raw_texts(tmp_path, text)

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
        before = _raw_rows(db_path)
        record = await CouncilRepository(make_session_factory(engine)).get_run(original.id)
        await CouncilRepository(make_session_factory(engine)).list_runs()
    finally:
        await engine.dispose()

    result = record.council_run_result
    [attempt] = [a for a in result.debate_result.numeric_verification_attempts if a.state == "invalid_proposal"]
    assert (attempt.raw_proposal, attempt.raw_proposal_omitted_reason) == (None, reason)
    [entry] = [
        c for c in result.source_analysis_result.claim_results if c.kind == "rejected" and c.reason == "invalid_entry"
    ]
    assert (entry.raw_entry, entry.raw_entry_omitted_reason) == (None, reason)
    audit = completed_run_audit(result, provider_execution_policy=record.provider_execution_policy)
    json.dumps(json.loads(audit.model_dump_json()), allow_nan=False)
    # o resto do run histórico é equivalente ao original
    assert result.total_cost_usd == pytest.approx(original.total_cost_usd)
    assert len(result.debate_result.claims) == len(original.debate_result.claims)
    # leitura nunca reescreve: o texto original continua lá, sem motivo inventado
    after = _raw_rows(db_path)
    assert after == before
    assert {row[1] for row in after[0] if row[1] == text} == {text}
    assert all(row[2] is None for row in after[0] + after[1])


async def test_partially_migrated_database_upgrades_and_reads(tmp_path):
    """Só uma das colunas de motivo existe (upgrade interrompido): o
    upgrade idempotente completa a outra e a leitura funciona."""
    text, reason = _LEGACY_TEXTS["deep_1500"]
    db_path, original = await _legacy_db_with_raw_texts(
        tmp_path, text, drop_reason_columns=("raw_entry_omitted_reason",)
    )

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
        await init_db(engine)
        record = await CouncilRepository(make_session_factory(engine)).get_run(original.id)
    finally:
        await engine.dispose()

    result = record.council_run_result
    assert {a.raw_proposal_omitted_reason for a in result.debate_result.numeric_verification_attempts} >= {reason}


async def test_new_writes_keep_the_exact_legacy_storage_representation(tmp_path):
    """A escrita não mudou: mesmo DDL `JSON`, `None` como o texto 'null',
    o mesmo texto de json.dumps."""
    db_path = tmp_path / "repr.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
        await CouncilRepository(make_session_factory(engine)).save_success(very_rich_council_run_result())
    finally:
        await engine.dispose()

    conn = sqlite3.connect(db_path)
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'deterministic_verification_attempts'"
    ).fetchone()[0]
    stored = conn.execute(
        "SELECT state, typeof(raw_proposal_json), raw_proposal_json FROM deterministic_verification_attempts ORDER BY state"
    ).fetchall()
    conn.close()
    assert "raw_proposal_json JSON" in ddl
    assert ("supports", "text", "null") in stored
    assert ("invalid_proposal", "text", json.dumps({"left": "2", "operator": "%%%", "right": "2", "asserted_result": "4"})) in stored


async def test_the_read_sentinel_can_never_be_written_back():
    from app.storage.audit_fragment_column import AuditFragmentJSON, OmittedStoredAuditFragment
    from sqlalchemy.dialects import sqlite

    bind = AuditFragmentJSON().bind_processor(sqlite.dialect())
    with pytest.raises(TypeError):
        bind(OmittedStoredAuditFragment("complexity_limit_exceeded"))
