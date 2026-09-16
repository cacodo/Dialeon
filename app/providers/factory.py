"""
Constrói instâncias de LLMProvider a partir das Settings (app/config.py).

Isto é só wiring — não decide quais providers usar em uma execução, não
sabe o que é uma "rodada" ou um "debate". Essas decisões pertencem ao
Orchestrator (app/orchestrator/orchestrator.py), construído internamente
por `DebateEngine` a partir do registry retornado aqui (ver
app/bootstrap.py). Aqui existe só para não espalhar
`OpenAIProvider(settings.openai_api_key, ...)` pelo código toda vez que
algo precisar de um provider.
"""

from __future__ import annotations

from app.config import Settings
from app.models.provider_models import (
    ProviderExecutionPolicy,
    validate_provider_execution_policy_for_new_execution,
)
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.pricing import DEFAULT_PRICING_REGISTRY


def build_all_providers(
    settings: Settings, provider_execution_policy: ProviderExecutionPolicy
) -> dict[str, LLMProvider]:
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

    T02.2: `provider_execution_policy` é recebido JÁ RESOLVIDO pelo
    chamador (`app/bootstrap.py`, via `ProviderExecutionPolicy.from_settings`
    uma única vez) -- esta função NUNCA lê
    `settings.provider_timeout_seconds`/`settings.provider_max_retries`
    diretamente, exatamente pra garantir que os 3 providers e o
    `CouncilExecutionService` (que recebe a MESMA instância de
    `provider_execution_policy` em `build_app_components`) derivem do
    ÚNICO mesmo objeto resolvido, nunca de duas leituras independentes
    de `Settings` que pudessem divergir.

    Fractional-timeout fidelity repair (achado MEDIUM da revisão
    independente): `attempt_timeout_seconds` é repassado LOSSLESSLY,
    como `float`, direto pra `LLMProvider.__init__`/`asyncio.wait_for`
    (`app/providers/base.py`, também `float` agora) -- nunca truncado
    via `int(...)`. O domínio de `ProviderExecutionPolicy.attempt_timeout_seconds`
    é `float > 0` (não só valores inteiros vindos de `Settings` hoje),
    então persisted == enforced precisa valer pra QUALQUER valor válido
    (ex.: 1.5, 0.9, 0.1), não só pros inteiros que `Settings` produz
    atualmente -- um `int(...)` aqui truncava 0.9s->0s silenciosamente,
    violando esse invariante. `max_transport_attempts_per_completion - 1`
    continua desfazendo exatamente o `+ 1` que `from_settings` aplicou
    -- conversão inalterada, já aprovada independentemente.

    Provider Execution Policy Finite New-Execution Boundary V1 --
    `validate_provider_execution_policy_for_new_execution` roda ANTES
    de qualquer `LLMProvider` ser construído -- defesa em profundidade
    pra chamadores diretos desta função (testes, código interno) que
    não passaram por `app/bootstrap.py`, que já resolve a política via
    `ProviderExecutionPolicy.from_settings` (garantidamente finita/
    positiva). Um `policy` externamente construído com
    `attempt_timeout_seconds` não-finito/não-positivo nunca produz
    providers "usáveis" com esse timeout -- a validação FALHA antes."""
    validate_provider_execution_policy_for_new_execution(provider_execution_policy)

    timeout_seconds = provider_execution_policy.attempt_timeout_seconds
    max_retries = provider_execution_policy.max_transport_attempts_per_completion - 1
    return {
        "openai": OpenAIProvider(
            api_key=settings.openai_api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_model=settings.openai_default_model,
            pricing=DEFAULT_PRICING_REGISTRY,
        ),
        "anthropic": AnthropicProvider(
            api_key=settings.anthropic_api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_model=settings.anthropic_default_model,
            pricing=DEFAULT_PRICING_REGISTRY,
        ),
        "gemini": GeminiProvider(
            api_key=settings.google_api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_model=settings.gemini_default_model,
            pricing=DEFAULT_PRICING_REGISTRY,
        ),
    }
