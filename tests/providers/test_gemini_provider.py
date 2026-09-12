from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors as genai_errors

from app.models.provider_models import CompletionRequest, Message
from app.providers.gemini_provider import GeminiProvider
from app.providers.pricing import PricingRegistry


def _provider(max_retries: int = 0) -> GeminiProvider:
    provider = GeminiProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=max_retries,
        default_model="gemini-test",
        pricing=PricingRegistry({}),
    )
    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock()
    return provider


def _request() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="O que é fotossíntese?")])


class _FakeUsageMetadata:
    def __init__(self, prompt_token_count, candidates_token_count, thoughts_token_count=None):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count
        self.thoughts_token_count = thoughts_token_count


class _FakeFinishReason:
    """Imita o enum FinishReason do SDK real -- só o atributo `.value`
    que o adapter lê."""

    def __init__(self, value):
        self.value = value


class _FakeCandidate:
    def __init__(self, finish_reason=None):
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(
        self,
        text,
        prompt_tokens=6,
        candidates_tokens=9,
        thoughts_tokens=None,
        model_version="gemini-3.7-flash",
        finish_reason=None,
    ):
        self.text = text
        self.usage_metadata = _FakeUsageMetadata(prompt_tokens, candidates_tokens, thoughts_tokens)
        self.model_version = model_version
        self.candidates = (
            [_FakeCandidate(_FakeFinishReason(finish_reason))] if finish_reason else [_FakeCandidate()]
        )


@pytest.mark.asyncio
async def test_successful_completion_is_normalized():
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "Fotossíntese é o processo pelo qual plantas convertem luz em energia."
    )

    result = await provider.complete(_request())

    assert result.status == "success"
    assert "Fotossíntese" in result.text
    assert result.provider == "gemini"
    assert result.usage.input_tokens == 6
    assert result.usage.output_tokens == 9


# ---------------------------------------------------------------------------
# Etapa 9 (patch pós-revisão) — thinking tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidates_without_thoughts_uses_candidates_only():
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "resposta", prompt_tokens=10, candidates_tokens=100, thoughts_tokens=None
    )
    result = await provider.complete(_request())
    assert result.usage.output_tokens == 100


@pytest.mark.asyncio
async def test_candidates_plus_thoughts_are_summed():
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "resposta", prompt_tokens=10, candidates_tokens=100, thoughts_tokens=50
    )
    result = await provider.complete(_request())
    assert result.usage.output_tokens == 150


@pytest.mark.asyncio
async def test_thoughts_zero_is_known_zero_not_added_incorrectly():
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "resposta", prompt_tokens=10, candidates_tokens=100, thoughts_tokens=0
    )
    result = await provider.complete(_request())
    assert result.usage.output_tokens == 100


@pytest.mark.asyncio
async def test_usage_metadata_entirely_absent_is_unknown_not_zero():
    class _ResponseNoUsageMetadata:
        def __init__(self, text):
            self.text = text
            self.usage_metadata = None
            self.model_version = "gemini-3.7-flash"

    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _ResponseNoUsageMetadata(
        "resposta"
    )
    result = await provider.complete(_request())
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None


@pytest.mark.asyncio
async def test_usage_metadata_entirely_absent_produces_unknown_cost_with_known_pricing():
    """O caso central do bug encontrado na revisão: mesmo com uma taxa
    REGISTRADA pro modelo, usage totalmente ausente não pode produzir
    cost_usd=0.0 — precisa ficar None (desconhecido)."""
    from app.providers.pricing import ModelRate, PricingRegistry

    class _ResponseNoUsageMetadata:
        def __init__(self, text):
            self.text = text
            self.usage_metadata = None
            self.model_version = "gemini-3.7-flash"

    pricing = PricingRegistry({("gemini", "gemini-3.7-flash"): ModelRate(1.0, 2.0)})
    provider = GeminiProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        default_model="gemini-3.7-flash",
        pricing=pricing,
    )
    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=_ResponseNoUsageMetadata("resposta")
    )

    result = await provider.complete(_request())

    assert result.cost_usd is None
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None


@pytest.mark.asyncio
async def test_candidates_absent_but_metadata_present_stays_unknown():
    """candidates_token_count ausente (mesmo com o bloco usage_metadata
    presente) continua desconhecido — sem regressão em relação ao
    comportamento anterior a esta correção."""
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "resposta", prompt_tokens=10, candidates_tokens=None, thoughts_tokens=50
    )
    result = await provider.complete(_request())
    assert result.usage.output_tokens is None


@pytest.mark.asyncio
async def test_candidates_absent_produces_unknown_cost_with_known_pricing():
    """Mesmo caso do teste acima, mas confirmando cost_usd=None de ponta
    a ponta com uma taxa registrada — não só o usage."""
    from app.providers.pricing import ModelRate, PricingRegistry

    pricing = PricingRegistry({("gemini", "gemini-3.7-flash"): ModelRate(1.0, 2.0)})
    provider = GeminiProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        default_model="gemini-3.7-flash",
        pricing=pricing,
    )
    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=_FakeResponse(
            "resposta", prompt_tokens=10, candidates_tokens=None, thoughts_tokens=50
        )
    )

    result = await provider.complete(_request())

    assert result.cost_usd is None
    assert result.usage.output_tokens is None


@pytest.mark.asyncio
async def test_cost_calculation_includes_thinking_tokens():
    from app.providers.pricing import ModelRate, PricingRegistry

    pricing = PricingRegistry(
        {("gemini", "gemini-3.7-flash"): ModelRate(1.0, 2.0)}
    )
    provider = GeminiProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        default_model="gemini-3.7-flash",
        pricing=pricing,
    )
    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=_FakeResponse(
            "resposta", prompt_tokens=1000, candidates_tokens=100, thoughts_tokens=400
        )
    )

    result = await provider.complete(_request())

    # output = 100 + 400 = 500 -> custo = 1000*1/1e6 + 500*2/1e6 = 0.001 + 0.001 = 0.002
    assert result.usage.output_tokens == 500
    assert result.cost_usd == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_token_budget_accounts_for_thinking_tokens():
    """Confirma que o total usado pra budget (não só custo) já reflete
    thinking — sem a correção, um budget de tokens seria subestimado."""
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "resposta", prompt_tokens=10, candidates_tokens=1000, thoughts_tokens=4000
    )
    result = await provider.complete(_request())
    total_tokens = (result.usage.input_tokens or 0) + (result.usage.output_tokens or 0)
    assert total_tokens == 10 + 1000 + 4000


# ---------------------------------------------------------------------------
# Etapa 9 (patch pós-revisão) — effective model via model_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_version_present_is_used_as_effective_model():
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        "resposta", model_version="gemini-3.7-flash"
    )
    result = await provider.complete(_request())
    assert result.model == "gemini-3.7-flash"


@pytest.mark.asyncio
async def test_model_version_absent_falls_back_to_requested_model():
    class _ResponseNoModelVersion:
        def __init__(self, text):
            self.text = text
            self.usage_metadata = _FakeUsageMetadata(6, 9)
            # sem atributo model_version — getattr deve cair pro fallback

    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _ResponseNoModelVersion(
        "resposta"
    )
    result = await provider.complete(_request())
    assert result.model == "gemini-test"  # default_model do _provider()
    # Etapa 13 (T03.A): requested_model é resolvido independentemente do
    # que o provider reporta -- aqui os dois coincidem porque a request
    # não pediu um model explícito (cai no default), mas são campos
    # distintos, nunca o mesmo valor por acidente de implementação.
    assert result.requested_model == "gemini-test"


@pytest.mark.asyncio
async def test_pricing_lookup_uses_effective_model_not_requested():
    """Se o model_version divergir do que o PricingRegistry conhece, o
    custo fica desconhecido (None) — nunca um preço errado calculado com
    a taxa de um modelo diferente do que realmente respondeu."""
    from app.providers.pricing import ModelRate, PricingRegistry

    pricing = PricingRegistry({("gemini", "gemini-3.7-flash"): ModelRate(1.0, 2.0)})
    provider = GeminiProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        default_model="gemini-3.7-flash",
        pricing=pricing,
    )
    provider._client = MagicMock()
    # model_version diverge da chave registrada (ex.: sufixo hipotético)
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=_FakeResponse(
            "resposta",
            prompt_tokens=100,
            candidates_tokens=50,
            model_version="gemini-3.7-flash-001",
        )
    )

    result = await provider.complete(_request())

    assert result.model == "gemini-3.7-flash-001"  # identidade real preservada
    assert result.cost_usd is None  # chave não bate -> desconhecido, nunca chutado


@pytest.mark.asyncio
async def test_auth_error_401_maps_to_auth_and_is_not_retried():
    provider = _provider(max_retries=3)
    provider._client.aio.models.generate_content.side_effect = genai_errors.ClientError(
        code=401, response_json={"error": {"message": "invalid api key"}}, response=None
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "auth"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_rate_limit_429_is_retryable():
    provider = _provider(max_retries=1)
    provider._client.aio.models.generate_content.side_effect = genai_errors.ClientError(
        code=429, response_json={"error": {"message": "quota exceeded"}}, response=None
    )

    result = await provider.complete(_request())

    assert result.error.type.value == "rate_limit"
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_other_client_error_is_not_retryable():
    provider = _provider(max_retries=3)
    provider._client.aio.models.generate_content.side_effect = genai_errors.ClientError(
        code=400, response_json={"error": {"message": "bad request"}}, response=None
    )

    result = await provider.complete(_request())

    assert result.error.type.value == "api_error"
    assert result.error.retryable is False
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_server_error_is_retryable():
    provider = _provider(max_retries=0)
    provider._client.aio.models.generate_content.side_effect = genai_errors.ServerError(
        code=500, response_json={"error": {"message": "internal error"}}, response=None
    )

    result = await provider.complete(_request())

    assert result.error.type.value == "api_error"
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_empty_text_is_malformed_and_not_retried():
    provider = _provider(max_retries=3)
    provider._client.aio.models.generate_content.return_value = _FakeResponse(text="")

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "malformed_response"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_malformed_response_preserves_observed_model_usage_finish_reason():
    """Etapa 17A.1 (Objetivo A/B) — reprodução real: resposta visivelmente
    incompleta/truncada (texto vazio), mas usage_metadata/model_version/
    finish_reason (MAX_TOKENS) já eram reais no objeto de resposta."""
    provider = _provider(max_retries=0)
    provider._client.aio.models.generate_content.return_value = _FakeResponse(
        text="", prompt_tokens=158, candidates_tokens=1020,
        model_version="gemini-3.7-flash-002", finish_reason="MAX_TOKENS",
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.model == "gemini-3.7-flash-002"
    assert result.usage.input_tokens == 158
    assert result.usage.output_tokens == 1020
    assert result.provider_finish_reason == "MAX_TOKENS"


def test_sdk_retry_is_disabled_on_client_construction():
    """attempts=1 no HttpRetryOptions = só a tentativa inicial, sem retry
    do SDK — igual à correção aplicada em OpenAI (max_retries=0) e
    Anthropic (max_retries=0)."""
    with patch("app.providers.gemini_provider.genai.Client") as mock_client_cls:
        GeminiProvider(
            api_key="test-key",
            timeout_seconds=5,
            max_retries=2,
            default_model="gemini-test",
            pricing=PricingRegistry({}),
        )
        assert mock_client_cls.call_count == 1
        _, kwargs = mock_client_cls.call_args
        assert kwargs["api_key"] == "test-key"
        assert kwargs["http_options"].retry_options.attempts == 1


@pytest.mark.asyncio
async def test_unknown_usage_response_propagates_true_via_sum_usage_and_cost():
    """Confirma o flag downstream, não só o campo isolado: o
    ProviderResponse produzido por essa chamada (com pricing conhecido
    mas usage ausente) precisa marcar has_unknown_accounting_components
    verdadeiro quando agregado, exatamente como qualquer outro registro
    de accounting incompleto."""
    from app.orchestrator.budget import sum_usage_and_cost
    from app.providers.pricing import ModelRate, PricingRegistry

    class _ResponseNoUsageMetadata:
        def __init__(self, text):
            self.text = text
            self.usage_metadata = None
            self.model_version = "gemini-3.7-flash"

    pricing = PricingRegistry({("gemini", "gemini-3.7-flash"): ModelRate(1.0, 2.0)})
    provider = GeminiProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        default_model="gemini-3.7-flash",
        pricing=pricing,
    )
    provider._client = MagicMock()
    provider._client.aio.models.generate_content = AsyncMock(
        return_value=_ResponseNoUsageMetadata("resposta")
    )

    result = await provider.complete(_request())

    _, _, known_cost, has_unknown = sum_usage_and_cost([result])
    assert known_cost == 0.0
    assert has_unknown is True


@pytest.mark.asyncio
async def test_max_output_tokens_per_call_reaches_correct_gemini_parameter():
    """Etapa 17A.1 (requisito J) — max_output_tokens_per_call chega ao
    SDK real como `max_output_tokens` dentro de GenerateContentConfig
    (parâmetro nativo da Gemini)."""
    provider = _provider()
    provider._client.aio.models.generate_content.return_value = _FakeResponse("ok")

    request = CompletionRequest(
        messages=[Message(role="user", content="pergunta")], max_tokens=4096
    )
    await provider.complete(request)

    call_kwargs = provider._client.aio.models.generate_content.call_args.kwargs
    assert call_kwargs["config"].max_output_tokens == 4096
