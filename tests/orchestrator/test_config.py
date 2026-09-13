from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.orchestrator.config import QuorumPolicy, RunConfig


# ---------------------------------------------------------------------------
# QuorumPolicy
# ---------------------------------------------------------------------------


def test_quorum_policy_valid_construction():
    policy = QuorumPolicy(min_for_debate=2, min_to_return=1)
    assert policy.min_for_debate == 2
    assert policy.min_to_return == 1


def test_quorum_policy_rejects_min_to_return_greater_than_min_for_debate():
    with pytest.raises(ValidationError, match="min_to_return"):
        QuorumPolicy(min_for_debate=1, min_to_return=2)


def test_quorum_policy_allows_equal_thresholds():
    # min_to_return == min_for_debate é um caso limite válido (ex.: nenhum
    # provider "opcional" — se não bater o mínimo pra debate, também não
    # bate o mínimo pra retornar).
    policy = QuorumPolicy(min_for_debate=2, min_to_return=2)
    assert policy.min_for_debate == policy.min_to_return == 2


def test_quorum_policy_is_frozen():
    policy = QuorumPolicy(min_for_debate=2, min_to_return=1)
    with pytest.raises(ValidationError):
        policy.min_for_debate = 3  # type: ignore[misc]


def test_quorum_policy_from_settings_uses_settings_values():
    """Única fonte de verdade: QuorumPolicy não tem defaults próprios —
    os valores vêm sempre de Settings."""
    settings = Settings(quorum_min_for_debate=3, quorum_min_to_return=2)
    policy = QuorumPolicy.from_settings(settings)
    assert policy.min_for_debate == 3
    assert policy.min_to_return == 2


def test_quorum_policy_has_no_independent_defaults():
    """Prova de que não existem dois valores que podem divergir: os campos
    de QuorumPolicy são obrigatórios (sem default próprio), então a única
    forma de obter uma instância "padrão" é via from_settings ou
    explicitamente — nunca por acidente com um número diferente do
    Settings."""
    with pytest.raises(ValidationError):
        QuorumPolicy()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# RunConfig
# ---------------------------------------------------------------------------


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Qual a capital do Brasil?",
        enabled_providers=["openai", "anthropic", "gemini"],
        max_cost_usd=1.0,
        max_total_tokens=50_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        round_dispatch_timeout_seconds=120.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)


def test_run_config_valid_construction():
    config = _run_config()
    assert config.enabled_providers == ("openai", "anthropic", "gemini")


def test_run_config_enabled_providers_is_tuple_and_immutable():
    """Etapa 10: enabled_providers virou tuple — não pode ser mutado
    in-place por ninguém, fechando a brecha de mutabilidade estrutural."""
    config = _run_config()
    assert isinstance(config.enabled_providers, tuple)
    assert not hasattr(config.enabled_providers, "append")


def test_run_config_accepts_list_input_and_normalizes_to_tuple():
    config = _run_config(enabled_providers=["openai", "anthropic"])
    assert config.enabled_providers == ("openai", "anthropic")
    assert isinstance(config.enabled_providers, tuple)


def test_run_config_is_frozen():
    config = _run_config()
    with pytest.raises(ValidationError):
        config.max_cost_usd = 5.0  # type: ignore[misc]


def test_run_config_rejects_duplicate_providers():
    with pytest.raises(ValidationError, match="enabled_providers"):
        _run_config(enabled_providers=["openai", "openai"])


def test_run_config_rejects_empty_providers():
    with pytest.raises(ValidationError):
        _run_config(enabled_providers=[])


def test_run_config_rejects_empty_question():
    with pytest.raises(ValidationError):
        _run_config(question="")


def test_run_config_no_hardcoded_provider_count():
    """O total de providers nunca é '3' fixo — RunConfig aceita qualquer
    quantidade >= 1, provando que não há constante escondida."""
    config_one = _run_config(enabled_providers=["openai"])
    config_five = _run_config(
        enabled_providers=["openai", "anthropic", "gemini", "mistral", "grok"]
    )
    assert len(config_one.enabled_providers) == 1
    assert len(config_five.enabled_providers) == 5


def test_run_config_from_settings_builds_quorum_from_same_source():
    settings = Settings(
        default_max_cost_usd=2.5,
        default_max_total_tokens=10_000,
        default_max_output_tokens_per_call=2048,
        default_max_output_tokens_grouping=4096,
        default_max_output_tokens_judge=6144,
        quorum_min_for_debate=2,
        quorum_min_to_return=1,
        orchestrator_round_dispatch_timeout_seconds=90.0,
        default_claim_processor_provider="gemini",
        default_judge_provider="openai",
        default_editor_provider="gemini",
    )
    config = RunConfig.from_settings(
        settings, question="pergunta de teste", enabled_providers=["openai", "anthropic"]
    )
    assert config.max_cost_usd == 2.5
    assert config.max_total_tokens == 10_000
    assert config.max_output_tokens_per_call == 2048
    assert config.max_output_tokens_grouping == 4096
    assert config.max_output_tokens_judge == 6144
    assert config.quorum.min_for_debate == 2
    assert config.quorum.min_to_return == 1
    assert config.round_dispatch_timeout_seconds == 90.0
    assert config.claim_processor_provider == "gemini"
    assert config.judge_provider == "openai"
    assert config.editor_provider == "gemini"


# ---------------------------------------------------------------------------
# Clarificação de contrato de execução (pós-run real): renomeado de
# `overall_timeout_seconds`/`ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS` --
# compatibilidade de entrada preservada onde já é uso real (variável de
# ambiente/.env), nunca reinterpretando o VALOR, só aceitando o nome
# antigo como alias de leitura.
# ---------------------------------------------------------------------------


def test_run_config_rejects_non_positive_round_dispatch_timeout_seconds():
    with pytest.raises(ValidationError):
        _run_config(round_dispatch_timeout_seconds=0)
    with pytest.raises(ValidationError):
        _run_config(round_dispatch_timeout_seconds=-5.0)


def test_settings_reads_round_dispatch_timeout_seconds_from_canonical_env_var(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_ROUND_DISPATCH_TIMEOUT_SECONDS", "42.0")
    settings = Settings(_env_file=None)
    assert settings.orchestrator_round_dispatch_timeout_seconds == 42.0


def test_settings_still_reads_legacy_env_var_name_as_compatibility_alias(monkeypatch):
    """Um .env já em produção com ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS
    não pode silenciosamente parar de funcionar -- essa é a variável de
    ambiente real, não só um nome de campo Python interno."""
    monkeypatch.setenv("ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS", "77.0")
    settings = Settings(_env_file=None)
    assert settings.orchestrator_round_dispatch_timeout_seconds == 77.0


def test_settings_canonical_env_var_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS", "11.0")
    monkeypatch.setenv("ORCHESTRATOR_ROUND_DISPATCH_TIMEOUT_SECONDS", "22.0")
    settings = Settings(_env_file=None)
    assert settings.orchestrator_round_dispatch_timeout_seconds == 22.0


def test_settings_default_unchanged_when_neither_env_var_is_set(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_ROUND_DISPATCH_TIMEOUT_SECONDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.orchestrator_round_dispatch_timeout_seconds == 120.0


def test_run_config_max_total_tokens_and_max_output_tokens_per_call_are_independent():
    """Os dois conceitos de token são campos distintos: mudar um não
    afeta o outro, e nenhum valida contra o outro (não há relação de
    ordem entre eles — um orçamento agregado pode legitimamente ser
    menor OU maior que o teto de uma única chamada)."""
    config = _run_config(max_total_tokens=500, max_output_tokens_per_call=2000)
    assert config.max_total_tokens == 500
    assert config.max_output_tokens_per_call == 2000


def test_run_config_rejects_non_positive_max_total_tokens():
    with pytest.raises(ValidationError):
        _run_config(max_total_tokens=0)


def test_run_config_rejects_non_positive_max_output_tokens_per_call():
    with pytest.raises(ValidationError):
        _run_config(max_output_tokens_per_call=0)


# ---------------------------------------------------------------------------
# Etapa 17A.1 (Objetivo C) — novo default de max_output_tokens_per_call
# ---------------------------------------------------------------------------


def test_settings_default_max_output_tokens_per_call_is_4096():
    """1024 provou-se insuficiente em Runs reais (truncamento visível de
    resposta de participante e do Judge). Trava explícita do novo
    default -- se alguém reduzir de novo por engano, este teste falha."""
    settings = Settings(_env_file=None)
    assert settings.default_max_output_tokens_per_call == 4096


def test_run_config_override_still_works_with_new_default():
    """O default subiu, mas continua sendo só isso -- um default. Um
    RunConfig específico ainda pode pedir qualquer valor via
    RunConfig.from_settings, sem nenhum subsistema novo de alocação por
    fase."""
    settings = Settings(_env_file=None, default_max_output_tokens_per_call=999)
    config = RunConfig.from_settings(settings, question="pergunta", enabled_providers=["openai"])
    assert config.max_output_tokens_per_call == 999


# ---------------------------------------------------------------------------
# Etapa 17A.2 — tetos próprios de agrupamento/Judge
# ---------------------------------------------------------------------------


def test_settings_default_max_output_tokens_grouping_and_judge_are_8192():
    """Investigação confirmou amplificação estrutural de escala:
    agrupamento e Judge têm output com cobertura obrigatória (uma
    entrada por claim), então compartilhar o teto de uma chamada O(1)
    (extração/crítica) os deixava mais propensos a truncamento. Trava
    explícita dos novos defaults -- se alguém reduzir por engano, este
    teste falha."""
    settings = Settings(_env_file=None)
    assert settings.default_max_output_tokens_grouping == 8192
    assert settings.default_max_output_tokens_judge == 8192


def test_run_config_rejects_missing_max_output_tokens_grouping():
    """Sem default próprio (mesma disciplina de max_cost_usd/
    max_total_tokens/max_output_tokens_per_call) -- omitir o campo
    inteiramente precisa falhar, não silenciosamente herdar um número
    de algum outro campo."""
    fields = dict(
        question="pergunta",
        enabled_providers=["openai"],
        max_cost_usd=1.0,
        max_total_tokens=50_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        round_dispatch_timeout_seconds=60.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    with pytest.raises(ValidationError, match="max_output_tokens_grouping"):
        RunConfig(**fields)


def test_run_config_rejects_non_positive_max_output_tokens_grouping():
    with pytest.raises(ValidationError):
        _run_config(max_output_tokens_grouping=0)


def test_run_config_rejects_non_positive_max_output_tokens_judge():
    with pytest.raises(ValidationError):
        _run_config(max_output_tokens_judge=0)


def test_run_config_grouping_judge_and_general_ceilings_are_independent():
    """Os três conceitos de teto de output por chamada são campos
    distintos: mudar um não afeta os outros, e nenhum valida contra o
    outro -- mesma disciplina de max_total_tokens vs.
    max_output_tokens_per_call (Etapa 4)."""
    config = _run_config(
        max_output_tokens_per_call=500,
        max_output_tokens_grouping=4000,
        max_output_tokens_judge=9000,
    )
    assert config.max_output_tokens_per_call == 500
    assert config.max_output_tokens_grouping == 4000
    assert config.max_output_tokens_judge == 9000


def test_run_config_from_settings_grouping_and_judge_default_to_8192():
    settings = Settings(_env_file=None)
    config = RunConfig.from_settings(settings, question="pergunta", enabled_providers=["openai"])
    assert config.max_output_tokens_grouping == 8192
    assert config.max_output_tokens_judge == 8192


# ---------------------------------------------------------------------------
# T02.4 repair (achado MEDIUM da revisão independente) --
# all_provider_authorities
# ---------------------------------------------------------------------------


def test_all_provider_authorities_unions_participants_and_all_4_internal_roles():
    """Enumeração canônica: os 4 papéis internos + enabled_providers,
    sem duplicatas (frozenset), mesmo quando um papel interno coincide
    com um participante."""
    config = _run_config(
        enabled_providers=["openai", "gemini"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="cohere",
        source_analyzer_provider="mistral",
    )
    assert config.all_provider_authorities == {
        "openai",
        "gemini",
        "anthropic",
        "cohere",
        "mistral",
    }


def test_all_provider_authorities_reflects_source_analyzer_provider():
    """Achado da revisão independente: `source_analyzer_provider`
    especificamente precisa aparecer na enumeração -- era o campo que
    escapava da validação de bootstrap antes deste repair."""
    config = _run_config(source_analyzer_provider="provider-exclusivo-da-fonte")
    assert "provider-exclusivo-da-fonte" in config.all_provider_authorities


def test_all_provider_authorities_is_never_in_model_dump():
    """Precisa ser @property, nunca @computed_field -- senão apareceria
    em model_dump(mode="json") e quebraria a reconstrução de
    RunConfig(**data) a partir do blob persistido (extra="forbid")."""
    config = _run_config()
    assert "all_provider_authorities" not in config.model_dump(mode="json")
    # reconstrução a partir do próprio dump precisa continuar funcionando
    RunConfig(**config.model_dump(mode="json"))
