from __future__ import annotations

from app.models.provider_models import (
    CompletionRequest,
    ModelIdentitySource,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
    TokenUsage,
    TransportAttemptPolicy,
)
from app.providers.base import LLMProvider
from app.providers.pricing import PricingRegistry


class ScriptedProvider(LLMProvider):
    """Provider fake que devolve uma sequência pré-definida de
    ProviderResponse, um por chamada de complete(), na ordem dada — usado
    pra testar extract_claims/group_claims/DebateEngine de forma
    determinística (inclusive sequências com retry: 1a resposta malformada,
    2a aceita)."""

    def __init__(
        self,
        name: str,
        responses: list[ProviderResponse],
        default_model: str = "fake-model",
    ):
        super().__init__(
            api_key="fake-key", timeout_seconds=9999, max_retries=0, pricing=PricingRegistry({})
        )
        self.provider_name = name
        self._responses = list(responses)
        self._default_model_name = default_model
        self.received_requests: list[CompletionRequest] = []
        self.received_execution_policies: list[TransportAttemptPolicy | None] = []

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(self, request: CompletionRequest):
        raise NotImplementedError("ScriptedProvider sobrescreve complete() diretamente")

    async def complete(
        self,
        request: CompletionRequest,
        *,
        execution_policy: TransportAttemptPolicy | None = None,
    ) -> ProviderResponse:
        self.received_requests.append(request)
        self.received_execution_policies.append(execution_policy)
        assert self._responses, "ScriptedProvider esgotou as respostas roteirizadas"
        return self._responses.pop(0)


class CallableProvider(LLMProvider):
    """Provider fake cujo complete() delega pra um handler assíncrono
    fornecido pelo teste — necessário quando a resposta certa depende do
    CONTEÚDO da requisição (ex.: o agrupamento recebe ids de claim gerados
    em runtime, que um ScriptedProvider de fila fixa não consegue prever).

    O handler recebe (call_index, request) e devolve o ProviderResponse."""

    def __init__(self, name: str, handler, default_model: str = "fake-model"):
        super().__init__(
            api_key="fake-key", timeout_seconds=9999, max_retries=0, pricing=PricingRegistry({})
        )
        self.provider_name = name
        self._handler = handler
        self._default_model_name = default_model
        self.received_requests: list[CompletionRequest] = []
        self.received_execution_policies: list[TransportAttemptPolicy | None] = []
        self.call_count = 0

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(self, request: CompletionRequest):
        raise NotImplementedError("CallableProvider sobrescreve complete() diretamente")

    async def complete(
        self,
        request: CompletionRequest,
        *,
        execution_policy: TransportAttemptPolicy | None = None,
    ) -> ProviderResponse:
        self.received_requests.append(request)
        self.received_execution_policies.append(execution_policy)
        self.call_count += 1
        return await self._handler(self.call_count, request)


def text_response(
    provider: str,
    text: str,
    *,
    model: str = "fake-model",
    requested_model: str | None = None,
    model_identity_source: ModelIdentitySource = ModelIdentitySource.PROVIDER_REPORTED,
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    cost_usd: float | None = None,
    latency_ms: int = 50,
    attempts: int = 1,
    provider_finish_reason: str | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model=requested_model if requested_model is not None else model,
        model=model,
        model_identity_source=model_identity_source,
        status="success",
        text=text,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        attempts=attempts,
        provider_finish_reason=provider_finish_reason,
    )


def transport_error_response(
    provider: str,
    *,
    model: str = "fake-model",
    requested_model: str | None = None,
    model_identity_source: ModelIdentitySource = ModelIdentitySource.REQUESTED_FALLBACK,
    error_type: ProviderErrorType = ProviderErrorType.API_ERROR,
    message: str = "erro de transporte simulado",
    retryable: bool = False,
    latency_ms: int = 30,
    attempts: int = 2,
    provider_finish_reason: str | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model=requested_model if requested_model is not None else model,
        model=model,
        model_identity_source=model_identity_source,
        status="error",
        text=None,
        usage=None,
        cost_usd=None,
        latency_ms=latency_ms,
        attempts=attempts,
        error=ProviderErrorInfo(type=error_type, message=message, retryable=retryable),
        provider_finish_reason=provider_finish_reason,
    )
