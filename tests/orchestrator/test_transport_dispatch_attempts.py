"""`ModelResponse.attempts` conta tentativas de transporte que chegaram a
`_call_api()` -- inclusive quando a rodada cancela a task no timeout de
dispatch, antes de `complete()` devolver qualquer `ProviderResponse`.

Provider real (`LLMProvider` com só `_call_api` implementado), então a
contagem vem do `complete()` de produção, nunca de um fake de `complete`.
"""

from __future__ import annotations

import asyncio

import pytest

import app.providers.base as provider_base
from app.models.provider_models import CompletionRequest, Message, TokenUsage
from app.orchestrator.orchestrator import Orchestrator
from app.providers.base import LLMProvider
from app.providers.errors import ProviderTimeoutError
from app.providers.pricing import ModelRate, PricingRegistry

_PRICING = PricingRegistry(
    {("fake", "fake-model"): ModelRate(input_usd_per_million_tokens=1.0, output_usd_per_million_tokens=2.0)},
    source_id="dispatch-attempts-test",
)


class _ScriptedTransport(LLMProvider):
    """Cada chamada de `_call_api` executa o próximo passo do roteiro:
    "ok" responde, "retryable" falha com erro retentável, "hang" nunca volta."""

    provider_name = "fake"

    def __init__(self, script: list[str], *, api_key: str | None = "key", max_retries: int = 2):
        super().__init__(api_key, 30.0, max_retries, _PRICING)
        self._script = list(script)
        self.calls = 0

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def _call_api(self, request):
        step = self._script[self.calls]
        self.calls += 1
        if step == "retryable":
            raise ProviderTimeoutError("fake: falha retentável")
        if step == "hang":
            await asyncio.Event().wait()
        return "resposta", TokenUsage(input_tokens=10, output_tokens=5), "fake-model", "stop"


def _request() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="q")])


async def _round(provider: LLMProvider, timeout: float = 0.3):
    result = await Orchestrator({"fake": provider}).run_round(
        {"fake": _request()}, round_number=1, round_dispatch_timeout_seconds=timeout, contract_version="test"
    )
    [response] = result.responses
    return response


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(provider_base, "_backoff_delay", lambda attempt_number: 0.0)


async def test_a_no_dispatch_records_zero_attempts_and_known_zero_cost():
    provider = _ScriptedTransport(["ok"], api_key=None)

    response = await _round(provider)

    assert provider.calls == 0
    assert (response.status, response.attempts, response.had_uncertain_prior_attempts) == ("error", 0, False)
    assert response.error.type == "auth"
    assert response.cost_usd == 0.0


async def test_b_dispatch_cancelled_by_round_timeout_counts_the_in_flight_attempt():
    provider = _ScriptedTransport(["hang"])

    response = await _round(provider)

    assert provider.calls == 1
    assert (response.status, response.attempts, response.had_uncertain_prior_attempts) == ("error", 1, False)
    assert response.error.type == "timeout"
    # a tentativa em voo nunca devolveu nada: usage/custo desconhecidos
    assert (response.usage, response.cost_usd, response.pricing_provenance) == (None, None, None)


async def test_c_retry_then_round_timeout_counts_both_attempts_and_flags_the_prior_one():
    provider = _ScriptedTransport(["retryable", "hang"])

    response = await _round(provider)

    assert provider.calls == 2
    assert (response.status, response.attempts, response.had_uncertain_prior_attempts) == ("error", 2, True)
    assert (response.usage, response.cost_usd) == (None, None)


async def test_c_retry_that_succeeds_keeps_the_normal_accounting():
    provider = _ScriptedTransport(["retryable", "ok"])

    response = await _round(provider)

    assert (response.status, response.attempts, response.had_uncertain_prior_attempts) == ("success", 2, True)
    assert response.usage == TokenUsage(input_tokens=10, output_tokens=5)


async def test_d_normal_response_is_one_attempt():
    provider = _ScriptedTransport(["ok"])

    response = await _round(provider)

    assert (response.status, response.attempts, response.had_uncertain_prior_attempts) == ("success", 1, False)
    assert response.cost_usd == pytest.approx(10 * 1e-6 + 5 * 2e-6)
