from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from app.models.provider_models import CompletionRequest, Message, ModelIdentitySource
from app.providers.openai_provider import OpenAIProvider
from app.providers.pricing import PricingRegistry


def _provider(max_retries: int = 0) -> OpenAIProvider:
    provider = OpenAIProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=max_retries,
        default_model="gpt-test",
        pricing=PricingRegistry({}),
    )
    provider._client = AsyncMock()
    return provider


def _request(model: str | None = None) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="Qual a capital do Brasil?")], model=model
    )


def _fake_response(request_method="POST", url="https://api.openai.com/v1/chat/completions"):
    return httpx.Request(request_method, url)


class _FakeChoiceMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _FakeChoiceMessage(content)
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(
        self, content, model="gpt-test", prompt_tokens=10, completion_tokens=5,
        finish_reason="stop",
    ):
        self.choices = [_FakeChoice(content, finish_reason=finish_reason)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)
        self.model = model


@pytest.mark.asyncio
async def test_successful_completion_is_normalized():
    provider = _provider()
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse("Brasília", prompt_tokens=12, completion_tokens=3)
    )

    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.text == "Brasília"
    assert result.provider == "openai"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert result.cost_usd is None  # "gpt-test" não tem taxa registrada — desconhecido


# ---------------------------------------------------------------------------
# Provenance de identidade de modelo (ModelIdentitySource) -- A.1/A.2 do
# contrato desta slice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_reports_model_marks_provider_reported():
    provider = _provider()
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse("Brasília", model="gpt-5.5-2026-01-15")
    )

    result = await provider.complete(_request(model="gpt-5.5"))

    assert result.requested_model == "gpt-5.5"
    assert result.model == "gpt-5.5-2026-01-15"
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_provider_omits_model_marks_requested_fallback():
    provider = _provider()
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse("Brasília", model=None)
    )

    result = await provider.complete(_request(model="gpt-5.5"))

    assert result.requested_model == "gpt-5.5"
    assert result.model == "gpt-5.5"
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


# ---------------------------------------------------------------------------
# Compatibilidade de parâmetro de output-token -- gpt-5.5 rejeita
# `max_tokens` na Chat Completions API, exige `max_completion_tokens`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sends_max_completion_tokens_not_max_tokens():
    """Reprodução real: HTTP 400 'Unsupported parameter: max_tokens ...
    Use max_completion_tokens instead.' -- prova que o parâmetro certo é
    enviado, e o antigo/incompatível nunca é."""
    provider = _provider()
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse("Brasília")
    )

    await provider.complete(_request())

    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert "max_completion_tokens" in call_kwargs
    assert "max_tokens" not in call_kwargs


@pytest.mark.asyncio
async def test_max_completion_tokens_value_matches_request_semantic_intent():
    """O valor enviado é exatamente o que a camada agnóstica de provider
    pediu (max_output_tokens_per_call -> CompletionRequest.max_tokens) --
    só o NOME do parâmetro muda na fronteira do adapter, nunca o valor
    nem a semântica de intenção."""
    provider = _provider()
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse("Brasília")
    )
    request = CompletionRequest(
        messages=[Message(role="user", content="pergunta")], max_tokens=777
    )

    await provider.complete(request)

    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["max_completion_tokens"] == 777


@pytest.mark.asyncio
async def test_provenance_and_accounting_unchanged_by_compatibility_fix():
    """A correção do nome do parâmetro não pode, de forma nenhuma,
    alterar requested_model/model reportado nem a contabilidade."""
    provider = _provider()
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse("Brasília", model="gpt-5.5-2025-06-15")
    )

    result = await provider.complete(_request(model="gpt-5.5"))

    assert result.requested_model == "gpt-5.5"
    assert result.model == "gpt-5.5-2025-06-15"


@pytest.mark.asyncio
async def test_status_400_unsupported_parameter_would_have_reproduced_before_fix():
    """Documenta o erro real observado (fora de escopo simular o SDK
    inteiro aqui) -- a asserção que importa é a de
    test_sends_max_completion_tokens_not_max_tokens acima; este teste só
    confirma que erros 4xx continuam sendo tratados como não-retryable,
    comportamento preservado pela correção."""
    provider = _provider()
    request_obj = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response_obj = httpx.Response(
        400, request=request_obj, json={"error": {"message": "Unsupported parameter"}}
    )
    provider._client.chat.completions.create = AsyncMock(
        side_effect=APIStatusError(
            "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.",
            response=response_obj,
            body=None,
        )
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.retryable is False  # 4xx não é retryable


@pytest.mark.asyncio
async def test_missing_api_key_returns_error_without_calling_sdk():
    provider = OpenAIProvider(
        api_key=None,
        timeout_seconds=5,
        max_retries=0,
        default_model="gpt-test",
        pricing=PricingRegistry({}),
    )
    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "auth"
    # Etapa 9 — falha local pré-request é conhecido-zero, não desconhecido:
    assert result.usage is None
    assert result.cost_usd == 0.0
    assert result.attempts == 0


@pytest.mark.asyncio
async def test_authentication_error_maps_to_auth_and_is_not_retried():
    provider = _provider(max_retries=3)
    response = httpx.Response(401, request=_fake_response())
    provider._client.chat.completions.create = AsyncMock(
        side_effect=AuthenticationError("invalid api key", response=response, body=None)
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "auth"
    assert result.error.retryable is False
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_rate_limit_error_maps_to_rate_limit_and_is_retryable():
    provider = _provider(max_retries=1)
    response = httpx.Response(429, request=_fake_response())
    provider._client.chat.completions.create = AsyncMock(
        side_effect=RateLimitError("rate limited", response=response, body=None)
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "rate_limit"
    assert result.error.retryable is True
    assert result.attempts == 2  # 1 tentativa + 1 retry configurado


@pytest.mark.asyncio
async def test_server_error_5xx_is_retryable_4xx_is_not():
    provider = _provider(max_retries=0)

    response_500 = httpx.Response(500, request=_fake_response())
    provider._client.chat.completions.create = AsyncMock(
        side_effect=APIStatusError("server error", response=response_500, body=None)
    )
    result_500 = await provider.complete(_request())
    assert result_500.error.retryable is True

    response_400 = httpx.Response(400, request=_fake_response())
    provider._client.chat.completions.create = AsyncMock(
        side_effect=APIStatusError("bad request", response=response_400, body=None)
    )
    result_400 = await provider.complete(_request())
    assert result_400.error.retryable is False


@pytest.mark.asyncio
async def test_timeout_and_connection_errors_map_to_timeout():
    provider = _provider(max_retries=0)
    req = _fake_response()

    provider._client.chat.completions.create = AsyncMock(
        side_effect=APITimeoutError(request=req)
    )
    result = await provider.complete(_request())
    assert result.error.type.value == "timeout"

    provider._client.chat.completions.create = AsyncMock(
        side_effect=APIConnectionError(request=req)
    )
    result = await provider.complete(_request())
    assert result.error.type.value == "timeout"


@pytest.mark.asyncio
async def test_malformed_response_missing_content_is_not_retried():
    provider = _provider(max_retries=3)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse(content=None)
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "malformed_response"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_malformed_response_preserves_observed_model_usage_finish_reason():
    """Etapa 17A.1 (Objetivo A/B) — reprodução real: 'campo message.content
    vazio ou ausente', mas usage/model/finish_reason já eram reais."""
    provider = _provider(max_retries=0)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_FakeResponse(
            content=None, model="gpt-5.5-2025-06-15", prompt_tokens=140,
            completion_tokens=1024, finish_reason="length",
        )
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.model == "gpt-5.5-2025-06-15"
    assert result.usage.input_tokens == 140
    assert result.usage.output_tokens == 1024
    assert result.provider_finish_reason == "length"


def test_sdk_retry_is_disabled_on_client_construction():
    """Retry duplicado (SDK + LLMProvider) foi identificado como bug: o
    retry deve ser controlado só por LLMProvider.complete(). O SDK da
    OpenAI é configurado com max_retries=0 na construção do client."""
    with patch("app.providers.openai_provider.AsyncOpenAI") as mock_client_cls:
        OpenAIProvider(
            api_key="test-key",
            timeout_seconds=5,
            max_retries=2,
            default_model="gpt-test",
            pricing=PricingRegistry({}),
        )
        mock_client_cls.assert_called_once_with(api_key="test-key", max_retries=0)


@pytest.mark.parametrize("key", ["", "   ", "\t\n"])
@pytest.mark.asyncio
async def test_whitespace_only_or_empty_key_is_missing_no_client_no_sdk_call(key):
    with patch("app.providers.openai_provider.AsyncOpenAI") as mock_client_cls:
        provider = OpenAIProvider(
            api_key=key,
            timeout_seconds=5,
            max_retries=0,
            default_model="gpt-test",
            pricing=PricingRegistry({}),
        )
        result = await provider.complete(_request())

    mock_client_cls.assert_not_called()
    assert provider.local_prerequisite_state() == "missing"
    assert result.status == "error"
    assert result.error.type.value == "auth"
    assert result.attempts == 0


def test_present_key_reaches_the_sdk_client_unmodified():
    with patch("app.providers.openai_provider.AsyncOpenAI") as mock_client_cls:
        provider = OpenAIProvider(
            api_key=" test-key ",
            timeout_seconds=5,
            max_retries=0,
            default_model="gpt-test",
            pricing=PricingRegistry({}),
        )

    mock_client_cls.assert_called_once_with(api_key=" test-key ", max_retries=0)
    assert provider.local_prerequisite_state() == "met"
