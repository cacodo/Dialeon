"""
COMPATIBILIDADE HISTÓRICA depois da remoção de agrupamento (v1-v4) e
reconciliação cross-round (v1-v2) da execução corrente.

Runs antigas persistiram, e DEVEM continuar carregando/inspecionando/
apresentando/exportando SEM migração nem reinterpretação:
- `ClaimProcessingAttempt` de `grouping` / `reconciliation` (todas as versões
  de contrato, com raw output, status -- inclusive `accepted_normalized` --
  e digests de proveniência exatamente como gravados);
- claims canônicas destrutivas (`source_model_response_id=None`,
  `merged_from_claim_ids`, `support_scope_model_count`), cujas fontes
  continuam aposentadas por `get_current_claims`.

Nenhuma chamada de provider; nenhum dado fabricado retroativamente.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.debate.claims import get_current_claims
from app.debate.processing_record import ClaimProcessingAttempt
from app.models.domain import Claim, ClaimAssessment
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from app.presentation.mappers import claim_processing_attempt_public, completed_run_audit
from tests.storage.fixtures import full_council_run_result, with_recomputed_reconciliation

_GROUPING_V1_V3_RAW = (
    '{"groups": [{"member_claim_ids": ["a", "b"], "canonical_text": "texto sintetizado histórico"}], '
    '"ungrouped_claim_ids": ["c"]}'
)
_GROUPING_V4_RAW = '{"clusters": [["a", "b"], ["c"]]}'
_RECON_V1_RAW = (
    '{"groups": [{"member_claim_ids": ["a", "d"], "canonical_text": "texto v1"}], "ungrouped_claim_ids": []}'
)
_RECON_V2_RAW = '{"equivalence_clusters": [["a", "d"]]}'

# (operation, contract_version, raw_output_text, parse_status, round_number)
_HISTORICAL_ATTEMPTS = [
    ("grouping", "claim_grouping_v1", _GROUPING_V1_V3_RAW, "accepted", 1),
    ("grouping", "claim_grouping_v2", _GROUPING_V1_V3_RAW, "accepted", 1),
    ("grouping", "claim_grouping_v3", _GROUPING_V1_V3_RAW, "accepted_normalized", 1),
    ("grouping", "claim_grouping_v4", _GROUPING_V4_RAW, "accepted", 1),
    ("reconciliation", "cross_round_claim_reconciliation_v1", _RECON_V1_RAW, "accepted", 2),
    ("reconciliation", "cross_round_claim_reconciliation_v2", _RECON_V2_RAW, "accepted", 2),
]


def _historical_attempt(index: int, operation, version, raw, status, round_number) -> ClaimProcessingAttempt:
    base = full_council_run_result().debate_result.claim_processing_attempts[0]
    return ClaimProcessingAttempt(
        **{
            **base.model_dump(),
            "id": f"historical-{index}",
            "operation": operation,
            "round_number": round_number,
            "attempt_number": 1,
            "target_model_response_id": None,
            "target_claim_ids": ["a", "b", "c"] if operation == "grouping" else ["a", "d"],
            "transport_status": "success",
            "transport_error": None,
            "raw_output_text": raw,
            "parse_status": status,
            "parse_error_message": None,
            "request_provenance": RequestProvenance(
                contract_version=version, request_digest=REQUEST_DIGEST_PREFIX + f"{index:064x}"
            ),
        }
    )


def _historical_run():
    """Run "antiga": extração + TODAS as versões de agrupamento/reconciliação
    + uma claim canônica destrutiva (merge de duas extraídas)."""
    result = full_council_run_result()
    claims = list(result.debate_result.claims)
    base = claims[0]  # claim já existente na fixture: só empresta resposta-fonte/suporte
    merged_a, merged_b = (
        Claim(
            text=f"Claim bruta histórica {label} (fundida na canônica).",
            source_model_response_id=base.source_model_response_id,
            round_introduced=1,
            status="active",
            supporting_model_response_ids=list(base.supporting_model_response_ids),
            total_models_in_round=3,
        )
        for label in ("A", "B")
    )
    canonical = Claim(
        text="Claim canônica histórica (fusão destrutiva v1).",
        source_model_response_id=None,
        round_introduced=1,
        merged_from_claim_ids=[merged_a.id, merged_b.id],
        status="active",
        supporting_model_response_ids=list(base.supporting_model_response_ids),
        total_models_in_round=3,
        support_scope_model_count=3,
    )
    attempts = [
        *result.debate_result.claim_processing_attempts,
        *[_historical_attempt(i, *spec) for i, spec in enumerate(_HISTORICAL_ATTEMPTS)],
    ]
    debate = result.debate_result.model_copy(
        update={"claims": [*claims, merged_a, merged_b, canonical], "claim_processing_attempts": attempts}
    )
    # o Judge de uma run antiga avaliou a claim canônica (a corrente), não as fundidas
    verdict = result.judge_result.verdict
    verdict = verdict.model_copy(
        update={
            "claim_assessments": [
                *verdict.claim_assessments,
                ClaimAssessment(claim_id=canonical.id, verdict="supported", explanation="Fusão histórica."),
            ]
        }
    )
    judge = result.judge_result.model_copy(update={"verdict": verdict})
    historical = with_recomputed_reconciliation(
        result.model_copy(update={"debate_result": debate, "judge_result": judge})
    )
    return historical, canonical, (merged_a, merged_b)


@pytest.mark.asyncio
async def test_historical_grouping_and_reconciliation_attempts_reload_unchanged(repo):
    result, _canonical, _merged = _historical_run()

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = {
        a.id: a for a in loaded.debate_result.claim_processing_attempts if a.id.startswith("historical-")
    }
    assert len(reloaded) == len(_HISTORICAL_ATTEMPTS)
    for index, (operation, version, raw, status, round_number) in enumerate(_HISTORICAL_ATTEMPTS):
        attempt = reloaded[f"historical-{index}"]
        assert attempt.operation == operation
        assert attempt.round_number == round_number
        assert attempt.raw_output_text == raw  # resposta bruta original, nunca re-parseada
        assert attempt.parse_status == status  # inclusive `accepted_normalized` (v3)
        assert attempt.request_provenance == RequestProvenance(
            contract_version=version, request_digest=REQUEST_DIGEST_PREFIX + f"{index:064x}"
        )


@pytest.mark.asyncio
async def test_historical_destructive_canonical_claim_reloads_and_retires_its_sources(repo):
    result, canonical, (merged_a, merged_b) = _historical_run()

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(c for c in loaded.debate_result.claims if c.id == canonical.id)
    assert reloaded.source_model_response_id is None
    assert reloaded.merged_from_claim_ids == [merged_a.id, merged_b.id]
    assert reloaded.support_scope_model_count == 3
    assert reloaded.text == canonical.text
    # `get_current_claims` segue aposentando as fontes de uma fusão HISTÓRICA
    current_ids = {c.id for c in get_current_claims(loaded.debate_result.claims)}
    assert canonical.id in current_ids
    assert merged_a.id not in current_ids and merged_b.id not in current_ids


@pytest.mark.asyncio
async def test_historical_rows_are_stored_verbatim_no_rewrite_no_migration(repo):
    """As colunas persistidas de uma tentativa histórica são as gravadas:
    nada reescrito, nenhuma operação/status/versão reinterpretados."""
    result, _canonical, _merged = _historical_run()

    await repo.save_success(result)
    async with repo._session_factory() as session:  # leitura crua da linha persistida
        rows = (
            await session.execute(
                text(
                    "SELECT id, operation, parse_status, raw_output_text, request_provenance_json "
                    "FROM claim_processing_attempts WHERE id LIKE 'historical-%' ORDER BY id"
                )
            )
        ).all()

    by_id = {r[0]: r for r in rows}
    assert len(by_id) == len(_HISTORICAL_ATTEMPTS)
    for index, (operation, version, raw, status, _round) in enumerate(_HISTORICAL_ATTEMPTS):
        _id, db_operation, db_status, db_raw, db_provenance = by_id[f"historical-{index}"]
        assert (db_operation, db_status, db_raw) == (operation, status, raw)
        assert json.loads(db_provenance) == {
            "contract_version": version,
            "request_digest": REQUEST_DIGEST_PREFIX + f"{index:064x}",
        }


@pytest.mark.asyncio
async def test_public_audit_mapping_of_a_historical_run_exposes_everything_verbatim(repo):
    result, canonical, _merged = _historical_run()
    await repo.save_success(result)
    record = await repo.get_run(result.id)

    audit = completed_run_audit(
        record.council_run_result,
        provider_execution_policy=record.provider_execution_policy,
        default_model_authority_snapshot=record.default_model_authority_snapshot,
    )

    public = {a.id: a for a in audit.claim_processing_attempts if a.id.startswith("historical-")}
    for index, (operation, version, raw, status, _round) in enumerate(_HISTORICAL_ATTEMPTS):
        p = public[f"historical-{index}"]
        assert (p.operation, p.parse_status, p.raw_output_text) == (operation, status, raw)
        assert p.request_provenance.contract_version == version
        assert p.request_provenance.request_digest == REQUEST_DIGEST_PREFIX + f"{index:064x}"
    public_canonical = next(c for c in audit.claims if c.id == canonical.id)
    assert public_canonical.merged_from_claim_ids == canonical.merged_from_claim_ids
    assert public_canonical.support_scope_model_count == 3
    assert public_canonical.source_model_response_id is None
    # o mapper isolado de tentativa continua sem tradução de status
    mapped = claim_processing_attempt_public(
        next(a for a in record.council_run_result.debate_result.claim_processing_attempts if a.id == "historical-2")
    )
    assert mapped.parse_status == "accepted_normalized" and mapped.operation == "grouping"


@pytest.mark.asyncio
async def test_cli_audit_reads_a_historical_run_json_and_human(repo, capsys):
    from app.cli import commands
    from app.config import Settings
    from tests.api.helpers import build_test_components

    result, canonical, _merged = _historical_run()
    components = await build_test_components(Settings(_env_file=None))
    await components.repository.save_success(result)

    assert await commands.cmd_audit(components, run_id=result.id, as_json=True) == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    attempts = {a["id"]: a for a in body["claim_processing_attempts"] if a["id"].startswith("historical-")}
    assert {a["operation"] for a in attempts.values()} == {"grouping", "reconciliation"}
    assert attempts["historical-2"]["parse_status"] == "accepted_normalized"
    assert attempts["historical-3"]["raw_output_text"] == _GROUPING_V4_RAW
    assert attempts["historical-5"]["request_provenance"]["contract_version"] == (
        "cross_round_claim_reconciliation_v2"
    )
    canonical_public = next(c for c in body["claims"] if c["id"] == canonical.id)
    assert canonical_public["support_scope_model_count"] == 3

    assert await commands.cmd_audit(components, run_id=result.id, as_json=False) == commands.EXIT_OK
    human = capsys.readouterr().out
    assert f"claim_processing_attempts: {len(result.debate_result.claim_processing_attempts)}" in human
