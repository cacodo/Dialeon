"""
`SingleJudge` — único modelo julga por execução (Etapa 6).

Contrato: `judge(debate_result, run_config) -> JudgeResult` — nunca levanta
exceção por falha de julgamento (transporte/output inválido/budget/claims
vazias viram estados explícitos em `JudgeResult.verdict_unavailable_reason`);
só levanta `ValueError` se `run_config.judge_provider` não existir entre os
providers injetados, ANTES de qualquer chamada real.

Retry: `_MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2` (1 inicial + 1 retry) — LOCAL a
este módulo, não importado do claim processor (camadas independentes, mesmo
número por coincidência de política, não por acoplamento). Erro de
TRANSPORTE não é retentado aqui — o `LLMProvider` já esgotou o retry dele
antes de devolver `status="error"`.

Etapa 17A.2 — truncamento CONHECIDO (provider_finish_reason confirmado,
nunca inferido de JSON malformado sozinho) também cancela o retry: o
`CompletionRequest` desta camada é montado UMA vez, idêntico em toda
tentativa, então repeti-lo não corrige um output que já estourou
`max_output_tokens_judge`. Vira `verdict_unavailable_reason=
"judge_output_truncated"`, distinto de "judge_output_invalid" (output
genuinamente incoerente, causa desconhecida) e "judge_transport_failed"
(falha de transporte sem relação com truncamento).
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import ValidationError

from app.debate.claims import get_current_claims
from app.debate.result import DebateResult
from app.judge.attempt import JudgeAttempt
from app.judge.context import build_judge_request, get_participating_providers
from app.judge.errors import InconsistentJudgeReferenceError, MalformedJudgeOutputError
from app.judge.result import JudgeResult
from app.judge.schemas import JudgeOutput
from app.judge.strategy import JudgeStrategy
from app.models.domain import ClaimAssessment, JudgeVerdict
from app.models.provider_models import ProviderResponse
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.providers.base import (
    LLMProvider,
    is_known_output_truncation,
    transport_error_common_fields,
)
from app.structured_output import strip_single_json_code_fence

_MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2


class SingleJudge(JudgeStrategy):
    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers

    async def judge(
        self,
        debate_result: DebateResult,
        run_config: RunConfig,
        *,
        prior_input_tokens: int,
        prior_output_tokens: int,
        prior_cost_usd: float,
    ) -> JudgeResult:
        if run_config.judge_provider not in self._providers:
            raise ValueError(f"judge_provider desconhecido: {run_config.judge_provider!r}")
        judge_llm = self._providers[run_config.judge_provider]

        # Calculado uma vez, ANTES de qualquer early return — usado tanto
        # no branch de "sem claims" quanto no de "budget esgotado", pra
        # cumulative_budget_exceeded nunca ficar hardcoded (correção
        # pós-fechamento arquitetural: o motivo de indisponibilidade e o
        # estado de budget são fatos independentes).
        #
        # `prior_*` (patch de revisão do Stage 16): já inclui QUALQUER
        # fase anterior real (Debate + Source Analysis, se houve) --
        # nunca só `debate_result.cumulative_*` sozinho, que ignoraria
        # silenciosamente o custo de uma Source Analysis que já rodou.
        # SingleJudge nunca soube e continua sem saber que Source
        # Analysis existe -- só recebe os 3 números.
        budget_exceeded_before_judge = compute_budget_exceeded(
            prior_input_tokens,
            prior_output_tokens,
            prior_cost_usd,
            run_config,
        )

        current_claims = get_current_claims(debate_result.claims)
        if not current_claims:
            return JudgeResult(
                verdict=None,
                attempts=[],
                verdict_unavailable_reason="no_claims_to_judge",
                judge_provider=run_config.judge_provider,
                cumulative_budget_exceeded=budget_exceeded_before_judge,
            )

        if budget_exceeded_before_judge:
            return JudgeResult(
                verdict=None,
                attempts=[],
                verdict_unavailable_reason="budget_exhausted_before_judge",
                judge_provider=run_config.judge_provider,
                cumulative_budget_exceeded=True,
            )

        current_claim_ids = {c.id for c in current_claims}
        participating_providers = get_participating_providers(debate_result)
        request = build_judge_request(
            question=run_config.question,
            debate_result=debate_result,
            current_claims=current_claims,
            # Etapa 17A.2 -- teto PRÓPRIO do Judge (não o
            # `max_output_tokens_per_call` geral): o schema exige uma
            # avaliação por claim atual, então o output mínimo exigido
            # cresce com a contagem de claims do debate inteiro.
            max_output_tokens_per_call=run_config.max_output_tokens_judge,
        )

        attempts: list[JudgeAttempt] = []
        parsed: JudgeOutput | None = None
        accepted_response: ProviderResponse | None = None

        for attempt_number in range(1, _MAX_STRUCTURED_OUTPUT_ATTEMPTS + 1):
            if attempt_number > 1:
                so_far_input, so_far_output, so_far_cost, _ = sum_usage_and_cost(attempts)
                if compute_budget_exceeded(
                    prior_input_tokens + so_far_input,
                    prior_output_tokens + so_far_output,
                    prior_cost_usd + so_far_cost,
                    run_config,
                ):
                    break  # budget já esgotado -- não inicia o retry
            provider_response = await judge_llm.complete(request)

            if provider_response.status == "error":
                attempts.append(_transport_error_attempt(attempt_number, provider_response))
                break  # sem retry desta camada pra erro de transporte

            try:
                parsed = _parse_and_validate(
                    provider_response.text, current_claim_ids, participating_providers
                )
            except MalformedJudgeOutputError as exc:
                attempts.append(
                    _parse_rejected_attempt(
                        attempt_number, provider_response, "malformed", str(exc)
                    )
                )
                if is_known_output_truncation(provider_response.provider_finish_reason):
                    break  # truncamento confirmado -- retry idêntico não corrige isso
                continue
            except InconsistentJudgeReferenceError as exc:
                attempts.append(
                    _parse_rejected_attempt(
                        attempt_number, provider_response, "inconsistent_references", str(exc)
                    )
                )
                if is_known_output_truncation(provider_response.provider_finish_reason):
                    break
                continue

            attempts.append(_accepted_attempt(attempt_number, provider_response))
            accepted_response = provider_response
            break

        judge_input, judge_output, judge_cost, _judge_has_unknown = sum_usage_and_cost(attempts)
        cumulative_budget_exceeded = compute_budget_exceeded(
            prior_input_tokens + judge_input,
            prior_output_tokens + judge_output,
            prior_cost_usd + judge_cost,
            run_config,
        )

        if parsed is None or accepted_response is None:
            last_attempt = attempts[-1]
            # Etapa 17A.2 -- truncamento CONHECIDO (provider_finish_reason
            # confirmado, nunca inferido de malformação sozinha) tem
            # prioridade sobre a distinção transporte/parse genérica: é
            # uma causa ESPECÍFICA e conhecida, seja qual for a forma que
            # a falha tomou (sem texto nenhum = erro de transporte; JSON
            # truncado = malformado).
            reason: Literal[
                "judge_transport_failed", "judge_output_invalid", "judge_output_truncated"
            ]
            if is_known_output_truncation(last_attempt.provider_finish_reason):
                reason = "judge_output_truncated"
            elif last_attempt.transport_status == "error":
                reason = "judge_transport_failed"
            else:
                reason = "judge_output_invalid"
            return JudgeResult(
                verdict=None,
                attempts=attempts,
                verdict_unavailable_reason=reason,
                judge_provider=run_config.judge_provider,
                cumulative_budget_exceeded=cumulative_budget_exceeded,
            )

        # evaluated_through_round: 2 se uma rodada de crítica OCORREU
        # (existe CritiqueResult), mesmo com 0/N sucesso — "ocorreu" é
        # sobre CritiqueResult existir, não sobre successful_count>0.
        evaluated_through_round = 2 if debate_result.critique_round is not None else 1

        verdict = JudgeVerdict(
            evaluated_through_round=evaluated_through_round,
            # modelo REALMENTE retornado pela tentativa aceita, nunca
            # presumido a partir de config — se a 1a tentativa falhou e a
            # 2a foi aceita, judge_model reflete a 2a (accepted_response).
            judge_model=accepted_response.model,
            judge_model_identity_source=accepted_response.model_identity_source,
            claim_assessments=[
                ClaimAssessment(
                    claim_id=draft.claim_id,
                    verdict=draft.verdict,
                    explanation=draft.explanation,
                )
                for draft in parsed.claim_assessments
            ],
            best_arguments_by=parsed.best_arguments_by,
            debate_limitations=parsed.debate_limitations,
            confidence=parsed.confidence,
            reasoning=parsed.reasoning,
        )

        return JudgeResult(
            verdict=verdict,
            attempts=attempts,
            verdict_unavailable_reason=None,
            judge_provider=run_config.judge_provider,
            cumulative_budget_exceeded=cumulative_budget_exceeded,
        )


def _parse_and_validate(
    raw_text: str, current_claim_ids: set[str], participating_providers: set[str]
) -> JudgeOutput:
    try:
        data = json.loads(strip_single_json_code_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise MalformedJudgeOutputError(f"JSON inválido: {exc}") from exc
    try:
        parsed = JudgeOutput.model_validate(data)
    except ValidationError as exc:
        raise MalformedJudgeOutputError(f"JSON não bate com o schema esperado: {exc}") from exc

    assessment_ids = [a.claim_id for a in parsed.claim_assessments]
    if len(set(assessment_ids)) != len(assessment_ids):
        raise InconsistentJudgeReferenceError("claim_assessments contém claim_id duplicado")

    assessment_id_set = set(assessment_ids)
    unknown = assessment_id_set - current_claim_ids
    if unknown:
        raise InconsistentJudgeReferenceError(
            f"claim_assessments referencia claim_id(s) desconhecido(s): {sorted(unknown)}"
        )

    missing = current_claim_ids - assessment_id_set
    if missing:
        raise InconsistentJudgeReferenceError(
            "claim_assessments não avaliou todas as claims atuais — "
            f"faltando: {sorted(missing)}"
        )

    unknown_providers = set(parsed.best_arguments_by) - participating_providers
    if unknown_providers:
        raise InconsistentJudgeReferenceError(
            "best_arguments_by referencia provider(s) que não participaram "
            f"do debate: {sorted(unknown_providers)}"
        )

    return parsed


def _transport_error_attempt(
    attempt_number: int, provider_response: ProviderResponse
) -> JudgeAttempt:
    return JudgeAttempt(
        attempt_number=attempt_number, **transport_error_common_fields(provider_response)
    )


def _parse_rejected_attempt(
    attempt_number: int,
    provider_response: ProviderResponse,
    parse_status: Literal["malformed", "inconsistent_references"],
    message: str,
) -> JudgeAttempt:
    return JudgeAttempt(
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
    )


def _accepted_attempt(attempt_number: int, provider_response: ProviderResponse) -> JudgeAttempt:
    return JudgeAttempt(
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
        parse_error_message=None,
        usage=provider_response.usage,
        cost_usd=provider_response.cost_usd,
        pricing_provenance=provider_response.pricing_provenance,
        latency_ms=provider_response.latency_ms,
        had_uncertain_prior_attempts=provider_response.had_uncertain_prior_attempts,
        provider_finish_reason=provider_response.provider_finish_reason,
    )
