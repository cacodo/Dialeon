"""
Orchestrator — Etapa 4, refatorado na Etapa 5.

`run_round()` é a primitiva pobre de execução paralela: dado um
`CompletionRequest` por provider, executa em paralelo, aplica timeout
global/cancelamento/cleanup, normaliza pra `ModelResponse`, contabiliza
respostas/tokens/custo. NÃO aplica quórum, NÃO aplica budget, NÃO sabe o
que é Claim, crítica ou Debate Engine — é reusada, sem alteração, tanto
pela Fase 1 (`run()`) quanto pela rodada de crítica do Debate Engine.

`run()` continua sendo o wrapper específico da Fase 1: monta a mesma
pergunta pra todos os providers, chama `run_round()`, aplica
`QuorumPolicy` e budget da execução inicial isolada.

Contrato de `Orchestrator.run()` (inalterado desde a Etapa 4):

    RunConfig -> InitialResponsesResult | raise InsufficientQuorumError

O que este contrato NÃO define, deliberadamente: como um chamador
multi-rodada (Debate Engine) deve reagir a essa exceção — isso é decidido
pelo Debate Engine, não aqui (ver app/debate/debate_engine.py).

Responsabilidades que este módulo NÃO tem, de propósito:
- retry (já é do LLMProvider — chama `complete()` uma vez por provider e
  trata o que vier, seja sucesso ou erro definitivo);
- agrupamento de claims, extração, crítica, juiz, verificação;
- persistência;
- interpretar texto de resposta de LLM como instrução — a única fonte de
  decisão aqui é `RunConfig`/os requests recebidos (construídos ANTES de
  qualquer chamada) e o `status` estrutural de cada `ProviderResponse`.
"""

from __future__ import annotations

import asyncio
import time

from app.models.domain import ModelResponse
from app.models.provider_models import (
    CompletionRequest,
    Message,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
)
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.providers.base import LLMProvider

_PHASE_1_ROUND_NUMBER = 1


class Orchestrator:
    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers

    async def run(self, run_config: RunConfig) -> InitialResponsesResult:
        request = CompletionRequest(
            messages=[Message(role="user", content=run_config.question)],
            max_tokens=run_config.max_output_tokens_per_call,
        )
        requests = {name: request for name in run_config.enabled_providers}

        round_result = await self.run_round(
            requests,
            round_number=_PHASE_1_ROUND_NUMBER,
            overall_timeout_seconds=run_config.overall_timeout_seconds,
        )

        return _apply_quorum_and_budget(round_result, run_config)

    async def run_round(
        self,
        requests: dict[str, CompletionRequest],
        round_number: int,
        overall_timeout_seconds: float,
    ) -> RoundResult:
        unknown = set(requests) - set(self._providers)
        if unknown:
            raise ValueError(
                f"requests contém provider(s) desconhecido(s): {sorted(unknown)}"
            )

        provider_responses = await self._execute_all(requests, overall_timeout_seconds)

        model_responses = [
            _to_model_response(response, round_number)
            for response in provider_responses.values()
        ]

        successful_count = sum(1 for r in model_responses if r.status == "success")
        total_input_tokens, total_output_tokens, total_cost_usd, has_unknown_accounting_components = (
            sum_usage_and_cost(model_responses)
        )

        return RoundResult(
            round_number=round_number,
            responses=model_responses,
            successful_count=successful_count,
            total_participants=len(model_responses),
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost_usd=total_cost_usd,
            has_unknown_accounting_components=has_unknown_accounting_components,
        )

    async def _execute_all(
        self,
        requests: dict[str, CompletionRequest],
        overall_timeout_seconds: float,
    ) -> dict[str, ProviderResponse]:
        start = time.monotonic()
        tasks: dict[str, asyncio.Task] = {
            name: asyncio.create_task(self._providers[name].complete(request))
            for name, request in requests.items()
        }

        try:
            await asyncio.wait(tasks.values(), timeout=overall_timeout_seconds)
        finally:
            pending = [t for t in tasks.values() if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        results: dict[str, ProviderResponse] = {}
        for name, task in tasks.items():
            provider = self._providers[name]
            request = requests[name]
            if task.cancelled():
                results[name] = _timeout_response(
                    name, provider, request, overall_timeout_seconds, elapsed_ms
                )
            elif task.exception() is not None:
                results[name] = _unknown_error_response(
                    name, provider, request, task.exception(), elapsed_ms
                )
            else:
                results[name] = task.result()

        return results


def _to_model_response(response: ProviderResponse, round_number: int) -> ModelResponse:
    return ModelResponse(
        provider=response.provider,
        requested_model=response.requested_model,
        model=response.model,
        round_number=round_number,
        status=response.status,
        response_text=response.text,
        usage=response.usage,
        cost_usd=response.cost_usd,
        pricing_provenance=response.pricing_provenance,
        latency_ms=response.latency_ms,
        attempts=response.attempts,
        error=response.error,
        had_uncertain_prior_attempts=response.had_uncertain_prior_attempts,
        provider_finish_reason=response.provider_finish_reason,
    )


def _timeout_response(
    provider_name: str,
    provider: LLMProvider,
    request: CompletionRequest,
    overall_timeout_seconds: float,
    elapsed_ms: int,
) -> ProviderResponse:
    # Etapa 13: mesmo caminho defensivo não previsto no Evidence Pack
    # original — a task foi cancelada pelo timeout GLOBAL da rodada,
    # antes mesmo de LLMProvider.complete() conseguir devolver um
    # ProviderResponse próprio (que resolveria requested_model
    # internamente). Resolvido aqui com a mesma regra
    # (request.model OR provider.default_model), nunca inventado.
    requested_model = request.model or provider.default_model
    return ProviderResponse(
        provider=provider_name,
        requested_model=requested_model,
        model=requested_model,
        status="error",
        text=None,
        usage=None,
        cost_usd=None,
        latency_ms=elapsed_ms,
        attempts=0,
        # Etapa 17A: sem tentativa CONFIRMADA nenhuma nesses dois
        # caminhos defensivos (a task foi cancelada/a exceção escapou
        # de fora de complete() -- não temos como saber se ela chegou a
        # discar) -- False aqui é o default neutro, nunca uma alegação
        # de que sabemos que não houve tentativa anterior real.
        had_uncertain_prior_attempts=False,
        provider_finish_reason=None,  # nenhuma chamada foi observada de verdade
        error=ProviderErrorInfo(
            type=ProviderErrorType.TIMEOUT,
            message=(
                f"{provider_name}: execução cancelada pelo timeout global "
                f"da rodada ({overall_timeout_seconds}s) antes de concluir. "
                "Distinto do timeout por provider (já tratado internamente "
                "por LLMProvider) — esta é a salvaguarda de execução da "
                "rodada inteira."
            ),
            retryable=False,
        ),
    )


def _unknown_error_response(
    provider_name: str,
    provider: LLMProvider,
    request: CompletionRequest,
    exc: BaseException,
    elapsed_ms: int,
) -> ProviderResponse:
    # Etapa 13: mesmo caso do timeout acima — exceção não tratada
    # escapou de complete() antes de um ProviderResponse próprio existir.
    requested_model = request.model or provider.default_model
    return ProviderResponse(
        provider=provider_name,
        requested_model=requested_model,
        model=requested_model,
        status="error",
        text=None,
        usage=None,
        cost_usd=None,
        latency_ms=elapsed_ms,
        attempts=0,
        # Etapa 17A: sem tentativa CONFIRMADA nenhuma nesses dois
        # caminhos defensivos (a task foi cancelada/a exceção escapou
        # de fora de complete() -- não temos como saber se ela chegou a
        # discar) -- False aqui é o default neutro, nunca uma alegação
        # de que sabemos que não houve tentativa anterior real.
        had_uncertain_prior_attempts=False,
        provider_finish_reason=None,  # nenhuma chamada foi observada de verdade
        error=ProviderErrorInfo(
            type=ProviderErrorType.UNKNOWN,
            message=f"{provider_name}: exceção inesperada escapou de complete(): {exc}",
            retryable=False,
        ),
    )


def _apply_quorum_and_budget(
    round_result: RoundResult, run_config: RunConfig
) -> InitialResponsesResult:
    if round_result.successful_count < run_config.quorum.min_to_return:
        raise InsufficientQuorumError(
            round_result.successful_count,
            round_result.total_participants,
            run_config.quorum.min_to_return,
            round_result,
        )

    insufficient_data_for_consensus = (
        round_result.successful_count < run_config.quorum.min_for_debate
    )

    budget_exceeded = compute_budget_exceeded(
        round_result.total_input_tokens,
        round_result.total_output_tokens,
        round_result.total_cost_usd,
        run_config,
    )

    return InitialResponsesResult(
        responses=round_result.responses,
        successful_count=round_result.successful_count,
        total_providers=round_result.total_participants,
        insufficient_data_for_consensus=insufficient_data_for_consensus,
        total_input_tokens=round_result.total_input_tokens,
        total_output_tokens=round_result.total_output_tokens,
        total_cost_usd=round_result.total_cost_usd,
        has_unknown_accounting_components=round_result.has_unknown_accounting_components,
        budget_exceeded=budget_exceeded,
    )
