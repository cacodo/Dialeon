"""
`SourceAnalyzer` — Etapa 16.

Componente CONCRETO (não uma interface/Strategy — nenhum segundo
"verificador" real existe hoje que justificasse isso, mesma disciplina
de "não construir framework antes de um segundo consumidor real").

Contrato: `analyze(debate_result, run_config) -> SourceAnalysisResult | None`
-- `None` SÓ quando `run_config.source_text is None` (nenhuma fonte
fornecida: a análise nem existe). Nunca levanta exceção por falha de
análise (transporte/output inválido/budget/zero claims viram estados
explícitos em `SourceAnalysisResult.skipped_reason`); só levanta
`ValueError` se `run_config.source_analyzer_provider` não existir entre
os providers injetados, ANTES de qualquer chamada real (mesmo contrato
de `SingleJudge.judge()`).

Verificação de excerpt: a aplicação NUNCA confia no texto citado pela
LLM como prova por si só -- sempre verifica mecanicamente que o excerpt
é uma substring EXATA do `source_text` original antes de aceitar uma
relação supports/contradicts. Ocorrência duplicada usa a PRIMEIRA
(`str.find`, mais à esquerda) -- regra determinística e documentada.
Excerpt fabricado/não encontrado -> `RejectedSourceEntry`, nunca vira
relação válida.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from app.debate.claims import get_current_claims
from app.debate.result import DebateResult
from app.models.provider_models import ProviderResponse
from app.models.request_provenance import RequestProvenance, build_request_provenance
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.providers.base import LLMProvider, transport_error_common_fields
from app.structured_output import INTERPRETATION_FAILURE_MESSAGE, strip_single_json_code_fence
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.context import SOURCE_ANALYSIS_CONTRACT_VERSION, build_source_analysis_request
from app.source_analysis.errors import MalformedSourceAnalysisOutputError
from app.source_analysis.models import (
    RejectedSourceEntry,
    SourceClaimAnalysisResult,
    ValidSourceRelation,
)
from app.source_analysis.result import SourceAnalysisResult
from app.source_analysis.schemas import SourceAnalysisOutput, SourceRelationDraft

_MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2

_RELATION_DRAFT_ADAPTER: TypeAdapter[SourceRelationDraft] = TypeAdapter(SourceRelationDraft)


class SourceAnalyzer:
    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers

    async def analyze(
        self, debate_result: DebateResult, run_config: RunConfig
    ) -> SourceAnalysisResult | None:
        if run_config.source_text is None:
            return None

        if run_config.source_analyzer_provider not in self._providers:
            raise ValueError(
                f"source_analyzer_provider desconhecido: {run_config.source_analyzer_provider!r}"
            )
        analyzer_llm = self._providers[run_config.source_analyzer_provider]

        budget_exceeded_before = compute_budget_exceeded(
            debate_result.cumulative_input_tokens,
            debate_result.cumulative_output_tokens,
            debate_result.cumulative_cost_usd,
            run_config,
        )

        current_claims = get_current_claims(debate_result.claims)
        if not current_claims:
            return SourceAnalysisResult(
                attempts=[],
                claim_results=[],
                skipped_reason="no_claims_to_analyze",
                source_analyzer_provider=run_config.source_analyzer_provider,
                cumulative_budget_exceeded=budget_exceeded_before,
            )

        if budget_exceeded_before:
            return SourceAnalysisResult(
                attempts=[],
                claim_results=[],
                skipped_reason="budget_exhausted_before_source_analysis",
                source_analyzer_provider=run_config.source_analyzer_provider,
                cumulative_budget_exceeded=True,
            )

        current_claim_ids = {c.id for c in current_claims}
        request = build_source_analysis_request(
            run_config.source_text, current_claims, run_config.max_output_tokens_per_call
        )
        request_provenance = build_request_provenance(SOURCE_ANALYSIS_CONTRACT_VERSION, request)

        attempts: list[SourceAnalysisAttempt] = []
        parsed: SourceAnalysisOutput | None = None

        for attempt_number in range(1, _MAX_STRUCTURED_OUTPUT_ATTEMPTS + 1):
            if attempt_number > 1:
                so_far_input, so_far_output, so_far_cost, _ = sum_usage_and_cost(attempts)
                if compute_budget_exceeded(
                    debate_result.cumulative_input_tokens + so_far_input,
                    debate_result.cumulative_output_tokens + so_far_output,
                    debate_result.cumulative_cost_usd + so_far_cost,
                    run_config,
                ):
                    break  # budget já esgotado -- não inicia o retry
            provider_response = await analyzer_llm.complete(request)

            if provider_response.status == "error":
                attempts.append(
                    _transport_error_attempt(attempt_number, provider_response, request_provenance)
                )
                break  # sem retry desta camada pra erro de transporte

            try:
                parsed = _parse(provider_response.text)
            except MalformedSourceAnalysisOutputError as exc:
                attempts.append(
                    _parse_rejected_attempt(
                        attempt_number, provider_response, str(exc), request_provenance
                    )
                )
                continue
            except Exception:
                # Repair H1 -- a chamada REALMENTE retornou, mas a
                # interpretação levantou algo fora do vocabulário antecipado
                # (ex.: `ValueError` puro de `json.loads` pra um inteiro além do
                # limite de conversão, `RecursionError` pra aninhamento
                # profundo). Registrado truthfully e tratado exatamente como
                # saída malformada (mesmo retry, mesmo fallback
                # `source_analysis_output_invalid`). Ver app/structured_output.py.
                attempts.append(
                    _parse_rejected_attempt(
                        attempt_number,
                        provider_response,
                        INTERPRETATION_FAILURE_MESSAGE,
                        request_provenance,
                        parse_status="interpretation_failed",
                    )
                )
                continue

            attempts.append(_accepted_attempt(attempt_number, provider_response, request_provenance))
            break

        sa_input, sa_output, sa_cost, _ = sum_usage_and_cost(attempts)
        cumulative_budget_exceeded = compute_budget_exceeded(
            debate_result.cumulative_input_tokens + sa_input,
            debate_result.cumulative_output_tokens + sa_output,
            debate_result.cumulative_cost_usd + sa_cost,
            run_config,
        )

        if parsed is None:
            reason: Literal[
                "source_analysis_transport_failed", "source_analysis_output_invalid"
            ] = (
                "source_analysis_transport_failed"
                if attempts[-1].transport_status == "error"
                else "source_analysis_output_invalid"
            )
            return SourceAnalysisResult(
                attempts=attempts,
                claim_results=[],
                skipped_reason=reason,
                source_analyzer_provider=run_config.source_analyzer_provider,
                cumulative_budget_exceeded=cumulative_budget_exceeded,
            )

        claim_results = _build_claim_results(parsed, current_claim_ids, run_config.source_text)

        return SourceAnalysisResult(
            attempts=attempts,
            claim_results=claim_results,
            skipped_reason=None,
            source_analyzer_provider=run_config.source_analyzer_provider,
            cumulative_budget_exceeded=cumulative_budget_exceeded,
        )


def _parse(raw_text: str) -> SourceAnalysisOutput:
    try:
        data = json.loads(strip_single_json_code_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise MalformedSourceAnalysisOutputError(f"JSON inválido: {exc}") from exc
    try:
        return SourceAnalysisOutput.model_validate(data)
    except ValidationError as exc:
        raise MalformedSourceAnalysisOutputError(
            f"JSON não bate com o shape mínimo esperado: {exc}"
        ) from exc


def _build_claim_results(
    parsed: SourceAnalysisOutput, current_claim_ids: set[str], source_text: str
) -> list[SourceClaimAnalysisResult]:
    """Agrupa entradas brutas por claim_id ANTES de validar cada uma
    individualmente -- é isso que permite distinguir omitida (0
    entradas) de duplicada (2+ entradas) de única (exatamente 1),
    conforme o contrato fechado."""
    by_claim_id: dict[str, list[dict[str, Any]]] = {}
    unknown_entries: list[Any] = []

    for raw_entry in parsed.claim_relations:
        claim_id = raw_entry.get("claim_id") if isinstance(raw_entry, dict) else None
        if isinstance(claim_id, str) and claim_id in current_claim_ids:
            by_claim_id.setdefault(claim_id, []).append(raw_entry)
        else:
            unknown_entries.append(raw_entry)

    results: list[SourceClaimAnalysisResult] = []

    for claim_id in current_claim_ids:
        entries = by_claim_id.get(claim_id, [])
        if len(entries) == 0:
            results.append(
                RejectedSourceEntry(claim_id=claim_id, reason="omitted_by_model", raw_entry=None)
            )
        elif len(entries) > 1:
            results.append(
                RejectedSourceEntry(
                    claim_id=claim_id, reason="duplicate_claim_id", raw_entry=entries
                )
            )
        else:
            results.append(_validate_single_entry(claim_id, entries[0], source_text))

    for raw_entry in unknown_entries:
        results.append(RejectedSourceEntry(claim_id=None, reason="invalid_entry", raw_entry=raw_entry))

    return results


def _validate_single_entry(
    claim_id: str, raw_entry: dict[str, Any], source_text: str
) -> SourceClaimAnalysisResult:
    try:
        draft = _RELATION_DRAFT_ADAPTER.validate_python(raw_entry)
    except ValidationError:
        return RejectedSourceEntry(claim_id=claim_id, reason="invalid_entry", raw_entry=raw_entry)

    if draft.relation == "unresolved":
        return ValidSourceRelation(claim_id=claim_id, relation="unresolved")

    if not draft.excerpt:
        return RejectedSourceEntry(claim_id=claim_id, reason="invalid_entry", raw_entry=raw_entry)

    # Verificação mecânica: o excerpt precisa ser substring EXATA do
    # source_text original -- nunca confiado da LLM. Ocorrência
    # duplicada usa a primeira (mais à esquerda), regra determinística.
    start = source_text.find(draft.excerpt)
    if start == -1:
        return RejectedSourceEntry(claim_id=claim_id, reason="invalid_entry", raw_entry=raw_entry)

    end = start + len(draft.excerpt)
    return ValidSourceRelation(
        claim_id=claim_id,
        relation=draft.relation,
        excerpt=draft.excerpt,
        excerpt_start=start,
        excerpt_end=end,
    )


def _transport_error_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    request_provenance: RequestProvenance | None = None,
) -> SourceAnalysisAttempt:
    return SourceAnalysisAttempt(
        attempt_number=attempt_number,
        request_provenance=request_provenance,
        **transport_error_common_fields(provider_response),
    )


def _parse_rejected_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    message: str,
    request_provenance: RequestProvenance | None = None,
    *,
    parse_status: Literal["malformed", "interpretation_failed"] = "malformed",
) -> SourceAnalysisAttempt:
    return SourceAnalysisAttempt(
        attempt_number=attempt_number,
        provider=provider_response.provider,
        requested_model=provider_response.requested_model,
        model=provider_response.model,
        model_identity_source=provider_response.model_identity_source,
        transport_status="success",
        transport_error=None,
        transport_attempts=provider_response.attempts,
        raw_output_text=provider_response.text,
        parse_status=parse_status,
        parse_error_message=message,
        usage=provider_response.usage,
        cost_usd=provider_response.cost_usd,
        pricing_provenance=provider_response.pricing_provenance,
        latency_ms=provider_response.latency_ms,
        had_uncertain_prior_attempts=provider_response.had_uncertain_prior_attempts,
        provider_finish_reason=provider_response.provider_finish_reason,
        request_provenance=request_provenance,
    )


def _accepted_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    request_provenance: RequestProvenance | None = None,
) -> SourceAnalysisAttempt:
    return SourceAnalysisAttempt(
        attempt_number=attempt_number,
        provider=provider_response.provider,
        requested_model=provider_response.requested_model,
        model=provider_response.model,
        model_identity_source=provider_response.model_identity_source,
        transport_status="success",
        transport_error=None,
        transport_attempts=provider_response.attempts,
        raw_output_text=provider_response.text,
        parse_status="accepted",
        usage=provider_response.usage,
        cost_usd=provider_response.cost_usd,
        pricing_provenance=provider_response.pricing_provenance,
        latency_ms=provider_response.latency_ms,
        had_uncertain_prior_attempts=provider_response.had_uncertain_prior_attempts,
        request_provenance=request_provenance,
        provider_finish_reason=provider_response.provider_finish_reason,
    )
