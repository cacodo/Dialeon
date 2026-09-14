"""
Testes de `ProviderExecutionPolicy` (T02.2) -- validação de campo e
resolução a partir de `Settings`.

Nenhuma chamada real de rede/provider aqui -- só o modelo Pydantic e a
resolução a partir de `Settings` construído em memória.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# Teste A -- validação de campo / Settings
# ---------------------------------------------------------------------------


def test_attempt_timeout_seconds_positive_accepted():
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=1.0, max_transport_attempts_per_completion=1
    )
    assert policy.attempt_timeout_seconds == 1.0


def test_attempt_timeout_seconds_zero_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=0.0, max_transport_attempts_per_completion=1
        )


def test_attempt_timeout_seconds_negative_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=-5.0, max_transport_attempts_per_completion=1
        )


def test_max_transport_attempts_one_accepted():
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=1.0, max_transport_attempts_per_completion=1
    )
    assert policy.max_transport_attempts_per_completion == 1


def test_max_transport_attempts_zero_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=1.0, max_transport_attempts_per_completion=0
        )


def test_max_transport_attempts_negative_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=1.0, max_transport_attempts_per_completion=-1
        )


def test_from_settings_provider_timeout_seconds_positive_accepted():
    policy = ProviderExecutionPolicy.from_settings(_settings(provider_timeout_seconds=10))
    assert policy.attempt_timeout_seconds == 10.0


def test_from_settings_provider_timeout_seconds_zero_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy.from_settings(_settings(provider_timeout_seconds=0))


def test_from_settings_provider_timeout_seconds_negative_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy.from_settings(_settings(provider_timeout_seconds=-1))


def test_from_settings_provider_max_retries_zero_accepted():
    policy = ProviderExecutionPolicy.from_settings(_settings(provider_max_retries=0))
    assert policy.max_transport_attempts_per_completion == 1


def test_from_settings_provider_max_retries_negative_rejected():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy.from_settings(_settings(provider_max_retries=-1))


# ---------------------------------------------------------------------------
# Teste B -- resolução provider_max_retries -> max_transport_attempts_per_completion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider_max_retries,expected_max_transport_attempts",
    [(0, 1), (1, 2), (2, 3)],
)
def test_from_settings_resolves_max_retries_plus_one(
    provider_max_retries, expected_max_transport_attempts
):
    policy = ProviderExecutionPolicy.from_settings(
        _settings(provider_max_retries=provider_max_retries)
    )
    assert policy.max_transport_attempts_per_completion == expected_max_transport_attempts


def test_from_settings_never_persists_max_retries_as_the_canonical_field():
    """O contrato T02.2 exige explicitamente que "max retries" nunca
    seja o nome do campo persistido/canônico -- só
    `max_transport_attempts_per_completion` existe no modelo."""
    assert "max_retries" not in ProviderExecutionPolicy.model_fields
    assert "max_transport_attempts_per_completion" in ProviderExecutionPolicy.model_fields


# ---------------------------------------------------------------------------
# Teste Q -- segurança: serialização contém SÓ os dois campos numéricos
# ---------------------------------------------------------------------------


def test_serialized_policy_contains_only_the_two_approved_fields():
    policy = ProviderExecutionPolicy.from_settings(_settings())
    dumped = policy.model_dump(mode="json")

    assert set(dumped.keys()) == {
        "attempt_timeout_seconds",
        "max_transport_attempts_per_completion",
    }


def test_serialized_policy_never_leaks_secrets_or_identifiers():
    settings = _settings(
        openai_api_key="sk-should-never-appear-in-policy",
        anthropic_api_key="sk-ant-should-never-appear-either",
    )
    policy = ProviderExecutionPolicy.from_settings(settings)
    dumped_text = policy.model_dump_json()

    for forbidden in (
        "sk-should-never-appear-in-policy",
        "sk-ant-should-never-appear-either",
        "openai",
        "anthropic",
        "gemini",
        "api_key",
        "endpoint",
    ):
        assert forbidden not in dumped_text


def test_policy_extra_fields_forbidden():
    """`extra="forbid"` -- ninguém pode acrescentar um campo novo (ex.:
    backoff, jitter, timeout nativo do SDK) sem atualizar o modelo
    explicitamente."""
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=1.0,
            max_transport_attempts_per_completion=1,
            backoff_base_seconds=0.5,
        )


def test_policy_is_frozen():
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=1.0, max_transport_attempts_per_completion=1
    )
    with pytest.raises(ValidationError):
        policy.attempt_timeout_seconds = 2.0


# ---------------------------------------------------------------------------
# Teste R -- regressão: contadores de retry ESTRUTURADO permanecem
# distintos de max_transport_attempts_per_completion (nunca renomeados/
# colapsados por este slice).
# ---------------------------------------------------------------------------


def test_structured_retry_attempt_number_fields_are_untouched_and_distinct():
    """`ClaimProcessingAttempt`/`SourceAnalysisAttempt`/`JudgeAttempt`/
    `EditorAttempt` continuam com `attempt_number` -- um contador
    inteiramente distinto de `max_transport_attempts_per_completion`
    (transporte normalizado). Este teste apenas confirma que os 4 tipos
    de domínio continuam existindo com esse campo, sem qualquer campo
    novo de "policy" vazando pra dentro deles (T02.2 nunca tocou esses
    módulos)."""
    from app.debate.processing_record import ClaimProcessingAttempt
    from app.editor.attempt import EditorAttempt
    from app.judge.attempt import JudgeAttempt
    from app.source_analysis.attempt import SourceAnalysisAttempt

    for cls in (ClaimProcessingAttempt, JudgeAttempt, EditorAttempt, SourceAnalysisAttempt):
        assert "attempt_number" in cls.model_fields
        assert "max_transport_attempts_per_completion" not in cls.model_fields
        assert "attempt_timeout_seconds" not in cls.model_fields
