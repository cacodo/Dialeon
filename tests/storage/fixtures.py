from __future__ import annotations

from datetime import datetime, timezone

from app.council.result import CouncilRunResult
from app.debate.numeric_verification import build_verification_attempt
from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.result import CritiqueResult, DebateResult
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.models import RejectedSourceEntry, ValidSourceRelation
from app.source_analysis.result import SourceAnalysisResult
from app.editor.attempt import EditorAttempt
from app.editor.result import EditorResult, FinalAnswer
from app.judge.attempt import JudgeAttempt
from app.judge.result import JudgeResult
from app.models.domain import Claim, ClaimAssessment, ClaimSupport, JudgeVerdict, ModelResponse
from app.models.provider_models import (
    PricingProvenance,
    ProviderErrorInfo,
    ProviderErrorType,
    TokenUsage,
)
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.result import InitialResponsesResult, RoundResult


def now() -> datetime:
    return datetime.now(timezone.utc)


def provenance(**overrides) -> PricingProvenance:
    fields = dict(
        source_id="test-pricing-table",
        tier="standard",
        input_rate_usd_per_million_tokens=5.0,
        output_rate_usd_per_million_tokens=30.0,
    )
    fields.update(overrides)
    return PricingProvenance(**fields)


def model_response(provider: str = "openai", **overrides) -> ModelResponse:
    fields = dict(
        provider=provider,
        model="gpt-5.5",
        round_number=1,
        status="success",
        response_text=f"resposta de {provider}",
        usage=TokenUsage(input_tokens=100, output_tokens=20),
        cost_usd=0.001,
        pricing_provenance=provenance(),
        latency_ms=500,
        attempts=1,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return ModelResponse(**fields)


def error_model_response(provider: str = "gemini", **overrides) -> ModelResponse:
    fields = dict(
        provider=provider,
        model="gemini-3.7-flash",
        round_number=1,
        status="error",
        usage=None,
        cost_usd=None,
        pricing_provenance=None,
        latency_ms=100,
        attempts=2,
        error=ProviderErrorInfo(type=ProviderErrorType.TIMEOUT, message="timeout", retryable=True),
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return ModelResponse(**fields)


def claim(source_model_response_id: str, supports: list[ClaimSupport], **overrides) -> Claim:
    fields = dict(
        text="Claim de teste.",
        source_model_response_id=source_model_response_id,
        round_introduced=1,
        status="consensus",
        supporting_model_response_ids=supports,
        total_models_in_round=len(supports),
    )
    fields.update(overrides)
    return Claim(**fields)


def run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Qual a capital do Brasil?",
        enabled_providers=["openai", "anthropic"],
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        overall_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)


def full_council_run_result(**overrides) -> CouncilRunResult:
    """CouncilRunResult rico — 2 respostas, 1 claim com 2 supports, 1
    critique round, 1 claim_processing_attempt, 1 verdict com 1
    assessment, 1 judge attempt, 1 editor attempt — cobre praticamente
    toda a árvore de tabelas num round-trip só."""
    mr1 = model_response("openai")
    mr2 = model_response("anthropic", model="claude-sonnet-5")
    critique_mr = model_response("openai", round_number=2, response_text="resposta de crítica")

    support1 = ClaimSupport(model_response_id=mr1.id, provider="openai", model="gpt-5.5")
    support2 = ClaimSupport(
        model_response_id=mr2.id, provider="anthropic", model="claude-sonnet-5"
    )
    c1 = claim(mr1.id, [support1, support2])

    processing_attempt = ClaimProcessingAttempt(
        operation="extraction",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        target_model_response_id=mr1.id,
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=40, output_tokens=15),
        cost_usd=0.0003,
        pricing_provenance=provenance(),
        latency_ms=250,
    )

    initial_result = InitialResponsesResult(
        responses=[mr1, mr2],
        successful_count=2,
        total_providers=2,
        insufficient_data_for_consensus=False,
        total_input_tokens=200,
        total_output_tokens=40,
        total_cost_usd=0.002,
        has_unknown_accounting_components=False,
        budget_exceeded=False,
    )
    critique_round = CritiqueResult(
        round_result=RoundResult(
            round_number=2,
            responses=[critique_mr],
            successful_count=1,
            total_participants=1,
            total_input_tokens=100,
            total_output_tokens=20,
            total_cost_usd=0.001,
            has_unknown_accounting_components=False,
        )
    )

    debate_result = DebateResult(
        initial_result=initial_result,
        critique_round=critique_round,
        claims=[c1],
        claim_processing_attempts=[processing_attempt],
        claim_processor_provider="anthropic",
        debate_skipped_reason=None,
        cumulative_budget_exceeded=False,
    )

    verdict = JudgeVerdict(
        evaluated_through_round=2,
        judge_model="claude-sonnet-5",
        claim_assessments=[
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="Consenso total.")
        ],
        best_arguments_by={"openai/gpt-5.5": "Argumento mais direto."},
        debate_limitations=["Só uma rodada de crítica."],
        confidence=0.9,
        reasoning="Ambos os modelos concordam sem contestação.",
    )
    judge_attempt = JudgeAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=50, output_tokens=10),
        cost_usd=0.0001,
        pricing_provenance=provenance(),
        latency_ms=300,
    )
    judge_result = JudgeResult(
        verdict=verdict,
        attempts=[judge_attempt],
        verdict_unavailable_reason=None,
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    final_answer = FinalAnswer(
        answer_text="Brasília é a capital do Brasil.",
        limitations=["Só uma rodada de crítica."],
        status="llm_composed",
        editor_model="claude-sonnet-5",
        based_on_verdict_id=verdict.id,
        judge_confidence=0.9,
    )
    editor_attempt = EditorAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=30, output_tokens=8),
        cost_usd=0.00005,
        pricing_provenance=provenance(),
        latency_ms=200,
    )
    editor_result = EditorResult(
        final_answer=final_answer,
        attempts=[editor_attempt],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    fields = dict(
        run_config=run_config(),
        debate_result=debate_result,
        source_analysis_result=None,
        judge_result=judge_result,
        editor_result=editor_result,
        started_at=now(),
        completed_at=now(),
    )
    fields.update(overrides)
    return CouncilRunResult(**fields)


def very_rich_council_run_result() -> CouncilRunResult:
    """Fixture desenhada especificamente pro teste de round-trip forte —
    cobre em UM objeto só tudo que o patch de auditabilidade precisa
    provar: >=2 responses na mesma rodada em ordem conhecida, critique
    round, >=2 claims em ordem conhecida, >=2 claim_processing_attempts
    (malformed -> accepted, estruturalmente válido), >=2 JudgeAttempts
    (idem), >=2 EditorAttempts (malformed -> accepted — EditorResult
    exige attempts[-1] aceito), >=2 ClaimAssessments, EvidenceRef
    preenchido, TokenUsage(None, None) presente, pricing provenance,
    timestamps."""
    from app.models.domain import EvidenceRef

    prov = provenance()

    mr1 = model_response("openai", model="gpt-5.5")
    mr2 = model_response("anthropic", model="claude-sonnet-5")
    # 3º response com TokenUsage(None, None) — caso central do patch
    mr3 = model_response(
        "gemini",
        model="gemini-3.7-flash",
        usage=TokenUsage(input_tokens=None, output_tokens=None),
        cost_usd=None,
        pricing_provenance=None,
    )
    critique_mr1 = model_response("openai", round_number=2, response_text="crítica 1")
    critique_mr2 = model_response(
        "anthropic", model="claude-sonnet-5", round_number=2, response_text="crítica 2"
    )

    support1 = ClaimSupport(model_response_id=mr1.id, provider="openai", model="gpt-5.5")
    support2 = ClaimSupport(
        model_response_id=mr2.id, provider="anthropic", model="claude-sonnet-5"
    )
    evidence = EvidenceRef(
        claim_id="placeholder",  # ajustado abaixo, depois que c1 existir
        source_url="https://exemplo.com/fonte-externa",
        summary="Fonte externa que corrobora a claim.",
        verification_method="web_search",
    )
    c1 = claim(mr1.id, [support1, support2], text="Primeira claim, em ordem.")
    evidence = evidence.model_copy(update={"claim_id": c1.id})
    c1 = c1.model_copy(update={"external_evidence": evidence})
    c2 = claim(mr2.id, [support2], text="Segunda claim, em ordem.", round_introduced=1)
    # Etapa 16 -- claims extras só pra dar cada uma um claim_id distinto
    # pros 6 cenários de source analysis exigidos no round-trip de
    # fidelidade total (supports/contradicts/unresolved/omitted/
    # duplicate/unknown não podem compartilhar claim_id).
    c3 = claim(mr1.id, [support1], text="Terceira claim, contradita pela fonte.")
    c4 = claim(mr1.id, [support1], text="Quarta claim, não resolvida pela fonte.")
    c5 = claim(mr1.id, [support1], text="Quinta claim, omitida pela análise.")

    proc_attempt_1 = ClaimProcessingAttempt(
        operation="extraction",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        target_model_response_id=mr1.id,
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{bad json",
        parse_status="malformed",
        parse_error_message="JSON inválido.",
        usage=TokenUsage(input_tokens=20, output_tokens=5),
        cost_usd=0.0001,
        pricing_provenance=prov,
        latency_ms=100,
    )
    proc_attempt_2 = ClaimProcessingAttempt(
        operation="extraction",
        round_number=1,
        attempt_number=2,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        target_model_response_id=mr1.id,
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=25, output_tokens=8),
        cost_usd=0.00015,
        pricing_provenance=prov,
        latency_ms=120,
    )

    initial_result = InitialResponsesResult(
        responses=[mr1, mr2, mr3],
        successful_count=3,
        total_providers=3,
        insufficient_data_for_consensus=False,
        total_input_tokens=200,
        total_output_tokens=40,
        total_cost_usd=0.002,
        has_unknown_accounting_components=True,  # por causa de mr3
        budget_exceeded=False,
    )
    critique_round = CritiqueResult(
        round_result=RoundResult(
            round_number=2,
            responses=[critique_mr1, critique_mr2],
            successful_count=2,
            total_participants=2,
            total_input_tokens=200,
            total_output_tokens=40,
            total_cost_usd=0.002,
            has_unknown_accounting_components=False,
        )
    )

    debate_result = DebateResult(
        initial_result=initial_result,
        critique_round=critique_round,
        claims=[c1, c2, c3, c4, c5],
        claim_processing_attempts=[proc_attempt_1, proc_attempt_2],
        numeric_verification_attempts=[
            build_verification_attempt(
                c1.id, {"left": "2", "operator": "+", "right": "2", "asserted_result": "4"}
            ),
            build_verification_attempt(
                c2.id, {"left": "2", "operator": "%%%", "right": "2", "asserted_result": "4"}
            ),
        ],
        claim_processor_provider="anthropic",
        debate_skipped_reason=None,
        cumulative_budget_exceeded=False,
    )

    judge_attempt_1 = JudgeAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{bad",
        parse_status="malformed",
        parse_error_message="JSON inválido.",
        usage=TokenUsage(input_tokens=30, output_tokens=6),
        cost_usd=0.0002,
        pricing_provenance=prov,
        latency_ms=200,
    )
    judge_attempt_2 = JudgeAttempt(
        attempt_number=2,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=35, output_tokens=9),
        cost_usd=0.00025,
        pricing_provenance=prov,
        latency_ms=210,
    )
    verdict = JudgeVerdict(
        evaluated_through_round=2,
        judge_model="claude-sonnet-5",
        claim_assessments=[
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="Primeira avaliação."),
            ClaimAssessment(
                claim_id=c2.id, verdict="partially_supported", explanation="Segunda avaliação."
            ),
        ],
        best_arguments_by={"openai/gpt-5.5": "Melhor argumento."},
        debate_limitations=["Limitação 1.", "Limitação 2."],
        confidence=0.85,
        reasoning="Raciocínio consolidado do juiz.",
    )
    judge_result = JudgeResult(
        verdict=verdict,
        attempts=[judge_attempt_1, judge_attempt_2],
        verdict_unavailable_reason=None,
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    editor_attempt_1 = EditorAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{bad",
        parse_status="malformed",
        parse_error_message="JSON inválido.",
        usage=TokenUsage(input_tokens=15, output_tokens=4),
        cost_usd=0.00005,
        pricing_provenance=prov,
        latency_ms=150,
    )
    editor_attempt_2 = EditorAttempt(
        attempt_number=2,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=18, output_tokens=6),
        cost_usd=0.00007,
        pricing_provenance=prov,
        latency_ms=160,
    )
    final_answer = FinalAnswer(
        answer_text="Resposta final consolidando as duas claims.",
        limitations=["Limitação 1.", "Limitação 2."],
        status="llm_composed",
        editor_model="claude-sonnet-5",
        based_on_verdict_id=verdict.id,
        judge_confidence=0.85,
    )
    editor_result = EditorResult(
        final_answer=final_answer,
        attempts=[editor_attempt_1, editor_attempt_2],
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    # Etapa 16 -- os 6 cenários exigidos pelo round-trip de fidelidade
    # total, cada um com claim_id distinto (c1..c5 + um desconhecido).
    source_text = (
        "O relatório anual confirma que a receita cresceu 12% em 2025. "
        "Não há menção a lucro líquido neste trecho."
    )
    source_analysis_attempt = SourceAnalysisAttempt(
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{...}",
        parse_status="accepted",
        usage=TokenUsage(input_tokens=120, output_tokens=40),
        cost_usd=0.0004,
        pricing_provenance=prov,
        latency_ms=300,
    )
    supports_excerpt = "a receita cresceu 12% em 2025"
    supports_start = source_text.find(supports_excerpt)
    contradicts_excerpt = "Não há menção a lucro líquido neste trecho."
    contradicts_start = source_text.find(contradicts_excerpt)
    source_claim_results = [
        ValidSourceRelation(
            claim_id=c1.id,
            relation="supports",
            excerpt=supports_excerpt,
            excerpt_start=supports_start,
            excerpt_end=supports_start + len(supports_excerpt),
        ),
        ValidSourceRelation(
            claim_id=c3.id,
            relation="contradicts",
            excerpt=contradicts_excerpt,
            excerpt_start=contradicts_start,
            excerpt_end=contradicts_start + len(contradicts_excerpt),
        ),
        ValidSourceRelation(claim_id=c4.id, relation="unresolved"),
        RejectedSourceEntry(claim_id=c5.id, reason="omitted_by_model", raw_entry=None),
        RejectedSourceEntry(
            claim_id=c2.id,
            reason="duplicate_claim_id",
            raw_entry=[
                {"claim_id": c2.id, "relation": "supports", "excerpt": "x"},
                {"claim_id": c2.id, "relation": "contradicts", "excerpt": "y"},
            ],
        ),
        RejectedSourceEntry(
            claim_id=None,
            reason="invalid_entry",
            raw_entry={"claim_id": "claim-inexistente-123", "relation": "supports"},
        ),
    ]
    source_analysis_result = SourceAnalysisResult(
        attempts=[source_analysis_attempt],
        claim_results=source_claim_results,
        skipped_reason=None,
        source_analyzer_provider="anthropic",
        cumulative_budget_exceeded=False,
    )

    return CouncilRunResult(
        run_config=run_config(source_text=source_text),
        debate_result=debate_result,
        source_analysis_result=source_analysis_result,
        judge_result=judge_result,
        editor_result=editor_result,
        started_at=now(),
        completed_at=now(),
    )


def quorum_failure_exception(**overrides) -> InsufficientQuorumError:
    mr_success = model_response("openai")
    mr_error = error_model_response("anthropic", model="claude-sonnet-5")
    round_result = RoundResult(
        round_number=1,
        responses=[mr_success, mr_error],
        successful_count=1,
        total_participants=2,
        total_input_tokens=100,
        total_output_tokens=20,
        total_cost_usd=0.001,
        has_unknown_accounting_components=True,
    )
    fields = dict(
        successful_count=1, total_providers=2, min_to_return=2, round_result=round_result
    )
    fields.update(overrides)
    return InsufficientQuorumError(**fields)
