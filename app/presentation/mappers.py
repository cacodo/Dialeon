"""
Mapeamento domínio -> contrato público -- Etapa 11 (movido pra
`app/presentation/` na Etapa 14, patch de revisão -- mesmo motivo de
`schemas.py`: consumido por `app/api/` e `app/cli/`, nenhum dos dois
importa o outro).

Só tradução de forma. Nenhum cálculo de accounting, nenhuma decisão de
pipeline, nenhuma consulta a PricingRegistry -- tudo aqui já vem pronto
dos objetos de domínio/storage (principio 1/7 da Decision Delta).
"""

from __future__ import annotations

from app.presentation.schemas import (
    AccountingSummary,
    ArithmeticAssertionPublic,
    ClaimAssessmentPublic,
    ClaimProcessingAttemptPublic,
    ClaimPublic,
    ClaimSupportPublic,
    CompletedRunAudit,
    CompletedRunResponse,
    DebateOutcome,
    DeterministicVerificationAttemptPublic,
    EditorAttemptPublic,
    EditorOutcome,
    FailedRunResponse,
    FinalAnswerPublic,
    InitialRoundAudit,
    JudgeAttemptPublic,
    JudgeOutcome,
    JudgeVerdictPublic,
    ModelResponsePublic,
    QuorumFailureAudit,
    QuorumFailureRunResponse,
    QuorumPublic,
    RejectedSourceEntryPublic,
    RoundAccountingPublic,
    RoundAudit,
    RunConfigPublic,
    RunningRunResponse,
    RunSummaryResponse,
    SourceAnalysisAttemptPublic,
    SourceAnalysisOutcome,
    SourceClaimAnalysisResultPublic,
    ValidSourceRelationPublic,
)
from app.council.result import CouncilRunResult
from app.debate.numeric_verification import DeterministicVerificationAttempt
from app.models.provider_models import ProviderExecutionPolicy
from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.result import CritiqueResult
from app.editor.attempt import EditorAttempt
from app.editor.result import FinalAnswer
from app.judge.attempt import JudgeAttempt
from app.models.domain import Claim, JudgeVerdict, ModelResponse
from app.orchestrator.config import RunConfig
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.models import (
    RejectedSourceEntry,
    SourceClaimAnalysisResult,
    ValidSourceRelation,
)
from app.source_analysis.result import SourceAnalysisResult
from app.storage.records import AcceptedRunRecord, QuorumFailureRecord, RunSummary


def model_response_public(mr: ModelResponse) -> ModelResponsePublic:
    return ModelResponsePublic(
        id=mr.id,
        provider=mr.provider,
        requested_model=mr.requested_model,
        model=mr.model,
        model_identity_source=mr.model_identity_source,
        round_number=mr.round_number,
        status=mr.status,
        response_text=mr.response_text,
        usage=mr.usage,
        cost_usd=mr.cost_usd,
        pricing_provenance=mr.pricing_provenance,
        latency_ms=mr.latency_ms,
        attempts=mr.attempts,
        error=mr.error,
        had_uncertain_prior_attempts=mr.had_uncertain_prior_attempts,
        provider_finish_reason=mr.provider_finish_reason,
        created_at=mr.created_at,
    )


def _round_accounting(source) -> RoundAccountingPublic:
    return RoundAccountingPublic(
        total_input_tokens=source.total_input_tokens,
        total_output_tokens=source.total_output_tokens,
        estimated_cost_usd=source.total_cost_usd,
        has_unknown_accounting_components=source.has_unknown_accounting_components,
    )


def initial_round_audit(initial: InitialResponsesResult) -> InitialRoundAudit:
    return InitialRoundAudit(
        responses=[model_response_public(r) for r in initial.responses],
        successful_count=initial.successful_count,
        total_providers=initial.total_providers,
        insufficient_data_for_consensus=initial.insufficient_data_for_consensus,
        budget_exceeded=initial.budget_exceeded,
        accounting=_round_accounting(initial),
    )


def round_audit(rr: RoundResult) -> RoundAudit:
    """Usado tanto pra rodada de crítica de um run completo quanto pra
    round_result de uma falha de quórum -- ambos são RoundResult puro,
    sem os campos exclusivos de InitialResponsesResult."""
    return RoundAudit(
        responses=[model_response_public(r) for r in rr.responses],
        successful_count=rr.successful_count,
        total_participants=rr.total_participants,
        accounting=_round_accounting(rr),
    )


def critique_round_audit(critique: CritiqueResult | None) -> RoundAudit | None:
    return None if critique is None else round_audit(critique.round_result)


def claim_public(claim: Claim) -> ClaimPublic:
    return ClaimPublic(
        id=claim.id,
        text=claim.text,
        source_model_response_id=claim.source_model_response_id,
        round_introduced=claim.round_introduced,
        parent_claim_id=claim.parent_claim_id,
        merged_from_claim_ids=list(claim.merged_from_claim_ids),
        status=claim.status,
        supporting_model_response_ids=[
            ClaimSupportPublic(
                model_response_id=s.model_response_id,
                provider=s.provider,
                model=s.model,
                model_identity_source=s.model_identity_source,
            )
            for s in claim.supporting_model_response_ids
        ],
        supporting_models=list(claim.supporting_models),
        total_models_in_round=claim.total_models_in_round,
        support_scope_model_count=claim.support_scope_model_count,
        confidence=claim.confidence,
        created_at=claim.created_at,
    )


def claim_processing_attempt_public(
    attempt: ClaimProcessingAttempt,
) -> ClaimProcessingAttemptPublic:
    return ClaimProcessingAttemptPublic(
        id=attempt.id,
        operation=attempt.operation,
        round_number=attempt.round_number,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=attempt.model_identity_source,
        target_model_response_id=attempt.target_model_response_id,
        target_claim_ids=list(attempt.target_claim_ids),
        transport_status=attempt.transport_status,
        transport_error=attempt.transport_error,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage=attempt.usage,
        cost_usd=attempt.cost_usd,
        pricing_provenance=attempt.pricing_provenance,
        latency_ms=attempt.latency_ms,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        created_at=attempt.created_at,
    )


def judge_verdict_public(verdict: JudgeVerdict | None) -> JudgeVerdictPublic | None:
    if verdict is None:
        return None
    return JudgeVerdictPublic(
        id=verdict.id,
        evaluated_through_round=verdict.evaluated_through_round,
        judge_model=verdict.judge_model,
        judge_model_identity_source=verdict.judge_model_identity_source,
        claim_assessments=[
            ClaimAssessmentPublic(
                claim_id=a.claim_id, verdict=a.verdict, explanation=a.explanation
            )
            for a in verdict.claim_assessments
        ],
        best_arguments_by=dict(verdict.best_arguments_by),
        debate_limitations=list(verdict.debate_limitations),
        confidence=verdict.confidence,
        reasoning=verdict.reasoning,
        created_at=verdict.created_at,
    )


def judge_attempt_public(attempt: JudgeAttempt) -> JudgeAttemptPublic:
    return JudgeAttemptPublic(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=attempt.model_identity_source,
        transport_status=attempt.transport_status,
        transport_error=attempt.transport_error,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage=attempt.usage,
        cost_usd=attempt.cost_usd,
        pricing_provenance=attempt.pricing_provenance,
        latency_ms=attempt.latency_ms,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        created_at=attempt.created_at,
    )


def editor_attempt_public(attempt: EditorAttempt) -> EditorAttemptPublic:
    return EditorAttemptPublic(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=attempt.model_identity_source,
        transport_status=attempt.transport_status,
        transport_error=attempt.transport_error,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage=attempt.usage,
        cost_usd=attempt.cost_usd,
        pricing_provenance=attempt.pricing_provenance,
        latency_ms=attempt.latency_ms,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        created_at=attempt.created_at,
    )


def final_answer_public(fa: FinalAnswer) -> FinalAnswerPublic:
    return FinalAnswerPublic(
        answer_text=fa.answer_text,
        limitations=list(fa.limitations),
        status=fa.status,
        editor_model=fa.editor_model,
        editor_model_identity_source=fa.editor_model_identity_source,
        judge_confidence=fa.judge_confidence,
    )


def run_config_public(rc: RunConfig) -> RunConfigPublic:
    return RunConfigPublic(
        question=rc.question,
        enabled_providers=list(rc.enabled_providers),
        claim_processor_provider=rc.claim_processor_provider,
        judge_provider=rc.judge_provider,
        editor_provider=rc.editor_provider,
        source_analyzer_provider=rc.source_analyzer_provider,
        source_text=rc.source_text,
        max_cost_usd=rc.max_cost_usd,
        max_total_tokens=rc.max_total_tokens,
        max_output_tokens_per_call=rc.max_output_tokens_per_call,
        max_output_tokens_grouping=rc.max_output_tokens_grouping,
        max_output_tokens_judge=rc.max_output_tokens_judge,
        round_dispatch_timeout_seconds=rc.round_dispatch_timeout_seconds,
        quorum=QuorumPublic(
            min_for_debate=rc.quorum.min_for_debate, min_to_return=rc.quorum.min_to_return
        ),
    )


def accounting_summary(result: CouncilRunResult) -> AccountingSummary:
    return AccountingSummary(
        total_input_tokens=result.total_input_tokens,
        total_output_tokens=result.total_output_tokens,
        estimated_cost_usd=result.total_cost_usd,
        has_unknown_accounting_components=result.has_unknown_accounting_components,
    )


def completed_run_response(
    result: CouncilRunResult, *, provider_execution_policy: ProviderExecutionPolicy | None
) -> CompletedRunResponse:
    """`provider_execution_policy` (T02.2) é passado explicitamente pelo
    chamador -- `CouncilRunResult` NUNCA carrega isso (autoridade
    distinta, ver docstring de `ProviderExecutionPolicy`). Quem chama
    isto no caminho síncrono de criação (`POST /runs`) usa o snapshot
    que acabou de ser aceito (`AppComponents.provider_execution_policy`);
    quem chama depois de reconstruir de storage (`GET /runs/{id}`) usa
    `CompletedRunRecord.provider_execution_policy` -- os dois SEMPRE
    concordam pra qualquer run real (mesma instância resolvida), mas
    esta função nunca decide isso por conta própria."""
    return CompletedRunResponse(
        id=result.id,
        started_at=result.started_at,
        completed_at=result.completed_at,
        final_answer=final_answer_public(result.final_answer),
        accounting=accounting_summary(result),
        config=run_config_public(result.run_config),
        provider_execution_policy=provider_execution_policy,
    )


def quorum_failure_run_response(record: QuorumFailureRecord) -> QuorumFailureRunResponse:
    return QuorumFailureRunResponse(
        id=record.id,
        started_at=record.started_at,
        failed_at=record.failed_at,
        successful_count=record.successful_count,
        total_providers=record.total_providers,
        min_to_return=record.min_to_return,
        accounting=_round_accounting(record.round_result),
        config=run_config_public(record.run_config),
        provider_execution_policy=record.provider_execution_policy,
    )


def deterministic_verification_attempt_public(
    attempt: DeterministicVerificationAttempt,
) -> DeterministicVerificationAttemptPublic:
    assertion = (
        ArithmeticAssertionPublic(
            kind=attempt.assertion.kind,
            left=attempt.assertion.left,
            operator=attempt.assertion.operator,
            right=attempt.assertion.right,
            asserted_result=attempt.assertion.asserted_result,
        )
        if attempt.assertion is not None
        else None
    )
    return DeterministicVerificationAttemptPublic(
        id=attempt.id,
        claim_id=attempt.claim_id,
        state=attempt.state,
        raw_proposal=attempt.raw_proposal,
        assertion=assertion,
        computed_result=attempt.computed_result,
        created_at=attempt.created_at,
    )


def source_analysis_attempt_public(attempt: SourceAnalysisAttempt) -> SourceAnalysisAttemptPublic:
    return SourceAnalysisAttemptPublic(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        provider=attempt.provider,
        requested_model=attempt.requested_model,
        model=attempt.model,
        model_identity_source=attempt.model_identity_source,
        transport_status=attempt.transport_status,
        transport_error=attempt.transport_error,
        raw_output_text=attempt.raw_output_text,
        parse_status=attempt.parse_status,
        parse_error_message=attempt.parse_error_message,
        usage=attempt.usage,
        cost_usd=attempt.cost_usd,
        pricing_provenance=attempt.pricing_provenance,
        latency_ms=attempt.latency_ms,
        had_uncertain_prior_attempts=attempt.had_uncertain_prior_attempts,
        provider_finish_reason=attempt.provider_finish_reason,
        created_at=attempt.created_at,
    )


def source_claim_analysis_result_public(
    result: SourceClaimAnalysisResult,
) -> SourceClaimAnalysisResultPublic:
    if isinstance(result, ValidSourceRelation):
        return ValidSourceRelationPublic(
            kind="relation",
            id=result.id,
            claim_id=result.claim_id,
            relation=result.relation,
            excerpt=result.excerpt,
            excerpt_start=result.excerpt_start,
            excerpt_end=result.excerpt_end,
            created_at=result.created_at,
        )
    return RejectedSourceEntryPublic(
        kind="rejected",
        id=result.id,
        claim_id=result.claim_id,
        reason=result.reason,
        raw_entry=result.raw_entry,
        created_at=result.created_at,
    )


def source_analysis_outcome_public(
    source_analysis: SourceAnalysisResult | None,
) -> SourceAnalysisOutcome | None:
    if source_analysis is None:
        return None
    return SourceAnalysisOutcome(
        skipped_reason=source_analysis.skipped_reason,
        source_analyzer_provider=source_analysis.source_analyzer_provider,
        cumulative_budget_exceeded=source_analysis.cumulative_budget_exceeded,
        attempts=[source_analysis_attempt_public(a) for a in source_analysis.attempts],
        claim_results=[
            source_claim_analysis_result_public(r) for r in source_analysis.claim_results
        ],
    )


def completed_run_audit(
    result: CouncilRunResult, *, provider_execution_policy: ProviderExecutionPolicy | None
) -> CompletedRunAudit:
    """Ver docstring de `completed_run_response` -- mesma disciplina de
    passagem explícita de `provider_execution_policy`."""
    debate = result.debate_result
    judge = result.judge_result
    editor = result.editor_result
    return CompletedRunAudit(
        id=result.id,
        started_at=result.started_at,
        completed_at=result.completed_at,
        config=run_config_public(result.run_config),
        debate_outcome=DebateOutcome(
            skipped_reason=debate.debate_skipped_reason,
            cumulative_budget_exceeded=debate.cumulative_budget_exceeded,
        ),
        judge_outcome=JudgeOutcome(
            verdict_unavailable_reason=judge.verdict_unavailable_reason,
            cumulative_budget_exceeded=judge.cumulative_budget_exceeded,
        ),
        editor_outcome=EditorOutcome(
            fallback_reason=editor.fallback_reason,
            cumulative_budget_exceeded=editor.cumulative_budget_exceeded,
        ),
        source_analysis=source_analysis_outcome_public(result.source_analysis_result),
        initial_round=initial_round_audit(debate.initial_result),
        critique_round=critique_round_audit(debate.critique_round),
        claims=[claim_public(c) for c in debate.claims],
        claim_processing_attempts=[
            claim_processing_attempt_public(a) for a in debate.claim_processing_attempts
        ],
        numeric_verification_attempts=[
            deterministic_verification_attempt_public(a)
            for a in debate.numeric_verification_attempts
        ],
        judge_verdict=judge_verdict_public(judge.verdict),
        judge_attempts=[judge_attempt_public(a) for a in judge.attempts],
        editor_attempts=[editor_attempt_public(a) for a in editor.attempts],
        final_answer=final_answer_public(editor.final_answer),
        accounting=accounting_summary(result),
        provider_execution_policy=provider_execution_policy,
    )


def quorum_failure_audit(record: QuorumFailureRecord) -> QuorumFailureAudit:
    return QuorumFailureAudit(
        id=record.id,
        started_at=record.started_at,
        failed_at=record.failed_at,
        config=run_config_public(record.run_config),
        successful_count=record.successful_count,
        total_providers=record.total_providers,
        min_to_return=record.min_to_return,
        round_result=round_audit(record.round_result),
        provider_execution_policy=record.provider_execution_policy,
    )


def running_run_response(record: AcceptedRunRecord) -> RunningRunResponse:
    assert record.status == "running"
    return RunningRunResponse(
        id=record.id,
        started_at=record.started_at,
        config=run_config_public(record.run_config),
        provider_execution_policy=record.provider_execution_policy,
    )


def failed_run_response(record: AcceptedRunRecord) -> FailedRunResponse:
    assert record.status == "failed"
    assert record.failed_at is not None
    assert record.failure_classification is not None
    assert record.failure_message is not None
    return FailedRunResponse(
        id=record.id,
        started_at=record.started_at,
        failed_at=record.failed_at,
        failure_reason=record.failure_classification,
        message=record.failure_message,
        config=run_config_public(record.run_config),
        provider_execution_policy=record.provider_execution_policy,
    )


def run_summary_response(summary: RunSummary) -> RunSummaryResponse:
    return RunSummaryResponse(
        id=summary.id,
        status=summary.status,
        started_at=summary.started_at,
        ended_at=summary.ended_at,
    )
