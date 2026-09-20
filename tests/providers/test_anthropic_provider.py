from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from anthropic import APIStatusError, AuthenticationError, RateLimitError

from app.models.provider_models import CompletionRequest, Message, ModelIdentitySource
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.pricing import PricingRegistry


def _provider(max_retries: int = 0) -> AnthropicProvider:
    provider = AnthropicProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=max_retries,
        default_model="claude-test",
        pricing=PricingRegistry({}),
    )
    provider._client = AsyncMock()
    return provider


def _request(model: str | None = None) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="Explique TCP em uma frase.")], model=model
    )


def _fake_request():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeThinkingBlock:
    """Repair (Run02 claim-extraction exhaustion) -- bloco de tipo
    diferente de "text" (ex.: extended thinking), SEM atributo `.text`
    -- prova que `_parse_response` ignora blocos não-text ao montar
    `text`, mesmo quando eles aparecem MISTURADOS com blocos de texto
    reais na mesma resposta."""

    type = "thinking"

    def __init__(self, thinking):
        self.thinking = thinking


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, blocks, model="claude-test", input_tokens=8, output_tokens=4, stop_reason=None):
        self.content = blocks
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.model = model
        self.stop_reason = stop_reason


@pytest.mark.asyncio
async def test_successful_completion_is_normalized():
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse([_FakeTextBlock("TCP garante entrega confiável.")])
    )

    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.text == "TCP garante entrega confiável."
    assert result.provider == "anthropic"
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 4


# ---------------------------------------------------------------------------
# Provenance de identidade de modelo (ModelIdentitySource) -- B.3/B.4 do
# contrato desta slice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_reports_model_marks_provider_reported():
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse(
            [_FakeTextBlock("TCP garante entrega confiável.")], model="claude-sonnet-5-2026-01-15"
        )
    )

    result = await provider.complete(_request(model="claude-sonnet-5"))

    assert result.requested_model == "claude-sonnet-5"
    assert result.model == "claude-sonnet-5-2026-01-15"
    assert result.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_provider_omits_model_marks_requested_fallback():
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse([_FakeTextBlock("TCP garante entrega confiável.")], model=None)
    )

    result = await provider.complete(_request(model="claude-sonnet-5"))

    assert result.requested_model == "claude-sonnet-5"
    assert result.model == "claude-sonnet-5"
    assert result.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_multiple_text_blocks_are_concatenated():
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse([_FakeTextBlock("Parte 1. "), _FakeTextBlock("Parte 2.")])
    )

    result = await provider.complete(_request())

    assert result.text == "Parte 1. Parte 2."


@pytest.mark.asyncio
async def test_mixed_non_text_and_text_blocks_extracts_only_text_and_preserves_usage():
    """Repair (Run02 claim-extraction exhaustion) -- G/"Anthropic mixed
    non-text + text behavior": um bloco não-text (ex.: thinking)
    misturado com um bloco de texto real precisa ser ignorado na
    extração de `text` (só o bloco de texto entra), mas usage/model/
    finish_reason continuam corretamente contabilizados a partir do
    objeto de resposta real -- nada é perdido/subestimado por causa do
    bloco extra."""
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse(
            [_FakeThinkingBlock("raciocínio interno, nunca visível"), _FakeTextBlock("Resposta final.")],
            input_tokens=50,
            output_tokens=200,
            stop_reason="end_turn",
        )
    )

    result = await provider.complete(_request())

    assert result.status == "success"
    assert result.text == "Resposta final."
    assert result.usage.input_tokens == 50
    assert result.usage.output_tokens == 200
    assert result.provider_finish_reason == "end_turn"


@pytest.mark.asyncio
async def test_non_text_only_blocks_with_max_tokens_is_malformed_but_preserves_usage():
    """Repair (Run02 claim-extraction exhaustion) -- G/"não-text-only +
    max_tokens preserva usage/cost/finish_reason": um output composto
    INTEIRAMENTE de blocos não-text (ex.: só thinking, sem nenhum texto
    visível ainda) cortado por `max_tokens` precisa virar
    `status="error"`/`malformed_response`, mas SEM descartar
    usage/model/finish_reason já observados -- é exatamente a SHAPE real
    reportada em Run02 (chamadas atingindo o teto de output sem JSON
    visível)."""
    provider = _provider(max_retries=0)
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse(
            [_FakeThinkingBlock("raciocínio que consumiu o teto inteiro")],
            model="claude-sonnet-5-20260115",
            input_tokens=80,
            output_tokens=4096,
            stop_reason="max_tokens",
        )
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "malformed_response"
    assert result.model == "claude-sonnet-5-20260115"
    assert result.usage.input_tokens == 80
    assert result.usage.output_tokens == 4096
    assert result.provider_finish_reason == "max_tokens"


@pytest.mark.asyncio
async def test_minimal_reasoning_maps_to_thinking_disabled():
    """Repair (Run02 claim-extraction exhaustion) -- único mapeamento
    concreto de `CompletionRequest.minimal_reasoning` neste
    repositório: `thinking={"type": "disabled"}`, o valor exato suportado
    pelo SDK instalado (nunca uma aproximação "minimal" quando
    "disabled" já é suportado)."""
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse([_FakeTextBlock("ok")])
    )

    request = CompletionRequest(
        messages=[Message(role="user", content="pergunta")], minimal_reasoning=True
    )
    await provider.complete(request)

    call_kwargs = provider._client.messages.create.call_args.kwargs
    assert call_kwargs["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_default_request_never_sends_thinking_parameter():
    """Repair (Run02 claim-extraction exhaustion) -- `minimal_reasoning`
    default (`False`, toda chamada existente antes deste campo existir)
    NUNCA envia `thinking` ao SDK -- comportamento byte-idêntico ao de
    antes deste repair pra participante/crítica/SourceAnalyzer/Editor (e pra
    qualquer request que não peça raciocínio mínimo; o Judge o pede
    explicitamente desde judge_v2 -- ver tests/judge/test_judge_reasoning_policy.py)."""
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse([_FakeTextBlock("ok")])
    )

    await provider.complete(_request())

    call_kwargs = provider._client.messages.create.call_args.kwargs
    assert "thinking" not in call_kwargs


@pytest.mark.asyncio
async def test_authentication_error_maps_to_auth_and_is_not_retried():
    provider = _provider(max_retries=3)
    response = httpx.Response(401, request=_fake_request())
    provider._client.messages.create = AsyncMock(
        side_effect=AuthenticationError("invalid key", response=response, body=None)
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "auth"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_rate_limit_is_retryable():
    provider = _provider(max_retries=1)
    response = httpx.Response(429, request=_fake_request())
    provider._client.messages.create = AsyncMock(
        side_effect=RateLimitError("rate limited", response=response, body=None)
    )

    result = await provider.complete(_request())

    assert result.error.type.value == "rate_limit"
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_server_error_5xx_is_retryable():
    provider = _provider(max_retries=0)
    response = httpx.Response(529, request=_fake_request())  # 529 = overloaded, específico da Anthropic
    provider._client.messages.create = AsyncMock(
        side_effect=APIStatusError("overloaded", response=response, body=None)
    )

    result = await provider.complete(_request())

    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_malformed_response_no_text_block_is_not_retried():
    provider = _provider(max_retries=3)
    provider._client.messages.create = AsyncMock(return_value=_FakeResponse([]))

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.error.type.value == "malformed_response"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_malformed_response_preserves_observed_model_usage_finish_reason():
    """Etapa 17A.1 (Objetivo A/B) — reprodução real: transporte com
    sucesso (HTTP 200), `content` sem bloco de texto -- mas `usage`/
    `model`/`stop_reason` já eram reais no objeto de resposta. Nada
    disso pode ser descartado só porque o texto não pôde ser extraído."""
    provider = _provider(max_retries=0)
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse(
            [], model="claude-sonnet-5-20250601", input_tokens=120, output_tokens=1024,
            stop_reason="max_tokens",
        )
    )

    result = await provider.complete(_request())

    assert result.status == "error"
    assert result.model == "claude-sonnet-5-20250601"  # nunca cai pro requested_model
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 1024
    assert result.provider_finish_reason == "max_tokens"


def test_sdk_retry_is_disabled_on_client_construction():
    with patch("app.providers.anthropic_provider.AsyncAnthropic") as mock_client_cls:
        AnthropicProvider(
            api_key="test-key",
            timeout_seconds=5,
            max_retries=2,
            default_model="claude-test",
            pricing=PricingRegistry({}),
        )
        mock_client_cls.assert_called_once_with(api_key="test-key", max_retries=0)


@pytest.mark.asyncio
async def test_max_output_tokens_per_call_reaches_correct_anthropic_parameter():
    """Etapa 17A.1 (requisito J) — max_output_tokens_per_call chega ao
    SDK real como `max_tokens` (parâmetro nativo da Anthropic, distinto
    do `max_completion_tokens` da OpenAI)."""
    provider = _provider()
    provider._client.messages.create = AsyncMock(
        return_value=_FakeResponse([_FakeTextBlock("ok")])
    )

    request = CompletionRequest(
        messages=[Message(role="user", content="pergunta")], max_tokens=4096
    )
    await provider.complete(request)

    call_kwargs = provider._client.messages.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == 4096
