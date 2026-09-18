"""
Testes da lógica compartilhada em app/providers/base.py, usando um provider
falso que não faz nenhuma chamada de rede — só simula sucesso/erro conforme
programado, para exercitar o loop de retry isoladamente.
"""

from __future__ import annotations

import pytest

from app.models.provider_models import (
    CompletionRequest,
    Message,
    ModelIdentitySource,
    ProviderExecutionPolicy,
    TokenUsage,
)
from app.providers.base import LLMProvider, is_known_output_truncation
from app.providers.errors import (
    ProviderAPIError,
    ProviderAuthError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
)
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
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


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
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


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
    # Nenhuma chamada de rede foi sequer tentada -- nunca provider_reported.
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


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
    # Nenhuma metadata observada (o script só continha erros) -- fallback.
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


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
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


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
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED
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


# ---------------------------------------------------------------------------
# Provenance de identidade de modelo (ModelIdentitySource) -- resolvida uma
# única vez em LLMProvider.complete() (`_resolve_model_identity`), nunca
# duplicada por adapter. `_ScriptedProvider` já devolve o 3º elemento da
# tupla como o valor CRU observado (possivelmente None) desde a Etapa 17A --
# nenhuma mudança de fixture necessária pra testar o fallback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_with_provider_omitting_model_marks_requested_fallback():
    """Sucesso de transporte, mas o adapter não conseguiu observar
    nenhuma identidade de modelo (3º elemento da tupla é None) -- `model`
    cai pro requested, e a origem é marcada honestamente como fallback,
    nunca provider_reported."""
    provider = _ScriptedProvider(
        script=[("resposta", TokenUsage(input_tokens=1, output_tokens=1), None)],
        timeout_seconds=5,
        max_retries=1,
    )
    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.status == "success"
    assert result.requested_model == "scripted-model-explicit"
    assert result.model == "scripted-model-explicit"
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_malformed_response_with_observed_model_marks_provider_reported():
    """Falha DEPOIS de _call_api() começar (texto malformado), mas o
    adapter conseguiu observar uma identidade real antes da validação de
    texto falhar (Etapa 17A.1) -- essa identidade é preservada E marcada
    como provider_reported, nunca fallback (ela FOI observada)."""

    async def _call_api(self, request):
        raise ProviderMalformedResponseError(
            "resposta em formato inesperado",
            observed_model="scripted-model-observed",
            observed_usage=TokenUsage(input_tokens=5, output_tokens=0),
            observed_finish_reason="stop",
        )

    provider = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=0)
    provider._call_api = _call_api.__get__(provider)

    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.status == "error"
    assert result.requested_model == "scripted-model-explicit"
    assert result.model == "scripted-model-observed"
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_malformed_response_without_observed_model_marks_requested_fallback():
    """Mesmo caminho acima, mas o adapter não conseguiu observar
    identidade nenhuma antes do texto falhar (observed_model=None) --
    fallback honesto, nunca uma identidade inventada."""

    async def _call_api(self, request):
        raise ProviderMalformedResponseError(
            "resposta em formato inesperado",
            observed_model=None,
            observed_usage=None,
            observed_finish_reason=None,
        )

    provider = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=0)
    provider._call_api = _call_api.__get__(provider)

    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.status == "error"
    assert result.requested_model == "scripted-model-explicit"
    assert result.model == "scripted-model-explicit"
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_pre_dispatch_failure_never_marks_provider_reported():
    """Regressão -- falha 100% local (API key ausente), sem NENHUMA
    chamada de rede: `model_identity_source` NUNCA pode ser
    provider_reported aqui, porque nenhuma resposta jamais existiu pra
    observar (ver seção 6/13 do contrato desta slice: 'se uma falha
    ocorre antes de qualquer resposta do provider, não invente uma
    identidade provider-reported')."""
    provider = _ScriptedProvider(script=[], timeout_seconds=5, max_retries=1)
    provider._api_key = None

    result = await provider.complete(_request(model="scripted-model-explicit"))

    assert result.attempts == 0
    assert result.model_identity_source != ModelIdentitySource.PROVIDER_REPORTED
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


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


# ---------------------------------------------------------------------------
# T02.2 -- ProviderExecutionPolicy -> comportamento de LLMProvider.complete()
#
# LLMProvider.__init__ continua aceitando timeout_seconds/max_retries como
# antes (sem mudança de assinatura) -- estes testes provam explicitamente
# que os valores DERIVADOS de uma ProviderExecutionPolicy (a mesma tradução
# que app/providers/factory.py faz) produzem exatamente o comportamento que
# o contrato T02.2 promete.
# ---------------------------------------------------------------------------


def _provider_from_policy(policy: ProviderExecutionPolicy, script: list, **kwargs) -> _ScriptedProvider:
    """Espelha EXATAMENTE a tradução real de app/providers/factory.py
    (`timeout_seconds`/`max_retries`) -- desde o repair de fidelidade de
    timeout fracionário (achado MEDIUM da revisão independente),
    `attempt_timeout_seconds` é repassado losslessly (sem `int(...)`)
    tanto aqui quanto na factory real, então este helper não precisa
    mais divergir do caminho de produção pra usar timeouts fracionários
    curtos nos testes D/E/F -- eles já são válidos e preservados de
    ponta a ponta (`ProviderExecutionPolicy.attempt_timeout_seconds` é
    `float > 0` por contrato de domínio, nunca só valores inteiros)."""
    return _ScriptedProvider(
        script=script,
        timeout_seconds=policy.attempt_timeout_seconds,
        max_retries=policy.max_transport_attempts_per_completion - 1,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_policy_max_attempts_one_allows_at_most_one_call_on_retryable_failure():
    """Teste D -- max_transport_attempts_per_completion=1 significa
    ZERO retries: uma falha retryable esgota na primeira tentativa."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1
    )
    provider = _provider_from_policy(
        policy,
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ("nunca deveria ser consumido", TokenUsage(), "scripted-model"),
        ],
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_policy_max_attempts_two_allows_at_most_two_calls_on_retryable_failure():
    """Teste D -- max_transport_attempts_per_completion=2 permite
    exatamente 1 retry (2 invocações de _call_api no total)."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=2
    )
    provider = _provider_from_policy(
        policy,
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
            ("nunca deveria ser consumido", TokenUsage(), "scripted-model"),
        ],
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_policy_max_attempts_three_allows_at_most_three_calls_on_retryable_failure():
    """Teste D -- max_transport_attempts_per_completion=3 (o default
    real: provider_max_retries=2 + 1) permite exatamente 2 retries."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=3
    )
    provider = _provider_from_policy(
        policy,
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
            ProviderAPIError("erro 500 mais uma vez", retryable=True),
        ],
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_policy_max_attempts_three_non_retryable_failure_stops_at_one_call():
    """Teste D -- mesmo com max_transport_attempts_per_completion=3, um
    erro NÃO retryable esgota imediatamente na primeira tentativa."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=3
    )
    provider = _provider_from_policy(
        policy, script=[ProviderAuthError("chave inválida")]
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.retryable is False
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_policy_timeout_applies_independently_to_each_transport_attempt():
    """Teste E -- o timeout por tentativa reseta a cada nova invocação
    de _call_api: a 1ª tentativa estoura o timeout (deliberadamente
    lenta), mas a 2ª tentativa (rápida) ainda tem o MESMO teto completo
    disponível pra si -- nunca um orçamento decrescente entre
    tentativas. Determinístico via asyncio.sleep curto, sem depender de
    tempo real de rede."""
    import asyncio

    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=0.05, max_transport_attempts_per_completion=2
    )

    class _SlowThenFastProvider(_ScriptedProvider):
        async def _call_api(self, request):
            self.call_count += 1
            if self.call_count == 1:
                await asyncio.sleep(10)  # nunca chega lá -- timeout curto interrompe antes
            item = self._script.pop(0)
            return (*item, None) if len(item) == 3 else item

    provider = _SlowThenFastProvider(
        script=[("resposta rápida", TokenUsage(), "scripted-model")],
        timeout_seconds=policy.attempt_timeout_seconds,
        max_retries=policy.max_transport_attempts_per_completion - 1,
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.attempts == 2
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_policy_backoff_never_counted_inside_the_per_attempt_timeout():
    """Teste F -- o backoff entre tentativas (LLMProvider._backoff_delay,
    ~0.5s+ na 2ª tentativa) fica FORA do wait_for de cada tentativa:
    mesmo com attempt_timeout_seconds MUITO curto, uma sequência de
    erros retryable rápidos (sem sleep dentro de _call_api) nunca
    estoura por timeout -- só por esgotar as tentativas. Prova que
    backoff e timeout-por-tentativa são orçamentos independentes."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=0.01, max_transport_attempts_per_completion=2
    )
    provider = _provider_from_policy(
        policy,
        script=[
            ProviderRateLimitError("rate limited"),
            ("resposta ok", TokenUsage(), "scripted-model"),
        ],
    )
    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.attempts == 2


@pytest.mark.parametrize(
    "client_attr,sdk_class_name",
    [("_client", "AsyncOpenAI")],
)
def test_openai_sdk_retries_remain_disabled_regardless_of_policy(client_attr, sdk_class_name):
    """Teste G -- reforça (não substitui) a garantia já existente em
    tests/providers/test_openai_provider.py: o SDK nativo continua
    construído com max_retries=0 independente do
    max_transport_attempts_per_completion resolvido -- retry é
    responsabilidade EXCLUSIVA de LLMProvider.complete()."""
    from app.providers.openai_provider import OpenAIProvider

    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=10.0, max_transport_attempts_per_completion=5
    )
    provider = OpenAIProvider(
        api_key="fake-key",
        timeout_seconds=int(policy.attempt_timeout_seconds),
        max_retries=policy.max_transport_attempts_per_completion - 1,
        default_model="gpt-5.5",
        pricing=PricingRegistry({}),
    )
    assert provider._client.max_retries == 0
    assert provider._max_retries == 4


# ---------------------------------------------------------------------------
# Repair (adversarial review -- transport backoff correctness) --
# `_backoff_delay(attempt_number)` é documentada/implementada em termos do
# PRÓXIMO attempt_number (1-indexado; a 1ª retentativa é attempt_number=2),
# mas o loop de retry chamava `_backoff_delay(attempts)` -- o número da
# tentativa que ACABOU de falhar, não da próxima -- produzindo um delay
# ~0.25s/0.5s em vez do ~0.5s/1.0s pretendido. Repair: `_backoff_delay(attempts + 1)`.
#
# Todos os testes abaixo usam `unittest.mock.patch` sobre `asyncio.sleep`
# (nunca dorme de verdade) -- determinísticos, sem custo de wall-clock.
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, patch  # noqa: E402


@pytest.mark.asyncio
async def test_never_returning_completion_makes_exactly_three_transport_attempts():
    """1 -- uma falha retryable persistente, com a policy REAL de
    produção (provider_max_retries=2 -> max_transport_attempts=3), nunca
    excede nem fica aquém de 3 chamadas reais a _call_api()."""
    provider = _ScriptedProvider(
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
            ProviderAPIError("erro 500 mais uma vez", retryable=True),
        ],
        timeout_seconds=5,
        max_retries=2,  # provider_max_retries=2 -> 3 tentativas no total
    )
    with patch("app.providers.base.asyncio.sleep", new_callable=AsyncMock):
        result = await provider.complete(_request())

    assert result.status == "error"
    assert provider.call_count == 3
    assert result.attempts == 3


@pytest.mark.asyncio
async def test_backoff_invoked_with_intended_upcoming_attempt_numbers():
    """2 -- prova o número exato de argumento passado a `_backoff_delay`
    em cada chamada: a 1ª tentativa falha -> delay pra tentativa 2 ->
    `_backoff_delay(2)`; a 2ª tentativa falha -> delay pra tentativa 3 ->
    `_backoff_delay(3)`. NUNCA `_backoff_delay(1)`/`_backoff_delay(2)`
    (o bug antigo, que passava o número da tentativa que ACABOU de
    falhar). Sem sleep real -- `asyncio.sleep` mockado."""
    provider = _ScriptedProvider(
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
            ProviderAPIError("erro 500 mais uma vez", retryable=True),
        ],
        timeout_seconds=5,
        max_retries=2,
    )
    with (
        patch("app.providers.base.asyncio.sleep", new_callable=AsyncMock),
        patch("app.providers.base._backoff_delay", wraps=lambda n: 0.0) as mock_backoff,
    ):
        await provider.complete(_request())

    # Exatamente 2 chamadas de backoff (entre as 3 tentativas -- nunca
    # depois da última, que já esgota o retry sem mais nenhum sleep).
    assert mock_backoff.call_args_list == [
        ((2,),),
        ((3,),),
    ]


@pytest.mark.asyncio
async def test_backoff_delay_produces_the_intended_schedule_for_upcoming_attempts():
    """Contrato de schedule -- `_backoff_delay(2)` (delay antes da 1ª
    retentativa) e `_backoff_delay(3)` (delay antes da 2ª retentativa)
    produzem os valores BASE pretendidos (~0.5s / ~1.0s, antes do
    jitter aleatório de até +25%) -- nunca os ~0.25s/~0.5s que o bug
    antigo produzia ao receber o número da tentativa já falhada."""
    from app.providers.base import _BASE_DELAY_SECONDS, _backoff_delay

    with patch("app.providers.base.random.uniform", return_value=0.0):
        delay_before_2nd_attempt = _backoff_delay(2)
        delay_before_3rd_attempt = _backoff_delay(3)

    assert delay_before_2nd_attempt == pytest.approx(_BASE_DELAY_SECONDS)  # ~0.5s
    assert delay_before_3rd_attempt == pytest.approx(_BASE_DELAY_SECONDS * 2)  # ~1.0s
    # Confirma que NÃO é mais o schedule do bug antigo (0.25s/0.5s).
    assert delay_before_2nd_attempt != pytest.approx(0.25)
    assert delay_before_3rd_attempt != pytest.approx(0.5)


@pytest.mark.asyncio
async def test_no_extra_outer_retry_layer_call_count_matches_attempts_exactly():
    """3 -- estrutura agregada de timeout/retry: `attempts` (contador
    interno) e `call_count` (chamadas REAIS a `_call_api`) permanecem
    EXATAMENTE iguais em todo desfecho -- nunca uma camada externa
    dobrando o número de tentativas reais além do que a policy declara
    (aqui, max_transport_attempts=3 via provider_max_retries=2)."""
    provider = _ScriptedProvider(
        script=[
            ProviderAPIError("erro 500", retryable=True),
            ProviderAPIError("erro 500 de novo", retryable=True),
            ("resposta ok", TokenUsage(input_tokens=1, output_tokens=1), "scripted-model"),
        ],
        timeout_seconds=5,
        max_retries=2,
    )
    with patch("app.providers.base.asyncio.sleep", new_callable=AsyncMock):
        result = await provider.complete(_request())

    assert result.status == "success"
    assert provider.call_count == 3
    assert result.attempts == 3
    assert provider.call_count == result.attempts  # nenhuma retentativa "extra" oculta


@pytest.mark.asyncio
async def test_timeout_exhaustion_retains_uncertain_prior_attempts_and_unknown_accounting():
    """4 -- uma falha por TIMEOUT (não erro de API genérico) que esgota
    todas as tentativas continua preservando exatamente o contrato já
    existente: usage/cost_usd desconhecidos (nunca conhecido-zero, a
    chamada pode ter sido processada remotamente) e
    had_uncertain_prior_attempts=True (mais de uma tentativa real
    discou). Sem sleep real."""
    import asyncio as _asyncio

    class _AlwaysTimesOutProvider(_ScriptedProvider):
        async def _call_api(self, request):
            self.call_count += 1
            await _asyncio.sleep(10)  # nunca retorna -- sempre estoura o timeout curto
            raise AssertionError("nunca deveria chegar aqui")

    provider = _AlwaysTimesOutProvider(
        script=[], timeout_seconds=0.01, max_retries=2
    )
    # Repair da própria correção deste patch: NÃO mockar `asyncio.sleep`
    # globalmente aqui -- `asyncio` é um módulo singleton, então mockar
    # `app.providers.base.asyncio.sleep` neutralizaria TAMBÉM o
    # `asyncio.sleep(10)` real usado acima pra simular a tentativa que
    # nunca retorna, quebrando a própria premissa do teste (o timeout
    # nunca dispararia). Mocka só `_backoff_delay` (retorna 0.0) --
    # `asyncio.sleep(0.0)` real ainda roda, mas resolve quase
    # instantaneamente, sem afetar o `wait_for` interno de cada
    # tentativa.
    with patch("app.providers.base._backoff_delay", return_value=0.0) as mock_backoff:
        result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "timeout"
    assert provider.call_count == 3
    assert result.attempts == 3
    assert result.usage is None
    assert result.cost_usd is None
    assert result.pricing_provenance is None
    assert result.had_uncertain_prior_attempts is True
    # Backoff é chamado só ENTRE tentativas -- 2 chamadas (antes da 2ª e
    # da 3ª tentativa), nunca dentro do wait_for de cada tentativa.
    assert mock_backoff.call_count == 2
