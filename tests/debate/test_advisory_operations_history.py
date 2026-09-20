"""
Histórico do agrupamento/reconciliação removidos da execução corrente
(vocabulário de auditoria + reconstrução de cardinalidade das Run02).

O round-trip de persistência, o mapper público e o `dialeon audit` de runs
antigas: tests/storage/test_historical_advisory_operations.py. Aqui:

1. `accepted_normalized` (claim_grouping_v3) continua um status legível, só de
   `grouping` e sem erro -- nenhuma operação corrente o emite.
2. Evidência local (banco somente-leitura; `skipif` se ausente): nas 3 runs
   Run02 auditadas, o conjunto de claims atuais sob a política corrente
   (extraídas autoritativas; só `revises_claim_id` explícito da extração
   aposenta) tem 48 / 50 / 50 claims -- reconstruído SÓ da evidência de
   extração persistida.
"""

from __future__ import annotations

import json
import sqlite3
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.debate.processing_record import ClaimProcessingAttempt
from app.editor.attempt import EditorAttempt
from app.judge.attempt import JudgeAttempt
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType
from app.source_analysis.attempt import SourceAnalysisAttempt

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "llm_council.db"

# run_id -> quantidade de claims atuais sob a política corrente (Run02)
RUN02_CURRENT_CLAIM_COUNTS = {
    "0d1740c4-a392-4e5d-bed9-df9c5b3cb4e0": 48,
    "c04dafbc-b861-49c1-bd4d-33396efbfe4f": 50,
    "2bd3b8b4-7916-4563-b3d1-38362cfbe69d": 50,
}


def _attempt_kwargs(**overrides):
    fields = dict(
        operation="grouping",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        requested_model="m",
        model="m",
        target_claim_ids=["c1", "c2"],
        transport_status="success",
        transport_attempts=1,
        raw_output_text='{"groups": [{"member_claim_ids": ["c1"], "canonical_text": ""}]}',
        parse_status="accepted_normalized",
        latency_ms=1,
    )
    fields.update(overrides)
    return fields


def test_historical_v3_accepted_normalized_grouping_attempt_remains_readable():
    historical = ClaimProcessingAttempt(**_attempt_kwargs())

    assert historical.parse_status == "accepted_normalized"
    assert historical.operation == "grouping"
    assert historical.raw_output_text.startswith('{"groups"')  # resposta original v3 intacta


def test_accepted_normalized_stays_grouping_only_and_error_free():
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_attempt_kwargs(operation="reconciliation"))
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(
            **_attempt_kwargs(operation="extraction", target_claim_ids=[], target_model_response_id="mr-1")
        )
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_attempt_kwargs(parse_error_message="erro"))
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(
            **_attempt_kwargs(
                transport_status="error",
                raw_output_text=None,
                transport_error=ProviderErrorInfo(type=ProviderErrorType.TIMEOUT, message="t", retryable=True),
            )
        )


def test_accepted_normalized_does_not_exist_for_judge_editor_or_source_analysis():
    for model in (JudgeAttempt, EditorAttempt, SourceAnalysisAttempt):
        assert "accepted_normalized" not in set(typing.get_args(model.model_fields["parse_status"].annotation))


@pytest.mark.skipif(not DB.exists(), reason="evidência local: banco com as runs Run02 não existe neste checkout")
@pytest.mark.parametrize("run_id, expected", sorted(RUN02_CURRENT_CLAIM_COUNTS.items()))
def test_local_evidence_run02_current_claim_cardinality_is_extraction_minus_explicit_revisions(run_id, expected):
    """Reconstrução SÓ da evidência de extração persistida (banco aberto
    somente-leitura, nenhum provider): claims extraídas aceitas menos as
    aposentadas por `revises_claim_id` explícito = claims atuais. Bate com o
    que agrupamento v4 + reconciliação v2 (não-destrutivos) já entregavam:
    nenhuma deduplicação/representante/união/teto escondido."""
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        if connection.execute("select 1 from council_runs where id=?", (run_id,)).fetchone() is None:
            pytest.skip(f"evidência local: run {run_id[:8]} ausente do banco deste checkout")
        outputs = connection.execute(
            "select raw_output_text from claim_processing_attempts "
            "where council_run_id=? and operation='extraction' and parse_status='accepted'",
            (run_id,),
        ).fetchall()
        raw_rows = connection.execute(
            "select id, parent_claim_id from claims where council_run_id=? "
            "and source_model_response_id is not null",
            (run_id,),
        ).fetchall()
    finally:
        connection.close()

    extracted = 0
    revised_ids: set[str] = set()
    for (raw_text,) in outputs:
        body = raw_text.strip().removeprefix("```json").removesuffix("```").strip()
        for claim in json.loads(body)["claims"]:
            extracted += 1
            if claim.get("revises_claim_id"):
                revised_ids.add(claim["revises_claim_id"])

    assert extracted - len(revised_ids) == expected
    # o mesmo número pela tabela de claims persistida (extraídas não aposentadas por parent_claim_id)
    parents = {parent for _id, parent in raw_rows if parent}
    assert len(raw_rows) == extracted
    assert len([cid for cid, _p in raw_rows if cid not in parents]) == expected
