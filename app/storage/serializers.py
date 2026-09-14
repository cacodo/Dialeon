"""
Conversão bidirecional entre objetos de domínio (Pydantic, imutáveis) e rows
SQLAlchemy -- Etapa 10.

Cada `*_to_row` é puro: recebe o objeto de domínio já existente + o(s) id(s)
de pai necessário(s), devolve a row correspondente, sem nenhuma lógica de
negócio (isso já aconteceu nas Etapas 2-9; aqui é só forma de armazenamento).
Cada `*_from_row` é o inverso exato -- reconstrução "semanticamente
equivalente" (não exige identidade Python, Decision Delta §10).

`usage`/`TokenUsage`: uma chamada que falhou antes de qualquer resposta tem
`usage=None` inteiro; uma chamada bem-sucedida ou parcialmente reportada
(ex.: Gemini sem `candidates_token_count`) pode ter `TokenUsage` com um dos
dois campos `None`. Para fins de armazenamento e de toda fórmula de
agregação existente (`sum_usage_and_cost`), as duas situações "usage=None"
e "TokenUsage(None, None)" são observacionalmente idênticas -- nenhuma
soma/flag do sistema as distingue. Por isso duas colunas nullable
(`input_tokens`/`output_tokens`) bastam: na reconstrução, `usage` só volta
a ser `None` quando os dois estão nulos.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.debate.numeric_verification import ArithmeticAssertion, DeterministicVerificationAttempt
from app.debate.processing_record import ClaimProcessingAttempt
from app.editor.attempt import EditorAttempt
from app.editor.result import FinalAnswer
from app.judge.attempt import JudgeAttempt
from app.models.domain import (
    Claim,
    ClaimAssessment,
    ClaimSupport,
    EvidenceRef,
    JudgeVerdict,
    ModelResponse,
)
from app.models.provider_models import (
    ModelIdentitySource,
    PricingProvenance,
    ProviderErrorInfo,
    TokenUsage,
)
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.models import (
    RejectedSourceEntry,
    SourceClaimAnalysisResult,
    ValidSourceRelation,
)
from app.storage.models import (
    ClaimAssessmentRow,
    ClaimMergeRow,
    ClaimProcessingAttemptRow,
    ClaimRow,
    ClaimSupportRow,
    DeterministicVerificationAttemptRow,
    EditorAttemptRow,
    FinalAnswerRow,
    JudgeAttemptRow,
    JudgeVerdictRow,
    ModelResponseRow,
    SourceAnalysisAttemptRow,
    SourceClaimAnalysisResultRow,
)


def dt_to_naive_utc(dt: datetime) -> datetime:
    """SQLite não tem tipo de data com timezone nativo -- sem essa
    normalização, o driver pode devolver um datetime *naive* na leitura,
    quebrando comparação/equivalência com o original (sempre UTC-aware
    no domínio, via `datetime.now(timezone.utc)`). Grava sempre naive
    (assumindo UTC), reconstitui sempre com `tzinfo=utc` explícito --
    round-trip correto independente de como o driver internamente lida
    com isso."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def dt_from_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _usage_to_columns(usage: TokenUsage | None) -> tuple[bool, int | None, int | None]:
    """Distingue "usage nunca existiu" (usage=None) de "usage existia mas
    está vazio" (TokenUsage(None, None) -- caso real: Gemini sem
    usage_metadata). Sem o `usage_present` explícito, os dois colapsam
    na mesma representação NULL/NULL no banco, perdendo a distinção
    histórica entre "não sabemos nada" e "sabemos que o objeto existiu
    mas os dois lados vieram vazios"."""
    if usage is None:
        return False, None, None
    return True, usage.input_tokens, usage.output_tokens


def _usage_from_columns(
    usage_present: bool, input_tokens: int | None, output_tokens: int | None
) -> TokenUsage | None:
    if not usage_present:
        return None
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _provenance_to_json(provenance: PricingProvenance | None) -> dict | None:
    return provenance.model_dump() if provenance is not None else None


def _provenance_from_json(data: dict | None) -> PricingProvenance | None:
    return PricingProvenance(**data) if data is not None else None


def _error_to_json(error: ProviderErrorInfo | None) -> dict | None:
    return error.model_dump() if error is not None else None


def _error_from_json(data: dict | None) -> ProviderErrorInfo | None:
    return ProviderErrorInfo(**data) if data is not None else None


def _model_identity_source_to_column(source: ModelIdentitySource | None) -> str | None:
    return source.value if source is not None else None


def _model_identity_source_from_column(value: str | None) -> ModelIdentitySource | None:
    return ModelIdentitySource(value) if value is not None else None


# ---------------------------------------------------------------------------
# ModelResponse
# ---------------------------------------------------------------------------


def model_response_to_row(
    mr: ModelResponse,
    *,
    council_run_id: str | None = None,
    quorum_failure_id: str | None = None,
    position: int = 0,
) -> ModelResponseRow:
    usage_present, input_tokens, output_tokens = _usage_to_columns(mr.usage)
    return ModelResponseRow(
        id=mr.id,
        council_run_id=council_run_id,
        quorum_failure_id=quorum_failure_id,
        round_number=mr.round_number,
        position=position,
        provider=mr.provider,
        requested_model=mr.requested_model,
        model=mr.model,
        model_identity_source=_model_identity_source_to_column(mr.model_identity_source),
        status=mr.status,
        response_text=mr.response_text,
        usage_present=usage_present,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=mr.cost_usd,
        pricing_provenance_json=_provenance_to_json(mr.pricing_provenance),
        latency_ms=mr.latency_ms,
        attempts=mr.attempts,
        error_type=mr.error.type.value if mr.error is not None else None,
        error_message=mr.error.message if mr.error is not None else None,
        error_retryable=mr.error.retryable if mr.error is not None else None,
        had_uncertain_prior_attempts=mr.had_uncertain_prior_attempts,
        provider_finish_reason=mr.provider_finish_reason,
        created_at=dt_to_naive_utc(mr.created_at),
    )


def model_response_from_row(row: ModelResponseRow) -> ModelResponse:
    error = None
    if row.error_type is not None:
        error = ProviderErrorInfo(
            type=row.error_type, message=row.error_message, retryable=row.error_retryable
        )
    return ModelResponse(
        id=row.id,
        provider=row.provider,
        requested_model=row.requested_model,
        model=row.model,
        model_identity_source=_model_identity_source_from_column(row.model_identity_source),
        round_number=row.round_number,
        status=row.status,
        response_text=row.response_text,
        usage=_usage_from_columns(row.usage_present, row.input_tokens, row.output_tokens),
        cost_usd=row.cost_usd,
        pricing_provenance=_provenance_from_json(row.pricing_provenance_json),
        latency_ms=row.latency_ms,
        attempts=row.attempts,
        error=error,
        had_uncertain_prior_attempts=row.had_uncertain_prior_attempts,
        provider_finish_reason=row.provider_finish_reason,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# Claim (+ merges, + supports)
# ---------------------------------------------------------------------------


def claim_to_row(claim: Claim, *, council_run_id: str, position: int = 0) -> ClaimRow:
    return ClaimRow(
        id=claim.id,
        council_run_id=council_run_id,
        position=position,
        text=claim.text,
        source_model_response_id=claim.source_model_response_id,
        round_introduced=claim.round_introduced,
        parent_claim_id=claim.parent_claim_id,
        superseded_by=claim.superseded_by,
        status=claim.status,
        total_models_in_round=claim.total_models_in_round,
        support_scope_model_count=claim.support_scope_model_count,
        confidence=claim.confidence,
        external_evidence_json=(
            claim.external_evidence.model_dump(mode="json")
            if claim.external_evidence is not None
            else None
        ),
        created_at=dt_to_naive_utc(claim.created_at),
    )


def claim_merge_rows(claim: Claim) -> list[ClaimMergeRow]:
    return [
        ClaimMergeRow(claim_id=claim.id, source_claim_id=source_id, position=position)
        for position, source_id in enumerate(claim.merged_from_claim_ids)
    ]


def claim_support_rows(claim: Claim) -> list[ClaimSupportRow]:
    return [
        ClaimSupportRow(
            claim_id=claim.id,
            model_response_id=support.model_response_id,
            provider=support.provider,
            model=support.model,
            model_identity_source=_model_identity_source_to_column(support.model_identity_source),
            position=position,
        )
        for position, support in enumerate(claim.supporting_model_response_ids)
    ]


def claim_support_from_row(row: ClaimSupportRow) -> ClaimSupport:
    return ClaimSupport(
        model_response_id=row.model_response_id,
        provider=row.provider,
        model=row.model,
        model_identity_source=_model_identity_source_from_column(row.model_identity_source),
    )


def claim_from_row(
    row: ClaimRow, *, merged_from_claim_ids: list[str], supports: list[ClaimSupport]
) -> Claim:
    return Claim(
        id=row.id,
        text=row.text,
        source_model_response_id=row.source_model_response_id,
        round_introduced=row.round_introduced,
        parent_claim_id=row.parent_claim_id,
        merged_from_claim_ids=merged_from_claim_ids,
        superseded_by=row.superseded_by,
        status=row.status,
        supporting_model_response_ids=supports,
        total_models_in_round=row.total_models_in_round,
        support_scope_model_count=row.support_scope_model_count,
        confidence=row.confidence,
        external_evidence=(
            None if row.external_evidence_json is None else EvidenceRef(**row.external_evidence_json)
        ),
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# ClaimProcessingAttempt
# ---------------------------------------------------------------------------


def claim_processing_attempt_to_row(
    attempt: ClaimProcessingAttempt, *, council_run_id: str, position: int = 0
) -> ClaimProcessingAttemptRow:
    usage_present, input_tokens, output_tokens = _usage_to_columns(attempt.usage)
    return ClaimProcessingAttemptRow(
        id=attempt.id,
        council_run_id=council_run_id,
        position=position,
        operation=attempt.operation,
        round_number=attempt.round_number,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=_model_identity_source_to_column(attempt.model_identity_source),
        target_model_response_id=attempt.target_model_response_id,
        target_claim_ids_json=list(attempt.target_claim_ids),
        transport_status=attempt.transport_status,
        transport_error_json=_error_to_json(attempt.transport_error),
        transport_attempts=attempt.transport_attempts,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage_present=usage_present,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=attempt.cost_usd,
        pricing_provenance_json=_provenance_to_json(attempt.pricing_provenance),
        latency_ms=attempt.latency_ms,
        created_at=dt_to_naive_utc(attempt.created_at),
    )


def claim_processing_attempt_from_row(row: ClaimProcessingAttemptRow) -> ClaimProcessingAttempt:
    return ClaimProcessingAttempt(
        id=row.id,
        operation=row.operation,
        round_number=row.round_number,
        attempt_number=row.attempt_number,
        provider=row.provider,
        requested_model=row.requested_model,
        model=row.model,
        model_identity_source=_model_identity_source_from_column(row.model_identity_source),
        target_model_response_id=row.target_model_response_id,
        target_claim_ids=list(row.target_claim_ids_json),
        transport_status=row.transport_status,
        transport_error=_error_from_json(row.transport_error_json),
        transport_attempts=row.transport_attempts,
        had_uncertain_prior_attempts=row.had_uncertain_prior_attempts,
        provider_finish_reason=row.provider_finish_reason,
        raw_output_text=row.raw_output_text,
        parse_status=row.parse_status,
        parse_error_message=row.parse_error_message,
        usage=_usage_from_columns(row.usage_present, row.input_tokens, row.output_tokens),
        cost_usd=row.cost_usd,
        pricing_provenance=_provenance_from_json(row.pricing_provenance_json),
        latency_ms=row.latency_ms,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# DeterministicVerificationAttempt (Etapa 15)
# ---------------------------------------------------------------------------


def deterministic_verification_attempt_to_row(
    attempt: DeterministicVerificationAttempt, *, council_run_id: str, position: int = 0
) -> DeterministicVerificationAttemptRow:
    assertion = attempt.assertion
    return DeterministicVerificationAttemptRow(
        id=attempt.id,
        council_run_id=council_run_id,
        claim_id=attempt.claim_id,
        position=position,
        state=attempt.state,
        raw_proposal_json=attempt.raw_proposal,
        assertion_left=assertion.left if assertion is not None else None,
        assertion_operator=assertion.operator if assertion is not None else None,
        assertion_right=assertion.right if assertion is not None else None,
        assertion_asserted_result=assertion.asserted_result if assertion is not None else None,
        computed_result=attempt.computed_result,
        created_at=dt_to_naive_utc(attempt.created_at),
    )


def deterministic_verification_attempt_from_row(
    row: DeterministicVerificationAttemptRow,
) -> DeterministicVerificationAttempt:
    assertion = None
    if row.assertion_left is not None:
        assertion = ArithmeticAssertion(
            left=row.assertion_left,
            operator=row.assertion_operator,
            right=row.assertion_right,
            asserted_result=row.assertion_asserted_result,
        )
    return DeterministicVerificationAttempt(
        id=row.id,
        claim_id=row.claim_id,
        state=row.state,
        raw_proposal=row.raw_proposal_json,
        assertion=assertion,
        computed_result=row.computed_result,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# JudgeVerdict (+ ClaimAssessment)
# ---------------------------------------------------------------------------


def judge_verdict_to_row(verdict: JudgeVerdict, *, council_run_id: str) -> JudgeVerdictRow:
    return JudgeVerdictRow(
        id=verdict.id,
        council_run_id=council_run_id,
        evaluated_through_round=verdict.evaluated_through_round,
        judge_model=verdict.judge_model,
        judge_model_identity_source=_model_identity_source_to_column(
            verdict.judge_model_identity_source
        ),
        best_arguments_by_json=dict(verdict.best_arguments_by),
        debate_limitations_json=list(verdict.debate_limitations),
        confidence=verdict.confidence,
        reasoning=verdict.reasoning,
        triggered_self_review=verdict.triggered_self_review,
        self_review_of=verdict.self_review_of,
        created_at=dt_to_naive_utc(verdict.created_at),
    )


def claim_assessment_rows(verdict: JudgeVerdict) -> list[ClaimAssessmentRow]:
    return [
        ClaimAssessmentRow(
            judge_verdict_id=verdict.id,
            claim_id=a.claim_id,
            verdict=a.verdict,
            explanation=a.explanation,
            position=position,
        )
        for position, a in enumerate(verdict.claim_assessments)
    ]


def judge_verdict_from_row(
    row: JudgeVerdictRow, *, claim_assessments: list[ClaimAssessment]
) -> JudgeVerdict:
    return JudgeVerdict(
        id=row.id,
        evaluated_through_round=row.evaluated_through_round,
        judge_model=row.judge_model,
        judge_model_identity_source=_model_identity_source_from_column(
            row.judge_model_identity_source
        ),
        claim_assessments=claim_assessments,
        best_arguments_by=dict(row.best_arguments_by_json),
        debate_limitations=list(row.debate_limitations_json),
        confidence=row.confidence,
        reasoning=row.reasoning,
        triggered_self_review=row.triggered_self_review,
        self_review_of=row.self_review_of,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# JudgeAttempt / EditorAttempt (mesma forma)
# ---------------------------------------------------------------------------


def judge_attempt_to_row(attempt: JudgeAttempt, *, council_run_id: str) -> JudgeAttemptRow:
    usage_present, input_tokens, output_tokens = _usage_to_columns(attempt.usage)
    return JudgeAttemptRow(
        id=attempt.id,
        council_run_id=council_run_id,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=_model_identity_source_to_column(attempt.model_identity_source),
        transport_status=attempt.transport_status,
        transport_error_json=_error_to_json(attempt.transport_error),
        transport_attempts=attempt.transport_attempts,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage_present=usage_present,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=attempt.cost_usd,
        pricing_provenance_json=_provenance_to_json(attempt.pricing_provenance),
        latency_ms=attempt.latency_ms,
        created_at=dt_to_naive_utc(attempt.created_at),
    )


def judge_attempt_from_row(row: JudgeAttemptRow) -> JudgeAttempt:
    return JudgeAttempt(
        id=row.id,
        attempt_number=row.attempt_number,
        provider=row.provider,
        requested_model=row.requested_model,
        model=row.model,
        model_identity_source=_model_identity_source_from_column(row.model_identity_source),
        transport_status=row.transport_status,
        transport_error=_error_from_json(row.transport_error_json),
        transport_attempts=row.transport_attempts,
        had_uncertain_prior_attempts=row.had_uncertain_prior_attempts,
        provider_finish_reason=row.provider_finish_reason,
        raw_output_text=row.raw_output_text,
        parse_status=row.parse_status,
        parse_error_message=row.parse_error_message,
        usage=_usage_from_columns(row.usage_present, row.input_tokens, row.output_tokens),
        cost_usd=row.cost_usd,
        pricing_provenance=_provenance_from_json(row.pricing_provenance_json),
        latency_ms=row.latency_ms,
        created_at=dt_from_naive_utc(row.created_at),
    )


def editor_attempt_to_row(attempt: EditorAttempt, *, council_run_id: str) -> EditorAttemptRow:
    usage_present, input_tokens, output_tokens = _usage_to_columns(attempt.usage)
    return EditorAttemptRow(
        id=attempt.id,
        council_run_id=council_run_id,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=_model_identity_source_to_column(attempt.model_identity_source),
        transport_status=attempt.transport_status,
        transport_error_json=_error_to_json(attempt.transport_error),
        transport_attempts=attempt.transport_attempts,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage_present=usage_present,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=attempt.cost_usd,
        pricing_provenance_json=_provenance_to_json(attempt.pricing_provenance),
        latency_ms=attempt.latency_ms,
        created_at=dt_to_naive_utc(attempt.created_at),
    )


def editor_attempt_from_row(row: EditorAttemptRow) -> EditorAttempt:
    return EditorAttempt(
        id=row.id,
        attempt_number=row.attempt_number,
        provider=row.provider,
        requested_model=row.requested_model,
        model=row.model,
        model_identity_source=_model_identity_source_from_column(row.model_identity_source),
        transport_status=row.transport_status,
        transport_error=_error_from_json(row.transport_error_json),
        transport_attempts=row.transport_attempts,
        had_uncertain_prior_attempts=row.had_uncertain_prior_attempts,
        provider_finish_reason=row.provider_finish_reason,
        raw_output_text=row.raw_output_text,
        parse_status=row.parse_status,
        parse_error_message=row.parse_error_message,
        usage=_usage_from_columns(row.usage_present, row.input_tokens, row.output_tokens),
        cost_usd=row.cost_usd,
        pricing_provenance=_provenance_from_json(row.pricing_provenance_json),
        latency_ms=row.latency_ms,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# FinalAnswer
# ---------------------------------------------------------------------------


def final_answer_to_row(fa: FinalAnswer, *, council_run_id: str) -> FinalAnswerRow:
    return FinalAnswerRow(
        id=fa.id,
        council_run_id=council_run_id,
        answer_text=fa.answer_text,
        limitations_json=list(fa.limitations),
        status=fa.status,
        editor_model=fa.editor_model,
        editor_model_identity_source=_model_identity_source_to_column(
            fa.editor_model_identity_source
        ),
        based_on_verdict_id=fa.based_on_verdict_id,
        judge_confidence=fa.judge_confidence,
        created_at=dt_to_naive_utc(fa.created_at),
    )


def final_answer_from_row(row: FinalAnswerRow) -> FinalAnswer:
    return FinalAnswer(
        id=row.id,
        answer_text=row.answer_text,
        limitations=list(row.limitations_json),
        status=row.status,
        editor_model=row.editor_model,
        editor_model_identity_source=_model_identity_source_from_column(
            row.editor_model_identity_source
        ),
        based_on_verdict_id=row.based_on_verdict_id,
        judge_confidence=row.judge_confidence,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# SourceAnalysisAttempt (Etapa 16)
# ---------------------------------------------------------------------------


def source_analysis_attempt_to_row(
    attempt: SourceAnalysisAttempt, *, council_run_id: str, position: int = 0
) -> SourceAnalysisAttemptRow:
    return SourceAnalysisAttemptRow(
        id=attempt.id,
        council_run_id=council_run_id,
        position=position,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=_model_identity_source_to_column(attempt.model_identity_source),
        transport_status=attempt.transport_status,
        transport_error_json=_error_to_json(attempt.transport_error),
        transport_attempts=attempt.transport_attempts,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage_present=attempt.usage is not None,
        input_tokens=attempt.usage.input_tokens if attempt.usage is not None else None,
        output_tokens=attempt.usage.output_tokens if attempt.usage is not None else None,
        cost_usd=attempt.cost_usd,
        pricing_provenance_json=_provenance_to_json(attempt.pricing_provenance),
        latency_ms=attempt.latency_ms,
        created_at=dt_to_naive_utc(attempt.created_at),
    )


def source_analysis_attempt_from_row(row: SourceAnalysisAttemptRow) -> SourceAnalysisAttempt:
    return SourceAnalysisAttempt(
        id=row.id,
        attempt_number=row.attempt_number,
        provider=row.provider,
        requested_model=row.requested_model,
        model=row.model,
        model_identity_source=_model_identity_source_from_column(row.model_identity_source),
        transport_status=row.transport_status,
        transport_error=_error_from_json(row.transport_error_json),
        transport_attempts=row.transport_attempts,
        had_uncertain_prior_attempts=row.had_uncertain_prior_attempts,
        provider_finish_reason=row.provider_finish_reason,
        raw_output_text=row.raw_output_text,
        parse_status=row.parse_status,
        parse_error_message=row.parse_error_message,
        usage=_usage_from_columns(row.usage_present, row.input_tokens, row.output_tokens),
        cost_usd=row.cost_usd,
        pricing_provenance=_provenance_from_json(row.pricing_provenance_json),
        latency_ms=row.latency_ms,
        created_at=dt_from_naive_utc(row.created_at),
    )


# ---------------------------------------------------------------------------
# SourceClaimAnalysisResult (Etapa 16) -- união discriminada numa tabela só
# ---------------------------------------------------------------------------


def source_claim_analysis_result_to_row(
    result: SourceClaimAnalysisResult,
    *,
    council_run_id: str,
    source_analysis_attempt_id: str,
    position: int = 0,
) -> SourceClaimAnalysisResultRow:
    if isinstance(result, ValidSourceRelation):
        return SourceClaimAnalysisResultRow(
            id=result.id,
            council_run_id=council_run_id,
            source_analysis_attempt_id=source_analysis_attempt_id,
            position=position,
            claim_id=result.claim_id,
            kind="relation",
            relation=result.relation,
            excerpt=result.excerpt,
            excerpt_start=result.excerpt_start,
            excerpt_end=result.excerpt_end,
            reason=None,
            raw_entry_json=None,
            created_at=dt_to_naive_utc(result.created_at),
        )
    return SourceClaimAnalysisResultRow(
        id=result.id,
        council_run_id=council_run_id,
        source_analysis_attempt_id=source_analysis_attempt_id,
        position=position,
        claim_id=result.claim_id,
        kind="rejected",
        relation=None,
        excerpt=None,
        excerpt_start=None,
        excerpt_end=None,
        reason=result.reason,
        raw_entry_json=result.raw_entry,
        created_at=dt_to_naive_utc(result.created_at),
    )


def source_claim_analysis_result_from_row(
    row: SourceClaimAnalysisResultRow,
) -> SourceClaimAnalysisResult:
    if row.kind == "relation":
        return ValidSourceRelation(
            id=row.id,
            claim_id=row.claim_id,
            relation=row.relation,
            excerpt=row.excerpt,
            excerpt_start=row.excerpt_start,
            excerpt_end=row.excerpt_end,
            created_at=dt_from_naive_utc(row.created_at),
        )
    return RejectedSourceEntry(
        id=row.id,
        claim_id=row.claim_id,
        reason=row.reason,
        raw_entry=row.raw_entry_json,
        created_at=dt_from_naive_utc(row.created_at),
    )
