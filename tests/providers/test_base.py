"""
Testes da lógica compartilhada em app/providers/base.py, usando um provider
falso que não faz nenhuma chamada de rede — só simula sucesso/erro conforme
programado, para exercitar o loop de retry isoladamente.
"""

from __future__ import annotations

import pytest

from app.models.provider_models import CompletionRequest, Message, TokenUsage
from app.providers.base import LLMProvider, is_known_output_truncation
from app.providers.errors import ProviderAPIError, ProviderAuthError, ProviderRateLimitError
from app.providers.pricing import ModelRate, PricingRegistry


class _ScriptedProvider(LLMProvider):
    """Provider de teste: cada chamada consome um item de `script`."""

    provider_name = "scripted"

    def __init__(self, script: list, pricing: PricingRegistry | None = None, **kwargs):
        super().__init__(api_key="fake-key", pricing=pricing or PricingRegistry({}), **kwargs)
        self._script = list(script)
        self.call_count = 0

    @property
    def default_model(self) -> str:
        return "scripted-model"

    async def _call_api(self, request: CompletionRequest):
        self.call_count += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        # Etapa 17A.1: _call_api agora retorna 4-tupla (+ finish_reason).
        # Scripts de teste existentes continuam com 3-tupla -- completados
        # aqui com finish_reason=None, sem precisar reescrever cada teste.
        if len(item) == 3:
            return (*item, None)
        return item


def _request(model: str | None = None) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="oi")], model=model)


@pytest.mark.asyncio
async def test_success_on_first_attempt():
    provider = _ScriptedProvider(
        script=[("resposta", TokenUsage(input_tokens=1, output_tokens=1), "scripted-model")],
        timeout_seconds=5,
        max_retries=2,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.text == "resposta"
    assert result.attempts == 1
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_retries_on_retryable_error_then_succeeds():
    provider = _ScriptedProvider(
        script=[
            ProviderRateLimitError("rate limited"),
            ("resposta ok", TokenUsage(), "scripted-model"),
        ],
        timeout_seconds=5,
        max_retries=2,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.attempts == 2
    assert provider.call_count == 2
    # Etapa 17A (B3): a tentativa 1 falhou DEPOIS de discar de verdade
    # (accounting dela é desconhecida) -- mesmo a tentativa 2 tendo
    # sucesso com custo/uso CONHECIDOS, o flag preserva a incerteza da
    # tentativa anterior.
    assert result.had_uncertain_prior_attempts is True


@pytest.mark.asyncio
async def test_exhausts_retries_and_returns_error():
    provider = _ScriptedProvider(
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
            ProviderAPIError("erro 500 mais uma vez", retryable=True),
        ],
        timeout_seconds=5,
        max_retries=2,
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "api_error"
    assert result.error.retryable is True
    # 1 tentativa inicial + 2 retries = 3 chamadas, batendo o script inteiro
    assert provider.call_count == 3
    # Etapa 17A (B3): as 2 primeiras tentativas já eram incertas antes
    # da última também falhar.
    assert result.had_uncertain_prior_attempts is True


@pytest.mark.asyncio
async def test_non_retryable_error_does_not_retry():
    provider = _ScriptedProvider(
        script=[ProviderAuthError("chave inválida")],
        timeout_seconds=5,
        max_retries=3,
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "auth"
    assert result.error.retryable is False
    # erro não-retryable não deve gerar nenhuma retentativa
    assert provider.call_count == 1
    # Etapa 17A (B3): uma única tentativa (mesmo falha) nunca tem
    # tentativa "anterior" a ela mesma.
    assert result.had_uncertain_prior_attempts is False


@pytest.mark.asyncio
async def test_timeout_is_treated_as_retryable():
    import asyncio

    class _SlowProvider(_ScriptedProvider):
        async def _call_api(self, request):
            self.call_count += 1
            if self.call_count == 1:
                await asyncio.sleep(10)  # nunca chega lá — timeout_seconds é curto
            item = self._script.pop(0)
            return (*item, None) if len(item) == 3 else item

    provider = _SlowProvider(
        script=[("resposta rápida", TokenUsage(), "scripted-model")],
        timeout_seconds=0.05,
        max_retries=1,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_missing_api_key_never_calls_call_api():
    """Etapa 9: _require_api_key() agora é checado dentro de complete(),
    ANTES do loop de retry — _call_api() nunca chega a ser invocado."""
    provider = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=1)
    provider._api_key = None  # simula provider sem key configurada

    result = await provider.complete(_request())

    assert provider.call_count == 0  # _call_api() nunca foi chamado


@pytest.mark.asyncio
async def test_missing_api_key_is_known_zero_not_unknown():
    """Falha 100% local — nenhuma chamada de rede foi tentada, então
    sabemos com certeza que o custo de API é zero. usage=None continua
    honesto (nenhum provider reportou usage), mas cost_usd=0.0 (não
    None) — conhecido-zero, não desconhecido."""
    provider = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=1)
    provider._api_key = None

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.usage is None
    assert result.cost_usd == 0.0
    assert result.attempts == 0
    assert result.error.type.value == "auth"
    assert result.error.retryable is False
    # Etapa 17A.1: nenhuma chamada foi feita, nada foi observado --
    # provider_finish_reason precisa ficar None, nunca inventado.
    assert result.provider_finish_reason is None


@pytest.mark.asyncio
async def test_sdk_level_auth_error_after_call_api_starts_is_unknown():
    """Erro de autenticação que ocorre DEPOIS de _call_api() já ter
    começado (ex.: o SDK rejeitou a chamada) continua desconhecido — a
    distinção certa é por ONDE o erro ocorre, não pelo ProviderErrorType
    (os dois casos compartilham AUTH, mas só o pré-request é known-zero)."""
    provider = _ScriptedProvider(
        script=[ProviderAuthError("chave inválida (rejeitada pelo SDK)")],
        timeout_seconds=5,
        max_retries=3,
    )
    result = await provider.complete(_request())

    assert provider.call_count == 1  # _call_api() FOI chamado desta vez
    assert result.status == "error"
    assert result.error.type.value == "auth"  # mesmo tipo do caso pré-request
    assert result.usage is None
    assert result.cost_usd is None  # mas aqui é DESCONHECIDO, não known-zero


@pytest.mark.asyncio
async def test_successful_call_computes_real_cost_from_pricing_registry():
    pricing = PricingRegistry(
        {("scripted", "scripted-model"): ModelRate(1.0, 2.0)}
    )
    provider = _ScriptedProvider(
        script=[("resposta", TokenUsage(input_tokens=1000, output_tokens=500), "scripted-model")],
        pricing=pricing,
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    # 1000 * 1.0/1e6 + 500 * 2.0/1e6 = 0.001 + 0.001 = 0.002
    assert result.cost_usd == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_successful_call_with_unknown_pricing_leaves_cost_none():
    provider = _ScriptedProvider(
        script=[("resposta", TokenUsage(input_tokens=1000, output_tokens=500), "scripted-model")],
        pricing=PricingRegistry({}),  # sem nenhuma taxa registrada
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.usage.input_tokens == 1000  # usage continua conhecido
    assert result.cost_usd is None  # mas o preço é desconhecido


@pytest.mark.asyncio
async def test_transport_failure_after_call_api_starts_leaves_usage_and_cost_unknown():
    """Reforça a distinção da Etapa 9: erro DEPOIS de _call_api() começar
    nunca vira known-zero, mesmo esgotando o retry."""
    provider = _ScriptedProvider(
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
        ],
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.usage is None
    assert result.cost_usd is None
    assert provider.call_count == 2  # _call_api() foi chamado nas duas tentativas


# ---------------------------------------------------------------------------
# Etapa 13 (T03.A) — Requested vs Effective Model Execution Provenance
#
# `requested_model` = o que foi PEDIDO (CompletionRequest.model OR
# provider.default_model), resolvido uma única vez ANTES de qualquer
# chamada de rede, imutável durante retries/falhas. `model` mantém a
# semântica retrocompatível: provider-reported quando disponível, senão
# requested_model como fallback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requested_equals_provider_reported_when_no_override():
    """Cenário 1: nenhum model explícito na request -> requested_model
    cai no default do provider, e o provider "reporta" o mesmo nome de
    volta -- requested == reported."""
    provider = _ScriptedProvider(
        script=[("resposta", TokenUsage(input_tokens=1, output_tokens=1), "scripted-model")],
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.requested_model == "scripted-model"
    assert result.model == "scripted-model"
    assert result.requested_model == result.model


@pytest.mark.asyncio
async def test_requested_differs_from_provider_reported():
    """Cenário 2: a request pede um alias específico, mas o provider
    responde com um snapshot/identidade diferente (ex.: alias -> versão
    pinada real) -- requested_model preserva o que foi PEDIDO, model
    reflete o que foi REPORTADO, e os dois divergem sem se
    sobrescreverem."""
    provider = _ScriptedProvider(
        script=[
            ("resposta", TokenUsage(input_tokens=1, output_tokens=1), "scripted-model-2025-06-01")
        ],
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request(model="scripted-model-latest"))

    assert result.status == "success"
    assert result.requested_model == "scripted-model-latest"
    assert result.model == "scripted-model-2025-06-01"
    assert result.requested_model != result.model


@pytest.mark.asyncio
async def test_missing_api_key_preserves_requested_model():
    """Cenário 4: falha pré-request (API key ausente) -- requested_model
    ainda é resolvido e preservado, mesmo que _call_api() nunca seja
    chamado. `model` faz fallback pro mesmo valor (comportamento
    retrocompatível: nenhuma identidade efetiva pôde ter sido reportada,
    já que nenhuma chamada de rede ocorreu)."""
    provider = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=1)
    provider._api_key = None

    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.status == "error"
    assert result.requested_model == "scripted-model-explicit"
    assert result.model == "scripted-model-explicit"


@pytest.mark.asyncio
async def test_post_request_failure_preserves_requested_model():
    """Cenário 5: falha DEPOIS de _call_api() começar, esgotando os
    retries -- requested_model continua o que foi pedido originalmente;
    `model` faz fallback pro mesmo valor (nunca inventamos uma
    identidade efetiva que o provider não chegou a confirmar)."""
    provider = _ScriptedProvider(
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
        ],
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.status == "error"
    assert result.requested_model == "scripted-model-explicit"
    assert result.model == "scripted-model-explicit"


@pytest.mark.asyncio
async def test_retry_preserves_requested_model_across_attempts():
    """Cenário 6: retry (erro retryable seguido de sucesso) -- o
    requested_model resolvido ANTES da primeira tentativa é o mesmo
    devolvido depois do retry, nunca recalculado a cada tentativa."""
    provider = _ScriptedProvider(
        script=[
            ProviderRateLimitError("rate limited"),
            ("resposta ok", TokenUsage(), "scripted-model-reported"),
        ],
        timeout_seconds=5,
        max_retries=2,
    )
    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.status == "success"
    assert result.attempts == 2
    assert result.requested_model == "scripted-model-explicit"
    assert result.model == "scripted-model-reported"


@pytest.mark.asyncio
async def test_pricing_uses_reported_model_not_requested_when_they_differ():
    """Cenário 7: quando requested e reported divergem, o PricingRegistry
    é consultado com o modelo REPORTADO (o que efetivamente rodou),
    nunca com o requested -- preço errado calculado com a taxa de um
    modelo diferente do que realmente respondeu seria pior que
    desconhecido."""
    pricing = PricingRegistry(
        {("scripted", "scripted-model-reported"): ModelRate(1.0, 2.0)}
    )
    provider = _ScriptedProvider(
        script=[
            (
                "resposta",
                TokenUsage(input_tokens=1000, output_tokens=500),
                "scripted-model-reported",
            )
        ],
        pricing=pricing,
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request(model="scripted-model-requested"))

    assert result.requested_model == "scripted-model-requested"
    assert result.model == "scripted-model-reported"
    # 1000 * 1.0/1e6 + 500 * 2.0/1e6 = 0.002 -- só bate se o lookup usou
    # "scripted-model-reported", não "scripted-model-requested" (que não
    # tem taxa registrada e daria cost_usd=None)
    assert result.cost_usd == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_model_field_backward_compatible_behavior_unchanged():
    """Cenário 13: o comportamento de `model` (campo pré-existente) não
    muda com a Etapa 13 -- continua sendo provider-reported quando
    disponível, senão requested_model como fallback. `requested_model`
    é aditivo, nunca substitui a semântica antiga de `model`."""
    provider = _ScriptedProvider(
        script=[("resposta", TokenUsage(input_tokens=1, output_tokens=1), "scripted-model")],
        timeout_seconds=5,
        max_retries=1,
    )
    # sem model explícito -> cai no default, como sempre se comportou
    result = await provider.complete(_request())
    assert result.model == "scripted-model"

    # com falha pré-request -> model cai pro requested/default, como
    # sempre se comportou (nenhuma mudança visível pra quem só olha `model`)
    provider2 = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=1)
    provider2._api_key = None
    result2 = await provider2.complete(_request(model="explicit-model"))
    assert result2.model == "explicit-model"


@pytest.mark.asyncio
async def test_known_cost_preserved_despite_prior_uncertain_attempt():
    """Etapa 17A (B3) — o ponto central: custo CONHECIDO da tentativa
    bem-sucedida nunca é descartado/zerado só porque uma tentativa
    anterior teve accounting desconhecida. Os dois sinais coexistem:
    cost_usd real (conhecido) + had_uncertain_prior_attempts=True
    (incompletude adicional)."""
    pricing = PricingRegistry(
        {("scripted", "scripted-model"): ModelRate(
            input_usd_per_million_tokens=10.0, output_usd_per_million_tokens=30.0
        )}
    )
    provider = _ScriptedProvider(
        script=[
            ProviderRateLimitError("rate limited"),
            ("resposta ok", TokenUsage(input_tokens=1000, output_tokens=1000), "scripted-model"),
        ],
        pricing=pricing,
        timeout_seconds=5,
        max_retries=2,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    expected_cost = (1000 / 1_000_000) * 10.0 + (1000 / 1_000_000) * 30.0
    assert result.cost_usd == pytest.approx(expected_cost)  # NUNCA None/zerado
    assert result.had_uncertain_prior_attempts is True  # mas a incerteza é sinalizada


# ---------------------------------------------------------------------------
# Etapa 17A.2 — is_known_output_truncation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "finish_reason",
    ["max_tokens", "length", "MAX_TOKENS"],
)
def test_is_known_output_truncation_recognizes_confirmed_native_reasons(finish_reason):
    """Os três vocabulários NATIVOS já confirmados por adapter/teste
    (Anthropic/OpenAI/Gemini, ver docstring de
    ProviderResponse.provider_finish_reason) -- nunca um quarto valor
    adivinhado."""
    assert is_known_output_truncation(finish_reason) is True


@pytest.mark.parametrize(
    "finish_reason",
    [None, "end_turn", "stop", "STOP", "max_tokens_typo", ""],
)
def test_is_known_output_truncation_rejects_everything_else(finish_reason):
    """Nunca infere truncamento de um motivo desconhecido/ausente --
    inclusive os motivos de parada NORMAL dos três providers (end_turn/
    stop/STOP)."""
    assert is_known_output_truncation(finish_reason) is False
