"""
Testes de integração: fazem chamadas REAIS às APIs dos 3 providers.

Nunca rodam por padrão (ver `addopts = "-m 'not integration'"` em
pyproject.toml). Para executar:

    pytest -m integration

Cada teste individual ainda é pulado (skip) se a API key correspondente
não estiver no ambiente — então mesmo rodando com `-m integration`, só os
providers com chave configurada são de fato exercitados.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models.provider_models import CompletionRequest, Message
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider

pytestmark = pytest.mark.integration

_settings = Settings()


def _request() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="Responda em uma palavra: qual a capital da França?")],
        max_tokens=20,
    )


@pytest.mark.skipif(not _settings.openai_api_key, reason="OPENAI_API_KEY não configurada")
async def test_openai_real_call():
    provider = OpenAIProvider(
        api_key=_settings.openai_api_key,
        timeout_seconds=_settings.provider_timeout_seconds,
        max_retries=_settings.provider_max_retries,
        default_model=_settings.openai_default_model,
    )
    result = await provider.complete(_request())
    assert result.status == "success", result.error
    assert "Paris" in result.text


@pytest.mark.skipif(not _settings.anthropic_api_key, reason="ANTHROPIC_API_KEY não configurada")
async def test_anthropic_real_call():
    provider = AnthropicProvider(
        api_key=_settings.anthropic_api_key,
        timeout_seconds=_settings.provider_timeout_seconds,
        max_retries=_settings.provider_max_retries,
        default_model=_settings.anthropic_default_model,
    )
    result = await provider.complete(_request())
    assert result.status == "success", result.error
    assert "Paris" in result.text


@pytest.mark.skipif(not _settings.google_api_key, reason="GOOGLE_API_KEY não configurada")
async def test_gemini_real_call():
    provider = GeminiProvider(
        api_key=_settings.google_api_key,
        timeout_seconds=_settings.provider_timeout_seconds,
        max_retries=_settings.provider_max_retries,
        default_model=_settings.gemini_default_model,
    )
    result = await provider.complete(_request())
    assert result.status == "success", result.error
    assert "Paris" in result.text
