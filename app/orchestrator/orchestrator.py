"""
Orchestrator — Etapa 4, refatorado na Etapa 5.

`run_round()` é a primitiva pobre de execução paralela: dado um
`CompletionRequest` por provider, executa em paralelo, aplica timeout
de dispatch da rodada/cancelamento/cleanup, normaliza pra `ModelResponse`, contabiliza
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
    ModelIdentitySource,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
)
from app.models.request_provenance import RequestProvenance, build_request_provenance
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import (
    RunConfig,
    validate_execution_limits_for_new_execution,
    validate_quorum_feasibility,
)
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.providers.base import LLMProvider

_PHASE_1_ROUND_NUMBER = 1

# Provider-Neutral Request Provenance V1 -- contrato da resposta inicial
# do Council (Fase 1). Dono natural: é aqui, dentro de `Orchestrator.run()`,
# que o CompletionRequest da resposta inicial é montado inline -- sem
# builder próprio em outro módulo que justificasse morar em outro lugar
# (ver seção 6/10 do contrato desta slice).
INITIAL_RESPONSE_CONTRACT_VERSION = "initial_response_v1"


def _build_initial_request(question: str, max_output_tokens_per_call: int) -> CompletionRequest:
    """Único ponto de construção do `CompletionRequest` da resposta
    inicial (Fase 1) -- extraído de dentro de `Orchestrator.run()` (F2,
    review de independência) só pra que o teste de golden digest
    (`tests/models/test_request_provenance_contracts.py`) traverse a
    MESMA construção de produção, em vez de duplicar os campos à mão.
    Semântica IDÊNTICA à construção inline anterior -- nenhuma mudança
    de prompt/mensagem/model/cap/temperature, portanto nenhum bump de
    `INITIAL_RESPONSE_CONTRACT_VERSION`."""
    return CompletionRequest(
        messages=[Message(role="user", content=question)],
        max_tokens=max_output_tokens_per_call,
    )


class Orchestrator:
    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers

    async def run(self, run_config: RunConfig) -> InitialResponsesResult:
        """Accepted Quorum Feasibility Boundary V1: `validate_quorum_feasibility`
        roda ANTES de qualquer construção de request/provenance/dispatch
        -- defesa em profundidade pra chamadores diretos do
        `Orchestrator` (testes, código interno) que não passaram por
        `CouncilExecutionService`/`CouncilRunner`. `ValueError` propaga
        sem interceptação -- mesma disciplina de `validate_question` em
        `CouncilRunner.run()`; a comparação em si nunca é reimplementada
        aqui, só chamada.

        Finite RunConfig New-Execution Boundary V1:
        `validate_execution_limits_for_new_execution` roda logo em
        seguida, AINDA antes de qualquer construção de
        request/provenance/dispatch -- mesma defesa em profundidade:
        `run_config.round_dispatch_timeout_seconds` é passado direto
        pra `run_round`/`_execute_all` abaixo (`asyncio.wait`) -- um
        valor `+inf` nunca poderia ser rejeitado por aquele
        `asyncio.wait` (que trataria `+inf` como "nunca expira", nunca
        como erro), então esta é a ÚNICA boundary que impede uma
        chamada direta ao `Orchestrator` de despachar dispatch real de
        provider sob um timeout historicamente permissivo. `ValueError`
        propaga sem interceptação -- `Orchestrator` nunca reimplementa
        a checagem, só chama a MESMA função canônica."""
        validate_quorum_feasibility(run_config)
        validate_execution_limits_for_new_execution(run_config)

        request = _build_initial_request(
            run_config.question, run_config.max_output_tokens_per_call
        )
        requests = {name: request for name in run_config.enabled_providers}

        round_result = await self.run_round(
            requests,
            round_number=_PHASE_1_ROUND_NUMBER,
            round_dispatch_timeout_seconds=run_config.round_dispatch_timeout_seconds,
            contract_version=INITIAL_RESPONSE_CONTRACT_VERSION,
        )

        return _apply_quorum_and_budget(round_result, run_config)

    async def run_round(
        self,
        requests: dict[str, CompletionRequest],
        round_number: int,
        round_dispatch_timeout_seconds: float,
        contract_version: str,
    ) -> RoundResult:
        """`contract_version` (Provider-Neutral Request Provenance V1):
        rótulo de contrato da OPERAÇÃO que está chamando esta rodada
        genérica -- `run_round` não sabe (nem precisa saber) o que a
        operação significa semanticamente, só que toda `ModelResponse`
        produzida aqui precisa carregar provenance computada do
        `CompletionRequest` REAL associado a ela (`requests[name]`,
        nunca reconstruído). Único parâmetro que distingue a resposta
        inicial (`INITIAL_RESPONSE_CONTRACT_VERSION`) da crítica
        (`CRITIQUE_CONTRACT_VERSION`, ver app/debate/context.py) --
        ambas compartilham este mesmo mecanismo de dispatch."""
        unknown = set(requests) - set(self._providers)
        if unknown:
            raise ValueError(
                f"requests contém provider(s) desconhecido(s): {sorted(unknown)}"
            )

        # Provider-Neutral Request Provenance V1 (F1, review de
        # independência -- REPARO) -- `CompletionRequest` é mutável, então a
        # provenance precisa ser digerida do estado PRÉ-dispatch, ANTES de
        # `_execute_all` entregar o objeto a `LLMProvider.complete()`. Um
        # provider que mutar o request recebido (nunca deveria, mas nada
        # aqui impede em runtime) não pode retroativamente alterar a
        # provenance já registrada -- o dict abaixo é o snapshot imutável
        # que sobrevive ao dispatch, calculado uma vez por
        # participante/request associado (mesmo objeto compartilhado entre
        # participantes -> mesmo valor, por construção determinística de
        # `compute_request_digest`).
        request_provenances = {
            name: build_request_provenance(contract_version, request)
            for name, request in requests.items()
        }

        provider_responses = await self._execute_all(requests, round_dispatch_timeout_seconds)

        model_responses = [
            _to_model_response(response, round_number, request_provenances[name])
            for name, response in provider_responses.items()
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
        round_dispatch_timeout_seconds: float,
    ) -> dict[str, ProviderResponse]:
        start = time.monotonic()
        tasks: dict[str, asyncio.Task] = {
            name: asyncio.create_task(self._providers[name].complete(request))
            for name, request in requests.items()
        }

        try:
            await asyncio.wait(tasks.values(), timeout=round_dispatch_timeout_seconds)
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
                    name, provider, request, round_dispatch_timeout_seconds, elapsed_ms
                )
            elif task.exception() is not None:
                results[name] = _unknown_error_response(
                    name, provider, request, task.exception(), elapsed_ms
                )
            else:
                results[name] = task.result()

        return results


def _to_model_response(
    response: ProviderResponse, round_number: int, request_provenance: RequestProvenance
) -> ModelResponse:
    return ModelResponse(
        provider=response.provider,
        requested_model=response.requested_model,
        model=response.model,
        model_identity_source=response.model_identity_source,
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
        request_provenance=request_provenance,
    )


def _timeout_response(
    provider_name: str,
    provider: LLMProvider,
    request: CompletionRequest,
    round_dispatch_timeout_seconds: float,
    elapsed_ms: int,
) -> ProviderResponse:
    # Etapa 13: mesmo caminho defensivo não previsto no Evidence Pack
    # original — a task foi cancelada pelo timeout de DISPATCH da rodada,
    # antes mesmo de LLMProvider.complete() conseguir devolver um
    # ProviderResponse próprio (que resolveria requested_model
    # internamente). Resolvido aqui com a mesma regra
    # (request.model OR provider.default_model), nunca inventado.
    requested_model = request.model or provider.default_model
    return ProviderResponse(
        provider=provider_name,
        requested_model=requested_model,
        model=requested_model,
        # Mesmo caso pré-request de LLMProvider.complete() (base.py):
        # nenhum ProviderResponse próprio chegou a existir, então nenhuma
        # identidade pôde ter sido observada -- sempre fallback.
        model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
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
                f"{provider_name}: execução cancelada pelo timeout de dispatch "
                f"da rodada ({round_dispatch_timeout_seconds}s) antes de concluir. "
                "Distinto do timeout por provider (já tratado internamente "
                "por LLMProvider) — esta é a salvaguarda de execução da "
                "rodada inteira (não da execução do Council inteira)."
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
        # Mesma razão de _timeout_response acima -- nenhuma identidade
        # pôde ter sido observada.
        model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
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
