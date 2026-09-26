"""
`DirectExecutionService` -- Direct Answer Execution V1.

Fluxo (mesma disciplina de aceite durável do `CouncilExecutionService`,
nunca o pipeline do Conselho):

    caller
      -> valida a pergunta (regra canônica, `validate_question`)
      -> valida o provider contra o registry REAL construído
         (`UnknownProviderError`)
      -> pré-requisitos locais: `missing` rejeita ANTES do aceite
         (`LocalPrerequisitesMissingError`) -- a chamada nunca sairia do
         processo; `met`/`unknown` seguem (nenhuma verificação remota)
      -> congela a autoridade aceita: `DirectRunConfig` com o modelo padrão
         CONFIGURADO do provider construído (`LLMProvider.default_model`),
         nunca vindo do cliente
      -> constrói o `CompletionRequest` direto (modelo explícito) e sua
         `RequestProvenance`
      -> minta run_id/started_at e persiste o aceite (`run_kind="direct"`)
      -> UMA completion lógica: `LLMProvider.complete()` (retry/timeout de
         transporte do provider inalterados)
      -> grava o desfecho terminal (`completed`, ou `failed` por erro do
         provider, com o registro da chamada) na mesma transação que apaga o
         aceite

Nunca chama extração de afirmações, análise de fonte, crítica, Judge,
reconciliação, Editor nem realização linguística; nunca troca de provider
ou de modelo; nunca cai no Conselho; nunca repete a run inteira.

Exceção inesperada durante a chamada: aceite vira `failed`
(`failure_stage="execution"`, informação sanitizada) e a exceção ORIGINAL é
relançada. Falha ao gravar o desfecho: uma tentativa de marcar o aceite como
`failed` (`"terminal_persistence"`) e a exceção original é relançada. Se o
processo morrer depois do aceite, a run fica `running` -- honestamente sem
desfecho confirmado (nada é fabricado nem retomado).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from app.application.errors import (
    InvalidQuestionError,
    LocalPrerequisitesMissingError,
    UnknownProviderError,
)
from app.direct.models import (
    DIRECT_ANSWER_CONTRACT_VERSION,
    DirectRunConfig,
    DirectRunResult,
    build_direct_request,
)
from app.models.domain import ModelResponse
from app.models.provider_models import (
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderExecutionPolicy,
    ProviderResponse,
    validate_provider_execution_policy_for_new_execution,
)
from app.models.request_provenance import RequestProvenance, build_request_provenance
from app.orchestrator.config import validate_question
from app.providers.base import LLMProvider
from app.storage.repository import CouncilRepository

_UNEXPECTED_FAILURE_MESSAGE = "Erro interno inesperado durante a execução."

_TERMINAL_PERSISTENCE_FAILURE_MESSAGE = (
    "Falha ao persistir o resultado terminal da execução; o histórico "
    "detalhado da execução não foi preservado."
)

# Uma run direta tem uma única chamada, sempre registrada como rodada 1.
_DIRECT_ROUND_NUMBER = 1

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


def _direct_model_response(
    response: ProviderResponse, request_provenance: RequestProvenance
) -> ModelResponse:
    """A resposta do provider como registro de auditoria -- os mesmos campos
    que o Conselho grava pra uma resposta de participante (modelo
    solicitado x reportado e sua fonte, uso, custo estimado, precificação,
    tentativas, incerteza, erro), sem reinterpretar nenhum.

    Única exceção: um "sucesso" sem texto utilizável (vazio ou só espaços)
    nunca vira resposta -- é registrado como `malformed_response` (mesma
    classificação que os adapters já dão a uma resposta sem texto), com uso,
    custo e identidade da chamada preservados."""
    status = response.status
    text = response.text
    error = response.error
    if status == "success" and (text is None or text.strip() == ""):
        status, text = "error", None
        error = ProviderErrorInfo(
            type=ProviderErrorType.MALFORMED_RESPONSE,
            message="o provider devolveu uma resposta sem texto",
            retryable=False,
        )
    return ModelResponse(
        provider=response.provider,
        requested_model=response.requested_model,
        model=response.model,
        model_identity_source=response.model_identity_source,
        round_number=_DIRECT_ROUND_NUMBER,
        status=status,
        response_text=text,
        usage=response.usage,
        cost_usd=response.cost_usd,
        pricing_provenance=response.pricing_provenance,
        latency_ms=response.latency_ms,
        attempts=response.attempts,
        error=error,
        had_uncertain_prior_attempts=response.had_uncertain_prior_attempts,
        provider_finish_reason=response.provider_finish_reason,
        request_provenance=request_provenance,
    )


class DirectExecutionService:
    """Executa UMA run direta e persiste o desfecho. `providers` é o registry
    REAL construído pelo deployment (a mesma instância de
    `AppComponents.providers`); `provider_execution_policy` é a mesma
    política resolvida injetada nos providers, snapshotada no aceite."""

    def __init__(
        self,
        repository: CouncilRepository,
        providers: dict[str, LLMProvider],
        provider_execution_policy: ProviderExecutionPolicy,
    ):
        validate_provider_execution_policy_for_new_execution(provider_execution_policy)
        self._repository = repository
        self._providers = providers
        self._provider_execution_policy = provider_execution_policy

    async def run(self, *, question: str, provider: str, max_output_tokens: int) -> DirectRunResult:
        try:
            validate_question(question)
        except ValueError as exc:
            raise InvalidQuestionError(str(exc)) from exc

        llm = self._providers.get(provider)
        if llm is None:
            raise UnknownProviderError(
                unknown_providers=[provider], known_providers=sorted(self._providers)
            )
        if llm.local_prerequisite_state() == "missing":
            raise LocalPrerequisitesMissingError(provider)

        config = DirectRunConfig(
            question=question,
            provider=provider,
            requested_model=llm.default_model,
            max_output_tokens=max_output_tokens,
        )
        request = build_direct_request(config)
        request_provenance = build_request_provenance(DIRECT_ANSWER_CONTRACT_VERSION, request)

        run_id = _new_id()
        started_at = _now()
        await self._repository.save_direct_accepted(
            run_id,
            config=config,
            started_at=started_at,
            provider_execution_policy=self._provider_execution_policy,
        )

        try:
            provider_response = await llm.complete(request)
            result = DirectRunResult(
                id=run_id,
                started_at=started_at,
                ended_at=_now(),
                config=config,
                response=_direct_model_response(provider_response, request_provenance),
                provider_execution_policy=self._provider_execution_policy,
            )
        except Exception as exc:
            await self._repository.save_unexpected_failure(
                run_id,
                failed_at=_now(),
                failure_classification=type(exc).__name__,
                failure_message=_UNEXPECTED_FAILURE_MESSAGE,
                failure_stage="execution",
            )
            raise
        try:
            await self._repository.save_direct_result(result)
        except Exception as exc:
            await self._record_terminal_persistence_failure(run_id, exc)
            raise
        return result

    async def _record_terminal_persistence_failure(self, run_id: str, exc: Exception) -> None:
        """Mesma disciplina de `CouncilExecutionService`: uma tentativa de
        deixar o aceite `failed` ("terminal_persistence"), nunca levanta,
        nunca formata `exc`."""
        try:
            await self._repository.save_unexpected_failure(
                run_id,
                failed_at=_now(),
                failure_classification=type(exc).__name__,
                failure_message=_TERMINAL_PERSISTENCE_FAILURE_MESSAGE,
                failure_stage="terminal_persistence",
            )
        except Exception as fallback_exc:
            logger.error(
                "Persistência terminal da run direta %s falhou (%s) e o registro do "
                "estado failed também falhou (%s); o registro de aceite "
                "permanece 'running'.",
                run_id,
                type(exc).__name__,
                type(fallback_exc).__name__,
            )
            exc.add_note(
                f"Dialeon: a persistência terminal da run {run_id} falhou e o "
                "estado failed não pôde ser registrado; o registro de aceite "
                "permanece 'running'."
            )
            return
        logger.error(
            "Persistência terminal da run direta %s falhou (%s); registrada como "
            "failed (failure_stage=terminal_persistence).",
            run_id,
            type(exc).__name__,
        )
        exc.add_note(
            f"Dialeon: a persistência terminal da run {run_id} falhou; registrada "
            "como failed (failure_stage=terminal_persistence)."
        )
