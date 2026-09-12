"""
Constrói instâncias de LLMProvider a partir das Settings (app/config.py).

Isto é só wiring — não decide quais providers usar em uma execução, não
sabe o que é uma "rodada" ou um "debate". Essas decisões pertencem ao
Orchestrator, que ainda não existe (etapa futura). Aqui existe só para
não espalhar `OpenAIProvider(settings.openai_api_key, ...)` pelo código
toda vez que algo precisar de um provider.
"""

from __future__ import annotations

from app.config import Settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.pricing import DEFAULT_PRICING_REGISTRY


def build_all_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Retorna todos os providers do MVP, independente de terem API key ou não.

    Um provider sem API key configurada ainda é construído normalmente —
    ele só vai falhar (com ProviderAuthError, tratado como erro comum,
    accounting conhecido-zero — ver Etapa 9) na primeira chamada. Isso é
    intencional: decidir "não usar esse provider" é decisão do chamador
    (Orchestrator/RunConfig.enabled_providers), não algo que a factory
    deva esconder.

    Todos os 3 providers compartilham a MESMA instância de
    `DEFAULT_PRICING_REGISTRY` (imutável — ver app/providers/pricing.py),
    não um registry novo por provider.
    """
    return {
        "openai": OpenAIProvider(
            api_key=settings.openai_api_key,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            default_model=settings.openai_default_model,
            pricing=DEFAULT_PRICING_REGISTRY,
        ),
        "anthropic": AnthropicProvider(
            api_key=settings.anthropic_api_key,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            default_model=settings.anthropic_default_model,
            pricing=DEFAULT_PRICING_REGISTRY,
        ),
        "gemini": GeminiProvider(
            api_key=settings.google_api_key,
            timeout_seconds=settings.provider_timeout_seconds,
            max_retries=settings.provider_max_retries,
            default_model=settings.gemini_default_model,
            pricing=DEFAULT_PRICING_REGISTRY,
        ),
    }
