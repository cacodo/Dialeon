"""
Testes de validação a nível de domínio (Pydantic) de
`ClaimReconciliationOutcome`/`SourceJudgeReconciliationResult` --
independentes de `reconcile_source_and_judge`, provam que o próprio
schema recusa estado ambíguo mesmo se construído diretamente (defesa em
profundidade -- ver seção 20 do contrato desta slice)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.reconciliation.models import (
    CONTRACT_VERSION,
    ChannelRelationship,
    ClaimReconciliationOutcome,
    SourceChannelState,
    SourceJudgeReconciliationResult,
)


def _outcome(**overrides) -> ClaimReconciliationOutcome:
    fields = dict(
        claim_id="c1",
        judge_verdict_id="verdict-1",
        source_claim_result_ids=(),
        source_state=SourceChannelState.NOT_SUPPLIED,
        channel_relationship=ChannelRelationship.NOT_COMPARABLE,
    )
    fields.update(overrides)
    return ClaimReconciliationOutcome(**fields)


def test_contract_version_is_the_closed_literal():
    result = SourceJudgeReconciliationResult(status="judge_unavailable", claim_outcomes=[])
    assert result.contract_version == CONTRACT_VERSION == "source_judge_reconciliation_v1"


def test_outcome_rejects_duplicate_source_result_ids():
    with pytest.raises(ValidationError):
        _outcome(
            source_state=SourceChannelState.SUPPORTS,
            channel_relationship=ChannelRelationship.DIRECTIONALLY_ALIGNED,
            source_claim_result_ids=("r1", "r1"),
        )


def test_outcome_rejects_channel_relationship_other_than_not_comparable_without_verdict():
    """judge_verdict_id=None mas channel_relationship != not_comparable
    -- estruturalmente impossível, mesmo se alguém tentar construir isso
    à mão."""
    with pytest.raises(ValidationError):
        _outcome(
            judge_verdict_id=None,
            source_state=SourceChannelState.SUPPORTS,
            channel_relationship=ChannelRelationship.DIRECTIONALLY_ALIGNED,
        )


def test_outcome_allows_not_comparable_without_verdict():
    outcome = _outcome(
        judge_verdict_id=None,
        source_claim_result_ids=("r1",),
        source_state=SourceChannelState.SUPPORTS,
        channel_relationship=ChannelRelationship.NOT_COMPARABLE,
    )
    assert outcome.judge_verdict_id is None


@pytest.mark.parametrize(
    "source_state,invalid_relationship",
    [
        (SourceChannelState.UNRESOLVED, ChannelRelationship.DIRECTIONALLY_ALIGNED),
        (SourceChannelState.MIXED, ChannelRelationship.IN_TENSION),
        (SourceChannelState.NOT_SUPPLIED, ChannelRelationship.SOURCE_UNRESOLVED),
        (SourceChannelState.ENTRY_REJECTED, ChannelRelationship.SOURCE_CHANNEL_CONFLICT),
        (SourceChannelState.SUPPORTS, ChannelRelationship.SOURCE_UNRESOLVED),
        (SourceChannelState.CONTRADICTS, ChannelRelationship.SOURCE_CHANNEL_CONFLICT),
    ],
)
def test_outcome_rejects_invalid_source_state_relationship_combination(
    source_state, invalid_relationship
):
    with pytest.raises(ValidationError):
        _outcome(
            judge_verdict_id="verdict-1",
            source_state=source_state,
            channel_relationship=invalid_relationship,
        )


def test_result_rejects_duplicate_claim_outcomes():
    with pytest.raises(ValidationError):
        SourceJudgeReconciliationResult(
            status="judge_unavailable",
            claim_outcomes=[
                _outcome(claim_id="c1", judge_verdict_id=None),
                _outcome(claim_id="c1", judge_verdict_id=None),
            ],
        )


def test_result_rejects_judge_unavailable_with_a_concrete_verdict_id():
    with pytest.raises(ValidationError):
        SourceJudgeReconciliationResult(
            status="judge_unavailable",
            claim_outcomes=[_outcome(claim_id="c1", judge_verdict_id="verdict-1")],
        )


def test_result_rejects_complete_status_with_missing_verdict_id():
    with pytest.raises(ValidationError):
        SourceJudgeReconciliationResult(
            status="complete",
            claim_outcomes=[_outcome(claim_id="c1", judge_verdict_id=None)],
        )


def test_result_rejects_complete_status_with_two_different_verdict_ids():
    """Só existe UM JudgeVerdict por execução -- dois judge_verdict_id
    diferentes entre outcomes é estruturalmente impossível."""
    with pytest.raises(ValidationError):
        SourceJudgeReconciliationResult(
            status="complete",
            claim_outcomes=[
                _outcome(claim_id="c1", judge_verdict_id="verdict-1"),
                _outcome(claim_id="c2", judge_verdict_id="verdict-2"),
            ],
        )


def test_result_accepts_complete_status_with_consistent_verdict_id():
    result = SourceJudgeReconciliationResult(
        status="complete",
        claim_outcomes=[
            _outcome(
                claim_id="c1",
                judge_verdict_id="verdict-1",
                source_claim_result_ids=("r1",),
                source_state=SourceChannelState.SUPPORTS,
                channel_relationship=ChannelRelationship.DIRECTIONALLY_ALIGNED,
            ),
            _outcome(claim_id="c2", judge_verdict_id="verdict-1"),
        ],
    )
    assert len(result.claim_outcomes) == 2


# ---------------------------------------------------------------------------
# Repair #2 (revisão adversarial) -- invariantes canônicos reforçados:
# strings vazias NUNCA são normalizadas como ausência; cardinalidade de
# source_claim_result_ids precisa concordar estruturalmente com
# source_state; toda claim_id/reconciliation id precisa ser não-vazio.
# ---------------------------------------------------------------------------


def test_outcome_rejects_empty_judge_verdict_id():
    with pytest.raises(ValidationError):
        _outcome(judge_verdict_id="", source_state=SourceChannelState.NOT_SUPPLIED)


def test_outcome_rejects_empty_source_claim_result_id():
    with pytest.raises(ValidationError):
        _outcome(
            source_claim_result_ids=("",),
            source_state=SourceChannelState.SUPPORTS,
            channel_relationship=ChannelRelationship.DIRECTIONALLY_ALIGNED,
        )


def test_outcome_rejects_empty_claim_id():
    with pytest.raises(ValidationError):
        _outcome(claim_id="")


def test_result_rejects_empty_reconciliation_id():
    with pytest.raises(ValidationError):
        SourceJudgeReconciliationResult(
            id="", status="judge_unavailable", claim_outcomes=[]
        )


@pytest.mark.parametrize(
    "source_state", [SourceChannelState.NOT_SUPPLIED, SourceChannelState.ANALYSIS_UNAVAILABLE]
)
def test_outcome_rejects_ids_present_for_unsuppliable_states(source_state):
    """not_supplied/analysis_unavailable exigem source_claim_result_ids
    vazio -- não há canal de fonte utilizável pra referenciar nenhum id."""
    with pytest.raises(ValidationError):
        _outcome(
            source_state=source_state,
            source_claim_result_ids=("r1",),
            channel_relationship=ChannelRelationship.NOT_COMPARABLE,
        )


@pytest.mark.parametrize(
    "source_state,relationship",
    [
        (SourceChannelState.ENTRY_REJECTED, ChannelRelationship.NOT_COMPARABLE),
        (SourceChannelState.SUPPORTS, ChannelRelationship.DIRECTIONALLY_ALIGNED),
        (SourceChannelState.CONTRADICTS, ChannelRelationship.DIRECTIONALLY_ALIGNED),
        (SourceChannelState.UNRESOLVED, ChannelRelationship.SOURCE_UNRESOLVED),
    ],
)
def test_outcome_rejects_zero_ids_for_states_requiring_at_least_one(source_state, relationship):
    with pytest.raises(ValidationError):
        _outcome(
            source_state=source_state,
            source_claim_result_ids=(),
            channel_relationship=relationship,
        )


def test_outcome_rejects_mixed_with_only_one_source_id():
    """mixed exige >= 2 ids distintos -- por definição só existe quando
    há mais de um resultado canônico estruturalmente presente."""
    with pytest.raises(ValidationError):
        _outcome(
            source_state=SourceChannelState.MIXED,
            source_claim_result_ids=("r1",),
            channel_relationship=ChannelRelationship.SOURCE_CHANNEL_CONFLICT,
        )


def test_outcome_accepts_mixed_with_two_distinct_source_ids():
    outcome = _outcome(
        source_state=SourceChannelState.MIXED,
        source_claim_result_ids=("r1", "r2"),
        channel_relationship=ChannelRelationship.SOURCE_CHANNEL_CONFLICT,
    )
    assert len(outcome.source_claim_result_ids) == 2


def test_outcome_accepts_not_supplied_with_zero_ids():
    outcome = _outcome(
        source_state=SourceChannelState.NOT_SUPPLIED,
        source_claim_result_ids=(),
        channel_relationship=ChannelRelationship.NOT_COMPARABLE,
    )
    assert outcome.source_claim_result_ids == ()
