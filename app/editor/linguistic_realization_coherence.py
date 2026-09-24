"""Fail-closed cross-record coherence for LinguisticRealization."""

from __future__ import annotations

from app.editor.linguistic_realization import (
    LINGUISTIC_REALIZATION_CONTRACT_VERSION,
    LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION,
    build_linguistic_realization,
    candidate_digest,
    parse_realization_proposal,
    parse_semantic_review,
    primary_answer_digest,
    validate_realization_proposal,
)
from app.editor.result import EditorResult, FinalAnswer


class LinguisticRealizationCoherenceError(ValueError):
    pass


def _fail(message: str) -> None:
    raise LinguisticRealizationCoherenceError(message)


def validate_linguistic_realization_coherence(
    final_answer: FinalAnswer,
    editor_result: EditorResult,
    *,
    question: str,
) -> None:
    primary = final_answer.primary_answer
    realization = final_answer.linguistic_realization
    realization_attempts = editor_result.linguistic_realization_attempts
    review_attempts = editor_result.linguistic_semantic_review_attempts
    reason = editor_result.linguistic_realization_fallback_reason

    if not realization_attempts and not review_attempts and realization is None:
        return
    if primary is None:
        _fail("linguistic realization/attempts exigem PrimaryAnswer autoritativa")

    accepted_realization = [a for a in realization_attempts if a.parse_status == "accepted"]
    if review_attempts and not accepted_realization:
        _fail("semantic review sem candidato estruturalmente aceito")

    proposal = None
    digest = None
    expected_realization = None
    if accepted_realization:
        attempt = accepted_realization[-1]
        if (
            attempt.request_provenance is None
            or attempt.request_provenance.contract_version
            != LINGUISTIC_REALIZATION_CONTRACT_VERSION
        ):
            _fail("tentativa aceita de realização perdeu o contrato v1")
        try:
            proposal = parse_realization_proposal(attempt.raw_output_text)
            validate_realization_proposal(proposal, primary=primary, question=question)
            digest = candidate_digest(
                proposal, based_on_primary_answer_digest=primary_answer_digest(primary)
            )
            expected_realization = build_linguistic_realization(proposal, primary=primary)
        except Exception as exc:
            raise LinguisticRealizationCoherenceError(
                "tentativa aceita de realização não reconstrói o candidato"
            ) from exc

    accepted_reviews = [a for a in review_attempts if a.parse_status == "accepted"]
    review = None
    if accepted_reviews:
        attempt = accepted_reviews[-1]
        if (
            attempt.request_provenance is None
            or attempt.request_provenance.contract_version
            != LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION
        ):
            _fail("tentativa aceita de revisão perdeu o contrato v1")
        try:
            review = parse_semantic_review(attempt.raw_output_text)
        except Exception as exc:
            raise LinguisticRealizationCoherenceError(
                "tentativa aceita de revisão não reconstrói o resultado"
            ) from exc
        if digest is None or review.candidate_digest != digest:
            _fail("semantic review não está ligada ao digest exato do candidato")

    if realization is not None:
        if expected_realization is None or realization != expected_realization:
            _fail("LinguisticRealization persistida diverge da tentativa aceita")
        if realization.based_on_primary_answer_digest != primary_answer_digest(primary):
            _fail("LinguisticRealization diverge do digest da PrimaryAnswer")
        if review is None or review.decision != "accept":
            _fail("LinguisticRealization exige semantic review accept do candidato exato")
        if reason is not None:
            _fail("LinguisticRealization aceita não pode ter fallback reason")
    elif review is not None and review.decision == "accept" and reason != (
        "defensive_realization_persistence_failure"
    ):
        _fail("semantic review aceita sem realização persistida exige falha defensiva explícita")

    if reason == "semantic_review_rejection":
        if review is None or review.decision != "reject":
            _fail("semantic_review_rejection exige revisão válida com decision='reject'")
    elif review is not None and review.decision == "reject":
        _fail("revisão rejeitada exige fallback semantic_review_rejection")
