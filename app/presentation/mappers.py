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

import math
from typing import Literal

from app.presentation.schemas import (
    AccountingSummary,
    AnswerBlockPublic,
    AnswerClaimItemPublic,
    AnswerClaimSectionBlockPublic,
    AnswerParagraphBlockPublic,
    ArithmeticAssertionPublic,
    ClaimAssessmentPublic,
    ClaimProcessingAttemptPublic,
    PrimaryAnswerItemPublic,
    PrimaryAnswerPublic,
    PrimaryAnswerSectionPublic,
    ClaimPublic,
    ClaimReconciliationOutcomePublic,
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
    NaturalAnswerPublic,
    LinguisticRealizationBlockPublic,
    LinguisticRealizationPublic,
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
    SourceJudgeReconciliationResultPublic,
    ValidSourceRelationPublic,
)
from app.council.result import CouncilRunResult
from app.reconciliation.models import SourceJudgeReconciliationResult
from app.debate.numeric_verification import DeterministicVerificationAttempt
from app.models.provider_models import DefaultModelAuthoritySnapshot, ProviderExecutionPolicy
from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.result import CritiqueResult
from app.editor.answer_blocks import AnswerBlock, AnswerClaimSectionBlock, AnswerParagraphBlock
from app.editor.attempt import EditorAttempt
from app.editor.natural_answer import NaturalAnswer, natural_answer_is_presentation_eligible
from app.editor.linguistic_realization import (
    LinguisticRealization,
    linguistic_realization_is_presentation_eligible,
)
from app.editor.primary_answer import PrimaryAnswer
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
        request_provenance=mr.request_provenance,
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
        request_provenance=attempt.request_provenance,
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
        request_provenance=attempt.request_provenance,
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
        request_provenance=attempt.request_provenance,
        created_at=attempt.created_at,
    )


def answer_block_public(block: AnswerBlock) -> AnswerBlockPublic:
    """UI Slice 3 -- projeção 1:1 de app/editor/answer_blocks.py pros
    schemas públicos (nenhum cálculo, nenhuma decisão nova -- mesmo
    princípio 1/7 da Decision Delta que todo o resto deste módulo já
    segue). `isinstance` -- não um dict de despacho por `kind` -- porque
    só 2 variantes existem hoje e cada uma já carrega seu próprio
    Literal `kind` fixo; ver `source_claim_analysis_result_public` acima
    pro mesmo padrão já usado nesta base pra outra union discriminada."""
    if isinstance(block, AnswerParagraphBlock):
        return AnswerParagraphBlockPublic(kind="paragraph", text=block.text)
    assert isinstance(block, AnswerClaimSectionBlock)
    return AnswerClaimSectionBlockPublic(
        kind="claim_section",
        heading=block.heading,
        items=[
            AnswerClaimItemPublic(
                claim_text=item.claim_text,
                verdict_label=item.verdict_label,
                explanation=item.explanation,
                source_relationship_note=item.source_relationship_note,
            )
            for item in block.items
        ],
    )


def primary_answer_public(primary: PrimaryAnswer | None) -> PrimaryAnswerPublic | None:
    if primary is None:
        return None
    return PrimaryAnswerPublic(
        contract_version=primary.contract_version,
        based_on_verdict_id=primary.based_on_verdict_id,
        lead_in=primary.lead_in,
        sections=[
            PrimaryAnswerSectionPublic(
                role=section.role,
                heading=section.heading,
                items=[
                    PrimaryAnswerItemPublic(
                        claim_id=item.claim_id,
                        claim_text=item.claim_text,
                        verdict_label=item.verdict_label,
                    )
                    for item in section.items
                ],
            )
            for section in primary.sections
        ],
        limitations=list(primary.limitations),
        assessed_claim_count=primary.assessed_claim_count,
        selected_claim_count=primary.selected_claim_count,
        omitted_not_established_count=primary.omitted_not_established_count,
        scope_note=primary.scope_note,
        rendered_text=primary.rendered_text,
    )


def natural_answer_public(natural: NaturalAnswer | None) -> NaturalAnswerPublic | None:
    if natural is None:
        return None
    return NaturalAnswerPublic(
        renderer_contract_version=natural.renderer_contract_version,
        based_on_verdict_id=natural.based_on_verdict_id,
        rendered_text=natural.rendered_text,
    )


def linguistic_realization_public(
    realization: LinguisticRealization | None,
) -> LinguisticRealizationPublic | None:
    if realization is None:
        return None
    return LinguisticRealizationPublic(
        contract_version=realization.contract_version,
        based_on_primary_answer_digest=realization.based_on_primary_answer_digest,
        blocks=[
            LinguisticRealizationBlockPublic(claim_ids=list(block.claim_ids), text=block.text)
            for block in realization.blocks
        ],
        rendered_text=realization.rendered_text,
    )


def final_answer_public(fa: FinalAnswer) -> FinalAnswerPublic:
    natural_presentation_eligible = (
        fa.natural_answer is not None
        and fa.primary_answer is not None
        and natural_answer_is_presentation_eligible(fa.primary_answer)
    )
    realization_presentation_eligible = (
        fa.linguistic_realization is not None
        and fa.primary_answer is not None
        and linguistic_realization_is_presentation_eligible(
            fa.linguistic_realization, fa.primary_answer
        )
    )
    return FinalAnswerPublic(
        answer_text=fa.answer_text,
        answer_blocks=(
            [answer_block_public(b) for b in fa.answer_blocks] if fa.answer_blocks is not None else None
        ),
        unevaluated_claims=(
            list(fa.unevaluated_claims) if fa.unevaluated_claims is not None else None
        ),
        primary_answer=primary_answer_public(fa.primary_answer),
        natural_answer=natural_answer_public(fa.natural_answer),
        natural_answer_presentation_eligible=natural_presentation_eligible,
        linguistic_realization=linguistic_realization_public(fa.linguistic_realization),
        linguistic_realization_presentation_eligible=realization_presentation_eligible,
        limitations=list(fa.limitations),
        status=fa.status,
        editor_model=fa.editor_model,
        editor_model_identity_source=fa.editor_model_identity_source,
        judge_confidence=fa.judge_confidence,
    )


def _positive_execution_limit_public(value: float) -> float | Literal["positive_infinity"]:
    """Historical Non-Finite Execution-Limit Public Representation V1 --
    ÚNICO ponto de conversão domínio -> público pros dois campos que
    historicamente podiam ser `+inf` (`RunConfig.max_cost_usd`/
    `round_dispatch_timeout_seconds`, aceitos antes de "Deployment
    Execution Configuration Boundary V1"). `run_config_public` (abaixo)
    é o ÚNICO chamador -- nenhum lifecycle root (completed/quorum
    failure/running/failed, detail OU audit) reimplementa esta
    conversão separadamente, porque todos passam pelo mesmo
    `run_config_public`.

    - finito E positivo -> valor numérico, inalterado;
    - `+inf` (o ÚNICO não-finito historicamente alcançável por este
      campo -- `RunConfig` usa `Field(gt=0)`, que já rejeitava `-inf`/
      NaN antes desta slice existir) -> token de compatibilidade
      OUTWARD `"positive_infinity"`;
    - qualquer outro estado (finito <= 0, `-inf`, NaN) -- nenhum deles
      nunca deveria alcançar este ponto vindo de um `RunConfig` real
      (`Field(gt=0)` já os exclui na origem), mas a função FALHA
      FECHADO explicitamente em vez de silenciosamente devolver um
      número <=0/virar `null`/ser clampada/reinterpretada -- endurecimento
      de fronteira de apresentação (review F2), não uma mudança de
      contrato de `RunConfig`/Settings."""
    if math.isfinite(value) and value > 0:
        return value
    if value == math.inf:
        return "positive_infinity"
    raise ValueError(
        "valor de limite de execução fora do domínio suportado pra "
        f"representação pública histórica: {value!r} (esperado positivo "
        "finito, ou +inf historicamente alcançável)"
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
        max_cost_usd=_positive_execution_limit_public(rc.max_cost_usd),
        max_total_tokens=rc.max_total_tokens,
        max_output_tokens_per_call=rc.max_output_tokens_per_call,
        max_output_tokens_grouping=rc.max_output_tokens_grouping,
        max_output_tokens_judge=rc.max_output_tokens_judge,
        round_dispatch_timeout_seconds=_positive_execution_limit_public(
            rc.round_dispatch_timeout_seconds
        ),
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
    result: CouncilRunResult,
    *,
    provider_execution_policy: ProviderExecutionPolicy | None,
    default_model_authority_snapshot: DefaultModelAuthoritySnapshot | None = None,
) -> CompletedRunResponse:
    """`provider_execution_policy` (T02.2) é passado explicitamente pelo
    chamador -- `CouncilRunResult` NUNCA carrega isso (autoridade
    distinta, ver docstring de `ProviderExecutionPolicy`). TODO chamador
    -- tanto o caminho síncrono de criação (`POST /runs`/`dialeon run`,
    logo depois de `CouncilExecutionService.run()` suceder) quanto o de
    reconstrução (`GET /runs/{id}`) -- recarrega o registro recém-
    persistido (`repository.get_run(result.id)`) e usa
    `CompletedRunRecord.provider_execution_policy` de lá; nenhum dos dois
    lê `AppComponents.provider_execution_policy` pra montar a resposta de
    um Run (ver docstring de `AppComponents`, app/bootstrap.py) -- ler de
    volta o registro persistido é o que garante que a resposta imediata
    reporte exatamente o que foi aceito, nunca um valor potencialmente
    diferente observado depois. Esta função nunca decide isso por conta
    própria, só recebe o valor já resolvido pelo chamador.

    `default_model_authority_snapshot` (Provider Default-Model Snapshot
    Provenance V1) segue a MESMA disciplina -- mas, diferente de
    `provider_execution_policy` (um singleton de deployment, sempre
    idêntico independente do run), varia POR RUN (depende de
    `run_config.all_provider_authorities`). F1 (repair pós-revisão
    independente, MEDIUM): por variar por run, este valor NUNCA pode
    ser recomputado a partir do registry de provider AO VIVO no
    caminho síncrono de criação -- isso reintroduziria exatamente o
    bug que esta provenance existe pra fechar (o registry pode ter
    mudado entre o aceite e a resposta, mesmo dentro da mesma request).
    TODO chamador -- síncrono (`POST /runs`/`dialeon run`, logo depois
    de `CouncilExecutionService.run()` suceder) OU de reconstrução
    (`GET /runs/{id}`) -- SEMPRE passa
    `CompletedRunRecord.default_model_authority_snapshot`, recarregado
    de `repository.get_run(result.id)` no caso síncrono: o fato de
    provenance autoritativo é sempre o que foi PERSISTIDO no aceite,
    nunca uma reconstrução independente."""
    return CompletedRunResponse(
        id=result.id,
        started_at=result.started_at,
        completed_at=result.completed_at,
        final_answer=final_answer_public(result.final_answer),
        accounting=accounting_summary(result),
        config=run_config_public(result.run_config),
        provider_execution_policy=provider_execution_policy,
        default_model_authority_snapshot=default_model_authority_snapshot,
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
        default_model_authority_snapshot=record.default_model_authority_snapshot,
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
        request_provenance=attempt.request_provenance,
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


def reconciliation_public(
    reconciliation: SourceJudgeReconciliationResult | None,
) -> SourceJudgeReconciliationResultPublic | None:
    """Cross-Channel Reconciliation V1. `None` só pra execuções
    persistidas antes deste slice existir -- ver docstring de
    `SourceJudgeReconciliationResultPublic`."""
    if reconciliation is None:
        return None
    return SourceJudgeReconciliationResultPublic(
        contract_version=reconciliation.contract_version,
        status=reconciliation.status,
        claim_outcomes=[
            ClaimReconciliationOutcomePublic(
                claim_id=o.claim_id,
                judge_verdict_id=o.judge_verdict_id,
                source_claim_result_ids=o.source_claim_result_ids,
                source_state=o.source_state,
                channel_relationship=o.channel_relationship,
            )
            for o in reconciliation.claim_outcomes
        ],
    )


def completed_run_audit(
    result: CouncilRunResult,
    *,
    provider_execution_policy: ProviderExecutionPolicy | None,
    default_model_authority_snapshot: DefaultModelAuthoritySnapshot | None = None,
) -> CompletedRunAudit:
    """Ver docstring de `completed_run_response` -- mesma disciplina de
    passagem explícita de `provider_execution_policy`/
    `default_model_authority_snapshot`."""
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
            claim_extraction_eligible_response_count=debate.claim_extraction_eligible_response_count,
            claim_extraction_missing_response_count=debate.claim_extraction_missing_response_count,
        ),
        judge_outcome=JudgeOutcome(
            verdict_unavailable_reason=judge.verdict_unavailable_reason,
            cumulative_budget_exceeded=judge.cumulative_budget_exceeded,
        ),
        editor_outcome=EditorOutcome(
            fallback_reason=editor.fallback_reason,
            cumulative_budget_exceeded=editor.cumulative_budget_exceeded,
            primary_answer_fallback_reason=editor.primary_answer_fallback_reason,
            natural_answer_fallback_reason=editor.natural_answer_fallback_reason,
            linguistic_realization_fallback_reason=(
                editor.linguistic_realization_fallback_reason
            ),
            linguistic_semantic_review_provider=(
                editor.linguistic_semantic_review_provider
            ),
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
        primary_answer_attempts=[editor_attempt_public(a) for a in editor.primary_answer_attempts],
        linguistic_realization_attempts=[
            editor_attempt_public(a) for a in editor.linguistic_realization_attempts
        ],
        linguistic_semantic_review_attempts=[
            editor_attempt_public(a) for a in editor.linguistic_semantic_review_attempts
        ],
        final_answer=final_answer_public(editor.final_answer),
        accounting=accounting_summary(result),
        provider_execution_policy=provider_execution_policy,
        default_model_authority_snapshot=default_model_authority_snapshot,
        reconciliation=reconciliation_public(result.reconciliation),
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
        default_model_authority_snapshot=record.default_model_authority_snapshot,
    )


def running_run_response(record: AcceptedRunRecord) -> RunningRunResponse:
    assert record.status == "running"
    return RunningRunResponse(
        id=record.id,
        started_at=record.started_at,
        config=run_config_public(record.run_config),
        provider_execution_policy=record.provider_execution_policy,
        default_model_authority_snapshot=record.default_model_authority_snapshot,
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
        default_model_authority_snapshot=record.default_model_authority_snapshot,
    )


def run_summary_response(summary: RunSummary) -> RunSummaryResponse:
    return RunSummaryResponse(
        id=summary.id,
        status=summary.status,
        started_at=summary.started_at,
        ended_at=summary.ended_at,
        question=summary.question,
    )
