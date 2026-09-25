"""
Testes de `build_all_providers` (T02.2) -- autoridade única de
`ProviderExecutionPolicy`.

Nenhuma chamada real de rede -- só inspeciona os atributos internos
(`_timeout_seconds`/`_max_retries`) dos `LLMProvider` construídos.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from app.providers.factory import build_all_providers


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# Teste C -- autoridade única: os 3 providers derivam do MESMO objeto
# ProviderExecutionPolicy resolvido, nunca de uma leitura própria de
# Settings.
# ---------------------------------------------------------------------------


def test_all_providers_derive_timeout_and_retries_from_the_resolved_policy():
    settings = _settings()
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=42.0, max_transport_attempts_per_completion=5
    )

    providers = build_all_providers(settings, policy)

    assert set(providers) == {"openai", "anthropic", "gemini"}
    for name, provider in providers.items():
        assert provider._timeout_seconds == 42, name
        assert provider._max_retries == 4, name  # max_transport_attempts_per_completion - 1


def test_providers_ignore_settings_timeout_retry_fields_entirely():
    """Prova mecânica de "uma única resolução, nunca duas leituras
    independentes que possam divergir": `settings.provider_timeout_seconds`/
    `settings.provider_max_retries` são deliberadamente configurados com
    valores DIFERENTES do `policy` resolvido -- se a factory os lesse
    (em vez do `policy` recebido), este teste falharia."""
    settings = _settings(provider_timeout_seconds=999, provider_max_retries=999)
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=7.0, max_transport_attempts_per_completion=2
    )

    providers = build_all_providers(settings, policy)

    for name, provider in providers.items():
        assert provider._timeout_seconds == 7, name
        assert provider._max_retries == 1, name


# ---------------------------------------------------------------------------
# Fractional-timeout fidelity repair (achado MEDIUM da revisão
# independente) -- persisted policy == enforced timeout, pra QUALQUER
# attempt_timeout_seconds válido, não só inteiros. Antes deste repair,
# `build_all_providers` aplicava `int(...)`: 1.5->1, 0.9->0, 0.1->0
# (timeout zero silencioso). Estes testes cruzam o caminho REAL de
# propagação de produção (build_all_providers), não só o objeto
# ProviderExecutionPolicy isolado.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attempt_timeout_seconds", [60.0, 1.5, 0.9, 0.1], ids=["60.0", "1.5", "0.9", "0.1"]
)
def test_fractional_attempt_timeout_propagates_losslessly_to_every_provider(
    attempt_timeout_seconds,
):
    """Prova, pro caminho de produção completo (ProviderExecutionPolicy
    -> build_all_providers -> cada LLMProvider concreto), que o valor
    persistido/resolvido chega EXATO (mesmo objeto float, sem
    truncamento/arredondamento/clamping) a `provider._timeout_seconds`
    -- pros 3 providers atuais (openai/anthropic/gemini), não só um."""
    settings = _settings()
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=attempt_timeout_seconds,
        max_transport_attempts_per_completion=3,
    )

    providers = build_all_providers(settings, policy)

    assert set(providers) == {"openai", "anthropic", "gemini"}
    for name, provider in providers.items():
        assert provider._timeout_seconds == attempt_timeout_seconds, name
        assert isinstance(provider._timeout_seconds, float), name
        # nunca truncado silenciosamente pra 0 -- especificamente o bug
        # que 0.9/0.1 reproduziam antes do repair (int(0.9) == int(0.1) == 0).
        assert provider._timeout_seconds > 0, name


def test_fractional_timeout_construction_succeeds_for_every_valid_value():
    """`build_all_providers` não levanta nem falha silenciosamente pra
    nenhum dos 4 valores exigidos -- construção bem-sucedida é, ela
    mesma, parte do invariante (item 6 do contrato do repair)."""
    settings = _settings()
    for value in (60.0, 1.5, 0.9, 0.1):
        policy = ProviderExecutionPolicy(
            attempt_timeout_seconds=value, max_transport_attempts_per_completion=3
        )
        providers = build_all_providers(settings, policy)
        assert len(providers) == 3


def test_build_all_providers_never_calls_provider_execution_policy_from_settings():
    """`build_all_providers` recebe o `policy` JÁ resolvido -- nunca
    chama `ProviderExecutionPolicy.from_settings` internamente (essa
    chamada mora EXCLUSIVAMENTE em `app/bootstrap.py`, uma única vez por
    aplicação composta)."""
    import inspect

    import app.providers.factory as factory_module

    source = inspect.getsource(factory_module)
    assert "ProviderExecutionPolicy.from_settings(" not in source


# ---------------------------------------------------------------------------
# Provider Execution Policy Finite New-Execution Boundary V1 -- B11
# ---------------------------------------------------------------------------


def test_b11_build_all_providers_rejects_infinite_supplied_policy():
    """Um `policy` externamente construído com `attempt_timeout_seconds=+inf`
    (CONSTRUÍVEL, mas não AUTORIZADO pra execução nova) nunca produz
    providers "usáveis" com esse timeout -- a rejeição acontece ANTES
    de qualquer `LLMProvider` ser instanciado."""
    settings = _settings()
    bad_policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=float("inf"), max_transport_attempts_per_completion=3
    )

    with pytest.raises(ValueError):
        build_all_providers(settings, bad_policy)


# ---------------------------------------------------------------------------
# Judge Transport Execution Policy V1 -- o override do Judge NUNCA vaza pra
# os providers construídos, e o SDK segue sem retry próprio.
# ---------------------------------------------------------------------------


def _real_settings_with_keys() -> Settings:
    return _settings(
        anthropic_api_key="k-anthropic", openai_api_key="k-openai", google_api_key="k-google"
    )


def test_built_providers_keep_the_default_policy_never_the_judge_override():
    """Todo provider construído (usado por participantes, extração,
    agrupamento/reconciliação, Source Analysis, Editor E Judge) tem como
    default o `ProviderExecutionPolicy` de topo (60s / 3 tentativas) --
    `judge_override` (120s / 1) só chega ao Judge via
    `complete(execution_policy=...)`, nunca como default de instância."""
    settings = _real_settings_with_keys()
    policy = ProviderExecutionPolicy.from_settings(settings)
    assert policy.judge_override is not None  # o override EXISTE no snapshot

    providers = build_all_providers(settings, policy)

    for name, provider in providers.items():
        assert provider._timeout_seconds == 60.0, name
        assert provider._max_retries == 2, name


def test_sdk_native_retries_stay_disabled_for_every_provider():
    """Retry é responsabilidade ÚNICA de `LLMProvider.complete()` --
    nenhum SDK pode ter retry próprio (nested retries fariam o número
    real de tentativas de transporte divergir do que a policy declara)."""
    settings = _real_settings_with_keys()
    providers = build_all_providers(settings, ProviderExecutionPolicy.from_settings(settings))

    assert providers["anthropic"]._client.max_retries == 0
    assert providers["openai"]._client.max_retries == 0
    gemini_retry = providers["gemini"]._client._api_client._http_options.retry_options
    assert gemini_retry.attempts == 1


@pytest.mark.asyncio
async def test_judge_override_completion_leaves_sdk_retry_config_untouched():
    """Uma `complete(execution_policy=override)` real (com `_call_api`
    stubado, sem rede) não altera a configuração de retry do SDK."""
    from app.models.provider_models import (
        CompletionRequest,
        Message,
        TokenUsage,
        TransportAttemptPolicy,
    )

    settings = _real_settings_with_keys()
    providers = build_all_providers(settings, ProviderExecutionPolicy.from_settings(settings))
    anthropic = providers["anthropic"]

    async def _fake_call_api(request):
        return ("ok", TokenUsage(input_tokens=1, output_tokens=1), "claude-sonnet-5", "end_turn")

    anthropic._call_api = _fake_call_api  # type: ignore[method-assign]
    override = TransportAttemptPolicy(
        attempt_timeout_seconds=120.0, max_transport_attempts_per_completion=1
    )
    response = await anthropic.complete(
        CompletionRequest(messages=[Message(role="user", content="oi")]),
        execution_policy=override,
    )

    assert response.status == "success"
    assert anthropic._client.max_retries == 0
    assert anthropic._timeout_seconds == 60.0
    assert anthropic._max_retries == 2


# ---------------------------------------------------------------------------
# Pré-requisitos locais dos 3 adapters reais, a partir do Settings resolvido
# (sem rede: só construção).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "openai_key, anthropic_key, google_key, expected",
    [
        ("sk-o", "sk-a", "g", {"openai": "met", "anthropic": "met", "gemini": "met"}),
        (None, "", "   ", {"openai": "missing", "anthropic": "missing", "gemini": "missing"}),
        ("sk-o", "\t", None, {"openai": "met", "anthropic": "missing", "gemini": "missing"}),
    ],
)
def test_real_adapters_report_local_prerequisites_from_resolved_settings(
    openai_key, anthropic_key, google_key, expected
):
    settings = _settings(openai_api_key=openai_key, anthropic_api_key=anthropic_key, google_api_key=google_key)
    policy = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)

    providers = build_all_providers(settings, policy)

    assert {name: p.local_prerequisite_state() for name, p in providers.items()} == expected
    for name, state in expected.items():
        # o cliente do SDK só existe quando há credencial -- mesma decisão
        assert (providers[name]._client is not None) == (state == "met"), name
