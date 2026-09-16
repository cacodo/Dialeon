"""
Testes de `ProviderExecutionPolicy` (T02.2) -- validação de campo e
resolução a partir de `Settings`.

Nenhuma chamada real de rede/provider aqui -- só o modelo Pydantic e a
resolução a partir de `Settings` construído em memória.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models.provider_models import (
    ProviderExecutionPolicy,
    validate_provider_execution_policy_for_new_execution,
)


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


# ---------------------------------------------------------------------------
# Provider Execution Policy Finite New-Execution Boundary V1 --
# validate_provider_execution_policy_for_new_execution (matriz B1-B10, B19)
# ---------------------------------------------------------------------------


def test_b1_positive_finite_policy_accepted_for_new_execution():
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=30.0, max_transport_attempts_per_completion=3
    )
    validate_provider_execution_policy_for_new_execution(policy)  # não levanta


def test_b2_positive_infinity_rejected_for_new_execution():
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=float("inf"), max_transport_attempts_per_completion=3
    )
    with pytest.raises(ValueError):
        validate_provider_execution_policy_for_new_execution(policy)


def test_b3_negative_infinity_construction_itself_already_fails_closed():
    """`-inf` já é rejeitado na PRÓPRIA construção de `ProviderExecutionPolicy`
    (`Field(gt=0)`: `-inf > 0` é False) -- nem chega a existir uma
    instância pra passar pro validador de execução nova."""
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=float("-inf"), max_transport_attempts_per_completion=3
        )


def test_b4_nan_construction_itself_already_fails_closed():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=float("nan"), max_transport_attempts_per_completion=3
        )


def test_b5_zero_construction_itself_already_fails_closed():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=0.0, max_transport_attempts_per_completion=3
        )


def test_b6_negative_finite_construction_itself_already_fails_closed():
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds=-5.0, max_transport_attempts_per_completion=3
        )


def test_b7_retry_count_semantics_unchanged_by_the_new_validator():
    """A validação nova olha SÓ `attempt_timeout_seconds` -- `validate_provider_execution_policy_for_new_execution`
    não tem opinião sobre `max_transport_attempts_per_completion`, e o
    valor sobrevive inalterado depois de passar pela validação."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=15.0, max_transport_attempts_per_completion=5
    )
    validate_provider_execution_policy_for_new_execution(policy)
    assert policy.max_transport_attempts_per_completion == 5


def test_b8_direct_positive_infinity_construction_remains_possible():
    """CONSTRUÍVEL != AUTORIZADO PRA EXECUÇÃO NOVA -- `ProviderExecutionPolicy`
    em si continua aceitando `+inf` diretamente (necessário pra
    reconstrução histórica fiel, ver B9/B10 abaixo); só a boundary de
    execução NOVA (`validate_provider_execution_policy_for_new_execution`)
    rejeita."""
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=float("inf"), max_transport_attempts_per_completion=3
    )
    assert math.isinf(policy.attempt_timeout_seconds)


def test_b19_positive_infinity_token_is_not_accepted_as_policy_input():
    """O token público `"positive_infinity"` (Historical Non-Finite
    Execution-Limit Public Representation V1) NUNCA é um formato de
    INPUT -- `ProviderExecutionPolicy.attempt_timeout_seconds` é `float`
    puro; a string não é coercível e a construção falha."""
    with pytest.raises(ValidationError):
        ProviderExecutionPolicy(
            attempt_timeout_seconds="positive_infinity",
            max_transport_attempts_per_completion=3,
        )
