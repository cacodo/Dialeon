"""
`validate_linguistic_realization_coherence` -- checagem DEFENSIVA de
coerência entre registros (FinalAnswer.linguistic_realization,
EditorResult.linguistic_realization_attempts/linguistic_semantic_review_attempts/
linguistic_realization_fallback_reason). Constrói estados "impossíveis" via
`model_copy` (nunca reexecuta os validators normais de `FinalAnswer`/
`EditorResult` -- mesma técnica de tests/storage/test_natural_answer_persistence.py)
pra exercitar só esta função isolada. Nenhuma chamada de provider real.
"""

from __future__ import annotations

import json

import pytest

from app.editor.linguistic_realization import (
    LINGUISTIC_REALIZATION_CONTRACT_VERSION,
    LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION,
    LinguisticRealizationProposal,
    build_linguistic_realization,
    candidate_digest,
    primary_answer_digest,
)
from app.editor.linguistic_realization_coherence import (
    LinguisticRealizationCoherenceError,
    validate_linguistic_realization_coherence,
)
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from tests.storage.fixtures import full_council_run_result
from tests.storage.test_primary_answer_persistence import _with_primary_answer

QUESTION = "Qual a capital do Brasil?"


def _accepted_attempt(style_attempt, *, contract_version, raw_output_text, attempt_id):
    return style_attempt.model_copy(
        update={
            "id": attempt_id,
            "attempt_number": 1,
            "raw_output_text": raw_output_text,
            "parse_status": "accepted",
            "parse_error_message": None,
            "request_provenance": RequestProvenance(
                contract_version=contract_version,
                request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
            ),
        }
    )


def _valid_state():
    """(final_answer, editor_result) com realização + revisão semântica
    ACEITAS e coerentes entre si -- a linha de base pra cada mutação
    negativa abaixo."""
    result, primary = _with_primary_answer(full_council_run_result())
    style_attempts = result.editor_result.attempts
    claim_id = primary.sections[0].items[0].claim_id

    proposal_payload = {"blocks": [{"claim_ids": [claim_id], "text": "Reescrita da claim."}]}
    proposal = LinguisticRealizationProposal.model_validate(proposal_payload)
    based_on = primary_answer_digest(primary)
    digest = candidate_digest(proposal, based_on_primary_answer_digest=based_on)
    realization = build_linguistic_realization(proposal, primary=primary)

    realization_attempt = _accepted_attempt(
        style_attempts[0],
        contract_version=LINGUISTIC_REALIZATION_CONTRACT_VERSION,
        raw_output_text=json.dumps(proposal_payload),
        attempt_id="realization-1",
    )
    review_payload = {"candidate_digest": digest, "decision": "accept", "issue_codes": []}
    review_attempt = _accepted_attempt(
        style_attempts[0],
        contract_version=LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION,
        raw_output_text=json.dumps(review_payload),
        attempt_id="review-1",
    )

    final_answer = result.editor_result.final_answer.model_copy(
        update={"linguistic_realization": realization}
    )
    editor = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "linguistic_realization_attempts": [realization_attempt],
            "linguistic_semantic_review_attempts": [review_attempt],
            "linguistic_semantic_review_provider": review_attempt.provider,
            "linguistic_realization_fallback_reason": None,
        }
    )
    return final_answer, editor, primary, digest, review_attempt


def _check(final_answer, editor):
    validate_linguistic_realization_coherence(final_answer, editor, question=QUESTION)


# ---------------------------------------------------------------------------


def test_valid_accepted_state_passes():
    final_answer, editor, *_ = _valid_state()
    _check(final_answer, editor)  # não levanta


def test_nothing_present_is_a_noop():
    result, primary = _with_primary_answer(full_council_run_result())
    _check(result.editor_result.final_answer, result.editor_result)  # não levanta


def test_attempts_without_a_primary_answer_fail_closed():
    final_answer, editor, *_ = _valid_state()
    forged_final = final_answer.model_copy(update={"primary_answer": None})
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(forged_final, editor)


def test_review_attempts_without_an_accepted_realization_attempt_fail_closed():
    final_answer, editor, *_ = _valid_state()
    forged_final = final_answer.model_copy(update={"linguistic_realization": None})
    forged_editor = editor.model_copy(
        update={
            "final_answer": forged_final,
            "linguistic_realization_attempts": [],
            "linguistic_realization_fallback_reason": "malformed_realization",
        }
    )
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(forged_final, forged_editor)


def test_accepted_realization_attempt_with_wrong_contract_version_fails_closed():
    final_answer, editor, *_ = _valid_state()
    bad_attempt = editor.linguistic_realization_attempts[0].model_copy(
        update={
            "request_provenance": RequestProvenance(
                contract_version="linguistic_realization_v99",
                request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
            )
        }
    )
    forged_editor = editor.model_copy(update={"linguistic_realization_attempts": [bad_attempt]})
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(final_answer, forged_editor)


def test_accepted_realization_attempt_with_unparseable_raw_output_fails_closed():
    final_answer, editor, *_ = _valid_state()
    bad_attempt = editor.linguistic_realization_attempts[0].model_copy(
        update={"raw_output_text": "isto não é json"}
    )
    forged_editor = editor.model_copy(update={"linguistic_realization_attempts": [bad_attempt]})
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(final_answer, forged_editor)


def test_accepted_realization_attempt_that_fails_structural_validation_fails_closed():
    final_answer, editor, primary, *_ = _valid_state()
    other_claim = primary.sections[0].items[0].claim_id + "-forjado"
    bad_payload = {"blocks": [{"claim_ids": [other_claim], "text": "x"}]}
    bad_attempt = editor.linguistic_realization_attempts[0].model_copy(
        update={"raw_output_text": json.dumps(bad_payload)}
    )
    forged_editor = editor.model_copy(update={"linguistic_realization_attempts": [bad_attempt]})
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(final_answer, forged_editor)


def test_review_candidate_digest_mismatch_fails_closed():
    final_answer, editor, *_ = _valid_state()
    tampered_review = editor.linguistic_semantic_review_attempts[0].model_copy(
        update={
            "raw_output_text": json.dumps(
                {"candidate_digest": "f" * 64, "decision": "accept", "issue_codes": []}
            )
        }
    )
    forged_editor = editor.model_copy(
        update={"linguistic_semantic_review_attempts": [tampered_review]}
    )
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(final_answer, forged_editor)


def test_persisted_realization_diverging_from_the_accepted_attempt_fails_closed():
    final_answer, editor, *_ = _valid_state()
    tampered = final_answer.linguistic_realization.model_copy(
        update={
            "rendered_text": final_answer.linguistic_realization.rendered_text + " Tudo comprovado.",
            "blocks": tuple(
                b.model_copy(update={"text": b.text + " Tudo comprovado."})
                for b in final_answer.linguistic_realization.blocks
            ),
        }
    )
    forged_final = final_answer.model_copy(update={"linguistic_realization": tampered})
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(forged_final, editor)


def test_persisted_realization_present_but_review_decision_is_reject_fails_closed():
    final_answer, editor, primary, digest, _ = _valid_state()
    rejecting_review = editor.linguistic_semantic_review_attempts[0].model_copy(
        update={
            "raw_output_text": json.dumps(
                {
                    "candidate_digest": digest,
                    "decision": "reject",
                    "issue_codes": ["semantic_omission"],
                }
            )
        }
    )
    forged_editor = editor.model_copy(
        update={"linguistic_semantic_review_attempts": [rejecting_review]}
    )
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(final_answer, forged_editor)


def test_persisted_realization_present_with_a_fallback_reason_fails_closed():
    final_answer, editor, *_ = _valid_state()
    forged_editor = editor.model_copy(
        update={"linguistic_realization_fallback_reason": "defensive_realization_persistence_failure"}
    )
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(final_answer, forged_editor)


def test_accepted_review_without_persisted_realization_requires_the_defensive_reason():
    final_answer, editor, *_ = _valid_state()
    forged_final = final_answer.model_copy(update={"linguistic_realization": None})
    forged_editor = editor.model_copy(
        update={
            "final_answer": forged_final,
            "linguistic_realization_fallback_reason": "malformed_realization",
        }
    )
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(forged_final, forged_editor)


def test_accepted_review_without_persisted_realization_passes_with_the_defensive_reason():
    final_answer, editor, *_ = _valid_state()
    forged_final = final_answer.model_copy(update={"linguistic_realization": None})
    forged_editor = editor.model_copy(
        update={
            "final_answer": forged_final,
            "linguistic_realization_fallback_reason": "defensive_realization_persistence_failure",
        }
    )
    _check(forged_final, forged_editor)  # não levanta


def test_semantic_review_rejection_reason_requires_a_matching_rejected_review():
    final_answer, editor, primary, digest, _ = _valid_state()
    forged_final = final_answer.model_copy(update={"linguistic_realization": None})
    forged_editor = editor.model_copy(
        update={
            "final_answer": forged_final,
            "linguistic_realization_fallback_reason": "semantic_review_rejection",
        }
    )
    # revisão continua marcada como aceita -- motivo declarado não bate.
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(forged_final, forged_editor)


def test_a_rejected_review_without_the_matching_fallback_reason_fails_closed():
    final_answer, editor, primary, digest, _ = _valid_state()
    rejecting_review = editor.linguistic_semantic_review_attempts[0].model_copy(
        update={
            "raw_output_text": json.dumps(
                {
                    "candidate_digest": digest,
                    "decision": "reject",
                    "issue_codes": ["semantic_omission"],
                }
            )
        }
    )
    forged_final = final_answer.model_copy(update={"linguistic_realization": None})
    forged_editor = editor.model_copy(
        update={
            "final_answer": forged_final,
            "linguistic_semantic_review_attempts": [rejecting_review],
            "linguistic_realization_fallback_reason": "defensive_realization_persistence_failure",
        }
    )
    with pytest.raises(LinguisticRealizationCoherenceError):
        _check(forged_final, forged_editor)


def test_a_rejected_review_with_the_matching_fallback_reason_passes():
    final_answer, editor, primary, digest, _ = _valid_state()
    rejecting_review = editor.linguistic_semantic_review_attempts[0].model_copy(
        update={
            "raw_output_text": json.dumps(
                {
                    "candidate_digest": digest,
                    "decision": "reject",
                    "issue_codes": ["semantic_omission"],
                }
            )
        }
    )
    forged_final = final_answer.model_copy(update={"linguistic_realization": None})
    forged_editor = editor.model_copy(
        update={
            "final_answer": forged_final,
            "linguistic_semantic_review_attempts": [rejecting_review],
            "linguistic_realization_fallback_reason": "semantic_review_rejection",
        }
    )
    _check(forged_final, forged_editor)  # não levanta


@pytest.mark.parametrize("matching", [False, True])
def test_declined_rejection_is_bound_to_the_realization_attempt_digest(matching):
    final_answer, editor, _, digest, _ = _valid_state()
    review = editor.linguistic_semantic_review_attempts[0].model_copy(
        update={
            "raw_output_text": json.dumps(
                {
                    "candidate_digest": digest if matching else "f" * 64,
                    "decision": "reject",
                    "issue_codes": ["semantic_omission"],
                }
            )
        }
    )
    declined = final_answer.model_copy(update={"linguistic_realization": None})
    editor = editor.model_copy(
        update={
            "final_answer": declined,
            "linguistic_semantic_review_attempts": [review],
            "linguistic_realization_fallback_reason": "semantic_review_rejection",
        }
    )
    if matching:
        _check(declined, editor)
    else:
        with pytest.raises(LinguisticRealizationCoherenceError, match="digest"):
            _check(declined, editor)
