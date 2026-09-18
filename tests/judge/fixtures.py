from __future__ import annotations

from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.result import CritiqueResult, DebateResult
from app.models.domain import Claim, ClaimSupport, ModelResponse
from app.models.provider_models import TokenUsage
from app.orchestrator.result import InitialResponsesResult, RoundResult


def _support(response_id: str, provider: str = "openai") -> ClaimSupport:
    return ClaimSupport(model_response_id=response_id, provider=provider, model="test-model")


def raw_claim(text: str, response_id: str, provider: str = "openai", **overrides) -> Claim:
    fields = dict(
        text=text,
        source_model_response_id=response_id,
        round_introduced=1,
        status="active",
        supporting_model_response_ids=[_support(response_id, provider)],
        total_models_in_round=3,
    )
    fields.update(overrides)
    return Claim(**fields)


def canonical_claim(text: str, merged_from: list[str], supports: list[ClaimSupport], **overrides) -> Claim:
    fields = dict(
        text=text,
        source_model_response_id=None,
        round_introduced=1,
        merged_from_claim_ids=merged_from,
        status="active",
        supporting_model_response_ids=supports,
        total_models_in_round=3,
    )
    fields.update(overrides)
    return Claim(**fields)


def model_response(provider: str, round_number: int = 1, status: str = "success", **overrides) -> ModelResponse:
    fields = dict(
        provider=provider,
        model="fake-model",
        round_number=round_number,
        status=status,
        response_text="resposta de teste" if status == "success" else None,
        usage=TokenUsage(input_tokens=10, output_tokens=5) if status == "success" else None,
        latency_ms=50,
        attempts=1,
    )
    if status == "error":
        from app.models.provider_models import ProviderErrorInfo, ProviderErrorType

        fields["error"] = ProviderErrorInfo(
            type=ProviderErrorType.API_ERROR, message="falhou", retryable=True
        )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return ModelResponse(**fields)


def initial_result(responses: list[ModelResponse], **overrides) -> InitialResponsesResult:
    successful = [r for r in responses if r.status == "success"]
    fields = dict(
        responses=responses,
        successful_count=len(successful),
        total_providers=len(responses),
        insufficient_data_for_consensus=False,
        total_input_tokens=sum((r.usage.input_tokens or 0) for r in responses if r.usage),
        total_output_tokens=sum((r.usage.output_tokens or 0) for r in responses if r.usage),
        total_cost_usd=0.0,
        has_unknown_accounting_components=any(r.cost_usd is None for r in responses),
        budget_exceeded=False,
    )
    fields.update(overrides)
    return InitialResponsesResult(**fields)


def round_result(responses: list[ModelResponse], round_number: int = 2, **overrides) -> RoundResult:
    successful = [r for r in responses if r.status == "success"]
    fields = dict(
        round_number=round_number,
        responses=responses,
        successful_count=len(successful),
        total_participants=len(responses),
        total_input_tokens=sum((r.usage.input_tokens or 0) for r in responses if r.usage),
        total_output_tokens=sum((r.usage.output_tokens or 0) for r in responses if r.usage),
        total_cost_usd=0.0,
        has_unknown_accounting_components=any(r.cost_usd is None for r in responses),
    )
    fields.update(overrides)
    return RoundResult(**fields)


def _accepted_extraction_attempt(
    response_id: str, provider: str, round_number: int
) -> ClaimProcessingAttempt:
    """Repair (Run02 claim-extraction exhaustion) -- `DebateResult` agora
    deriva `claim_extraction_missing_response_count` a partir de
    `claim_processing_attempts` (ver app/debate/result.py). Testes de
    Judge/Editor construídos com este módulo focam em OUTRA coisa
    (veredito/composição), não em cobertura de extração -- por isso o
    default de `debate_result()` abaixo sintetiza uma tentativa de
    extração ACEITA (mínima, mas estruturalmente válida) pra cada
    `ModelResponse` bem-sucedida, garantindo que a cobertura seja
    "completa" por padrão (byte-idêntico ao comportamento de antes deste
    campo existir: nenhuma nota de cobertura incompleta aparece). Testes
    que precisam exercitar cobertura PARCIAL/FALHA TOTAL passam
    `claim_processing_attempts=[...]` explicitamente via `**overrides`,
    substituindo este default por completo."""
    return ClaimProcessingAttempt(
        operation="extraction",
        round_number=round_number,
        attempt_number=1,
        provider=provider,
        requested_model="fake-model",
        model="fake-model",
        target_model_response_id=response_id,
        transport_status="success",
        transport_attempts=1,
        raw_output_text='{"claims": []}',
        parse_status="accepted",
        # ZERO tokens/custo de propósito -- este default sintético nunca
        # deve perturbar nenhum teste de budget existente (que já fixa
        # totais exatos a partir de `initial_result`/`critique_round`
        # sozinhos). `usage` CONHECIDO-zero (não `None`/desconhecido)
        # preserva `has_unknown_accounting_components=False` também.
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        cost_usd=0.0,
        latency_ms=1,
    )


def debate_result(
    claims: list[Claim],
    initial_responses: list[ModelResponse],
    critique_responses: list[ModelResponse] | None = None,
    debate_skipped_reason: str | None = None,
    claim_processor_provider: str = "anthropic",
    **overrides,
) -> DebateResult:
    initial = initial_result(initial_responses)
    critique = None
    if critique_responses is not None:
        critique = CritiqueResult(round_result=round_result(critique_responses))

    if critique is None and debate_skipped_reason is None:
        debate_skipped_reason = "insufficient_initial_quorum"

    # Ver docstring de `_accepted_extraction_attempt` -- cobertura
    # "completa" por padrão, nunca o foco deste helper genérico.
    default_attempts = [
        _accepted_extraction_attempt(r.id, claim_processor_provider, round_number=1)
        for r in initial_responses
        if r.status == "success"
    ]
    if critique_responses is not None:
        default_attempts += [
            _accepted_extraction_attempt(r.id, claim_processor_provider, round_number=2)
            for r in critique_responses
            if r.status == "success"
        ]

    fields = dict(
        initial_result=initial,
        critique_round=critique,
        claims=claims,
        claim_processing_attempts=default_attempts,
        claim_processor_provider=claim_processor_provider,
        debate_skipped_reason=debate_skipped_reason if critique is None else None,
        cumulative_budget_exceeded=False,
    )
    fields.update(overrides)
    return DebateResult(**fields)
