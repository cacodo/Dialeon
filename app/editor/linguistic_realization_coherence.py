"""Fail-closed cross-record coherence for LinguisticRealization.

This module separates two questions:

A. Is an ACCEPTED/PERSISTED LinguisticRealization
   (`final_answer.linguistic_realization is not None`) coherent with the
   exact attempt that produced it? This is the strongest guarantee --
   full reconstruction from the accepted attempt's `raw_output_text`
   against the CURRENT `PrimaryAnswer`, digest binding between
   realization and semantic review, etc. -- and it still fails closed on
   any mismatch, exactly as before this repair.

B. Is a set of realization/semantic-review ATTEMPTS, from an optional
   layer that was NOT accepted/persisted (declined, rejected, budget-
   closed, failed to interpret, failed to persist), still a TRUTHFUL
   audit record? A completed call remains historical evidence even if
   presentation later fails. A semantic rejection used as the fallback
   must nevertheless identify the exact candidate in the accepted
   realization attempt. Later persistence failures may retain attempts
   whose current interpretation cannot be established.
"""

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

# Motivos bounded sob os quais uma revisão semântica ACEITA (decision=
# "accept") pode legitimamente coexistir com nenhuma LinguisticRealization
# persistida/preferida -- nunca um provider/model configuration novo, só o
# vocabulário fechado já existente de `linguistic_realization_fallback_reason`.
_DECLINED_DESPITE_ACCEPTED_REVIEW_REASONS = (
    "defensive_realization_persistence_failure",
    "realization_persistence_preflight_failed",
)


class LinguisticRealizationCoherenceError(ValueError):
    pass


def _fail(message: str) -> None:
    raise LinguisticRealizationCoherenceError(message)


def _safe_review(attempt):
    """Read an accepted review without discarding a completed call if its
    interpretation no longer succeeds. Such a review cannot justify a
    semantic rejection fallback."""
    try:
        return parse_semantic_review(attempt.raw_output_text)
    except Exception:
        return None


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

    if realization is None:
        # B. Audit validity of declined attempts. Reconstruct a candidate
        # only when a rejection decision is used to justify this fallback.
        if reason is None:
            _fail("tentativas de linguistic realization sem resultado exigem motivo")

        accepted_reviews = [a for a in review_attempts if a.parse_status == "accepted"]
        review = _safe_review(accepted_reviews[-1]) if accepted_reviews else None
        review_decision = review.decision if review is not None else None
        if reason == "semantic_review_provider_unavailable" and review_attempts:
            _fail("provider de revisão indisponível não pode ter tentativa de revisão")
        if review_decision == "accept" and reason not in (
            _DECLINED_DESPITE_ACCEPTED_REVIEW_REASONS
        ):
            _fail(
                "semantic review aceita sem realização persistida exige motivo bounded explícito"
            )
        if reason == "semantic_review_rejection":
            if review_decision != "reject":
                _fail("semantic_review_rejection exige revisão válida com decision='reject'")
            attempt = accepted_realization[-1]
            if (
                attempt.request_provenance is None
                or attempt.request_provenance.contract_version
                != LINGUISTIC_REALIZATION_CONTRACT_VERSION
                or accepted_reviews[-1].request_provenance is None
                or accepted_reviews[-1].request_provenance.contract_version
                != LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION
            ):
                _fail("rejeição semântica exige contratos v1 das tentativas aceitas")
            try:
                proposal = parse_realization_proposal(attempt.raw_output_text)
                validate_realization_proposal(proposal, primary=primary, question=question)
                digest = candidate_digest(
                    proposal, based_on_primary_answer_digest=primary_answer_digest(primary)
                )
            except Exception as exc:
                raise LinguisticRealizationCoherenceError(
                    "rejeição semântica não reconstrói o candidato da tentativa aceita"
                ) from exc
            if review.candidate_digest != digest:
                _fail("semantic review rejeitada diverge do digest exato do candidato")
        elif review_decision == "reject":
            _fail("revisão rejeitada exige fallback semantic_review_rejection")
        return

    # A. Coherence of an ACCEPTED/PERSISTED LinguisticRealization -- full
    # reconstruction against the CURRENT PrimaryAnswer, exactly as before
    # this repair: um estado que AFIRMA aceitação precisa provar essa
    # aceitação byte a byte, nunca relaxado.
    if not accepted_realization:
        _fail("LinguisticRealization aceita exige tentativa de realização aceita")
    attempt = accepted_realization[-1]
    if (
        attempt.request_provenance is None
        or attempt.request_provenance.contract_version != LINGUISTIC_REALIZATION_CONTRACT_VERSION
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
    if not accepted_reviews:
        _fail("LinguisticRealization aceita exige revisão semântica aceita")
    review_attempt = accepted_reviews[-1]
    if (
        review_attempt.request_provenance is None
        or review_attempt.request_provenance.contract_version
        != LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION
    ):
        _fail("tentativa aceita de revisão perdeu o contrato v1")
    try:
        review = parse_semantic_review(review_attempt.raw_output_text)
    except Exception as exc:
        raise LinguisticRealizationCoherenceError(
            "tentativa aceita de revisão não reconstrói o resultado"
        ) from exc
    if review.candidate_digest != digest:
        _fail("semantic review não está ligada ao digest exato do candidato")

    if realization != expected_realization:
        _fail("LinguisticRealization persistida diverge da tentativa aceita")
    if realization.based_on_primary_answer_digest != primary_answer_digest(primary):
        _fail("LinguisticRealization diverge do digest da PrimaryAnswer")
    if review.decision != "accept":
        _fail("LinguisticRealization exige semantic review accept do candidato exato")
    if reason is not None:
        _fail("LinguisticRealization aceita não pode ter fallback reason")
