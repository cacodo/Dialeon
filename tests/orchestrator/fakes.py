"""
Fake de LLMProvider para os testes do Orchestrator.

Sobrescreve `complete()` diretamente (não `_call_api`) — o Orchestrator só
depende do contrato `complete(request) -> ProviderResponse`, então testar
contra esse contrato diretamente desacopla os testes do Orchestrator da
lógica de retry/timeout do LLMProvider, que já foi testada isoladamente
na Etapa 2. Isso também permite construir ProviderResponse com valores
precisos (usage, cost_usd, attempts) sem precisar simular uma API real.
"""

from __future__ import annotations

import asyncio
from typing import Callable

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


class StubProvider(LLMProvider):
    """provider_name é setado por instância (não por classe) porque cada
    teste cria vários stubs com nomes diferentes ("openai", "anthropic",
    "slow-provider", etc.) sem precisar de uma subclasse por nome."""

    def __init__(
        self,
        name: str,
        *,
        delay: float = 0.0,
        response: ProviderResponse | None = None,
        raise_exc: BaseException | None = None,
        default_model: str = "fake-model",
    ):
        # pricing vazio — nunca consultado, já que StubProvider sobrescreve
        # complete() diretamente (não passa por LLMProvider._call_api()).
        super().__init__(
            api_key="fake-key", timeout_seconds=9999, max_retries=0, pricing=PricingRegistry({})
        )
        self.provider_name = name
        self._delay = delay
        self._response = response
        self._raise_exc = raise_exc
        self._default_model_name = default_model
        self.call_count = 0
        self.received_requests: list[CompletionRequest] = []

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(self, request: CompletionRequest):
        raise NotImplementedError("StubProvider sobrescreve complete() diretamente")

    async def complete(
        self,
        request: CompletionRequest,
        *,
        execution_policy: TransportAttemptPolicy | None = None,
    ) -> ProviderResponse:
        self.call_count += 1
        self.received_requests.append(request)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._response is not None, "StubProvider precisa de response ou raise_exc"
        return self._response


class MutatingProvider(LLMProvider):
    """Provider ADVERSARIAL -- nunca deveria existir em produção. Muta o
    `CompletionRequest` recebido em `complete()` (campo semântico do
    digest) ANTES de devolver a resposta, provando que a provenance
    registrada precisa ser digerida do estado PRÉ-dispatch (F1, review
    de independência desta slice) -- `CompletionRequest` é mutável e
    nada no contrato de `LLMProvider.complete()` impede um provider real
    de mutar o objeto recebido em runtime."""

    def __init__(
        self,
        name: str,
        *,
        response: ProviderResponse,
        mutate: Callable[[CompletionRequest], None],
        default_model: str = "fake-model",
    ):
        super().__init__(
            api_key="fake-key", timeout_seconds=9999, max_retries=0, pricing=PricingRegistry({})
        )
        self.provider_name = name
        self._response = response
        self._mutate = mutate
        self._default_model_name = default_model
        self.received_requests: list[CompletionRequest] = []

    @property
    def default_model(self) -> str:
        return self._default_model_name

    async def _call_api(self, request: CompletionRequest):
        raise NotImplementedError("MutatingProvider sobrescreve complete() diretamente")

    async def complete(
        self,
        request: CompletionRequest,
        *,
        execution_policy: TransportAttemptPolicy | None = None,
    ) -> ProviderResponse:
        self.received_requests.append(request)
        self._mutate(request)
        return self._response


def success_response(
    provider: str,
    *,
    model: str = "fake-model",
    requested_model: str | None = None,
    model_identity_source: ModelIdentitySource = ModelIdentitySource.PROVIDER_REPORTED,
    text: str = "resposta de teste",
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    cost_usd: float | None = None,
    latency_ms: int = 100,
    attempts: int = 1,
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
    )


def error_response(
    provider: str,
    *,
    model: str = "fake-model",
    requested_model: str | None = None,
    model_identity_source: ModelIdentitySource = ModelIdentitySource.REQUESTED_FALLBACK,
    error_type: ProviderErrorType = ProviderErrorType.API_ERROR,
    message: str = "erro de teste",
    retryable: bool = False,
    latency_ms: int = 50,
    attempts: int = 3,
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
    )
