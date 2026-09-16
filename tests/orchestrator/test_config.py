from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.orchestrator.config import (
    MAX_QUESTION_CHARACTERS,
    MAX_SOURCE_TEXT_CHARACTERS,
    QuorumPolicy,
    RunConfig,
    validate_question,
    validate_quorum_feasibility,
)


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


def test_run_config_rejects_the_positive_infinity_public_compatibility_token():
    """Historical Non-Finite Execution-Limit Public Representation V1,
    review F1 LOW (M8) -- `"positive_infinity"` é um token de
    compatibilidade OUTWARD-ONLY (`app/presentation/schemas.py`), nunca
    um formato de INPUT pra `RunConfig`/execuções novas. Este teste
    prova o invariante estruturalmente: o token nunca é analisado de
    volta como um número -- `RunConfig` rejeita a string exatamente
    como rejeitaria qualquer outra string não-numérica nestes campos."""
    with pytest.raises(ValidationError):
        _run_config(max_cost_usd="positive_infinity")
    with pytest.raises(ValidationError):
        _run_config(round_dispatch_timeout_seconds="positive_infinity")


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


# ---------------------------------------------------------------------------
# Accepted Question Size Boundary V1 -- validate_question / MAX_QUESTION_CHARACTERS
# ---------------------------------------------------------------------------


def test_validate_question_rejects_empty_string():
    with pytest.raises(ValueError, match="vazia"):
        validate_question("")


@pytest.mark.parametrize("blank", [" ", "   ", "\n", "\t", "\n\t  \n"])
def test_validate_question_rejects_whitespace_only(blank):
    with pytest.raises(ValueError, match="vazia"):
        validate_question(blank)


def test_validate_question_accepts_exactly_the_maximum():
    question = "x" * MAX_QUESTION_CHARACTERS
    assert validate_question(question) == question


def test_validate_question_rejects_one_over_the_maximum():
    question = "x" * (MAX_QUESTION_CHARACTERS + 1)
    with pytest.raises(ValueError, match="máximo"):
        validate_question(question)


def test_validate_question_accepts_exactly_the_maximum_in_multibyte_unicode():
    """Seção 7 do contrato -- o limite é contagem de caracteres Python
    (`len(str)`, code points), nunca bytes UTF-8. "🎉" é 1 caractere
    Python mas 4 bytes em UTF-8 -- MAX_QUESTION_CHARACTERS repetições
    dele têm 4x mais bytes que o limite, mas ainda passam."""
    question = "🎉" * MAX_QUESTION_CHARACTERS
    assert len(question) == MAX_QUESTION_CHARACTERS
    assert len(question.encode("utf-8")) == MAX_QUESTION_CHARACTERS * 4
    assert validate_question(question) == question


def test_validate_question_rejects_one_over_the_maximum_in_multibyte_unicode():
    question = "🎉" * (MAX_QUESTION_CHARACTERS + 1)
    with pytest.raises(ValueError, match="máximo"):
        validate_question(question)


def test_validate_question_preserves_surrounding_whitespace_verbatim():
    """Seção 6 do contrato -- espaço em branco AO REDOR de conteúdo real
    nunca é removido; só o valor INTEIRO sendo em branco é rejeitado."""
    question = "  pergunta válida  "
    assert validate_question(question) == "  pergunta válida  "


def test_validate_question_never_strips_leading_or_trailing_newlines():
    question = "\npergunta\n"
    assert validate_question(question) == "\npergunta\n"


def test_question_and_source_text_limits_are_independent_constants():
    """Seção 3/H do contrato -- MAX_QUESTION_CHARACTERS e
    MAX_SOURCE_TEXT_CHARACTERS são nomes/constantes DISTINTOS no módulo
    (nunca `MAX_QUESTION_CHARACTERS = MAX_SOURCE_TEXT_CHARACTERS`), ainda
    que numericamente iguais hoje -- mudar um dos dois no código-fonte
    nunca moveria o outro. Esta asserção de valor documenta o estado
    atual; a independência real é uma garantia de DESIGN (duas
    atribuições de módulo separadas em app/orchestrator/config.py, cada
    uma consumida por exatamente uma função de validação própria), não
    algo que um teste de runtime sozinho prove por completo."""
    assert MAX_QUESTION_CHARACTERS == 20_000
    assert MAX_SOURCE_TEXT_CHARACTERS == 20_000


def test_run_config_question_field_has_no_max_length_or_blank_after_trim_validator():
    """Accepted Question Size Boundary V1, compatibilidade histórica --
    RunConfig.question NUNCA aplica validate_question como
    field_validator: uma question > MAX_QUESTION_CHARACTERS ou
    whitespace-only construída DIRETAMENTE via RunConfig (o caminho que
    reconstrução de dado histórico usa, `RunConfig(**run_config_json)`)
    precisa continuar aceita aqui -- só as boundaries de ACEITE DE
    EXECUÇÃO NOVA (CreateRunRequest, CouncilExecutionService.run())
    aplicam a regra."""
    oversized = "x" * (MAX_QUESTION_CHARACTERS + 1)
    config = _run_config(question=oversized)
    assert config.question == oversized

    whitespace_only = "   \n\t  "
    config2 = _run_config(question=whitespace_only)
    assert config2.question == whitespace_only


# ---------------------------------------------------------------------------
# Accepted Quorum Feasibility Boundary V1 -- validate_quorum_feasibility
# (matriz A do contrato desta slice)
# ---------------------------------------------------------------------------


def test_validate_quorum_feasibility_accepts_min_to_return_below_participant_count():
    config = _run_config(
        enabled_providers=["openai", "anthropic"],
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )
    validate_quorum_feasibility(config)  # não levanta


def test_validate_quorum_feasibility_accepts_min_to_return_equal_to_participant_count():
    config = _run_config(
        enabled_providers=["openai"],
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
    )
    validate_quorum_feasibility(config)  # igualdade é válida -- não levanta


def test_validate_quorum_feasibility_rejects_min_to_return_above_participant_count():
    config = _run_config(
        enabled_providers=["openai"],
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=2),
    )
    with pytest.raises(ValueError, match="min_to_return"):
        validate_quorum_feasibility(config)


def test_validate_quorum_feasibility_ignores_min_for_debate_above_participant_count():
    """`min_for_debate` pode legitimamente exceder a contagem de
    participantes -- só significa que a crítica nunca roda, não que a
    execução seja infactível (`min_to_return` continua satisfazível)."""
    config = _run_config(
        enabled_providers=["openai"],
        quorum=QuorumPolicy(min_for_debate=5, min_to_return=1),
    )
    validate_quorum_feasibility(config)  # não levanta


def test_historical_infeasible_quorum_run_config_still_constructible():
    """Matriz F, item 28 -- um `RunConfig` com `min_to_return >
    len(enabled_providers)` (infactível pra uma execução NOVA) continua
    diretamente construível: `validate_quorum_feasibility` NUNCA é um
    `field_validator`/`model_validator` de `RunConfig`/`QuorumPolicy` --
    só uma boundary de ACEITE DE EXECUÇÃO separada (mesma disciplina de
    `test_run_config_question_field_has_no_max_length_or_blank_after_trim_validator`
    acima). Reconstrução histórica (`RunConfig(**run_config_json)`)
    precisa continuar funcionando pra uma execução aceita ANTES desta
    regra existir."""
    config = _run_config(
        enabled_providers=["openai"],
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=2),
    )
    assert config.quorum.min_to_return == 2
    assert len(config.enabled_providers) == 1
    with pytest.raises(ValueError, match="min_to_return"):
        validate_quorum_feasibility(config)


def test_validate_quorum_feasibility_uses_enabled_providers_not_all_provider_authorities():
    """A checagem é estritamente sobre `len(enabled_providers)` -- os 4
    papéis internos (claim processor/judge/editor/source analyzer) NUNCA
    entram na conta, mesmo quando são nomes de provider adicionais que
    `all_provider_authorities` incluiria.

    Repair de teste (LOW #1, review independente) -- a versão anterior
    usava `min_to_return=1` com 1 participante: isso passa tanto sob a
    implementação CORRETA (`1 <= len(enabled_providers)=1`) quanto sob
    um mutante ERRADO que comparasse contra
    `len(all_provider_authorities)=5` (`1 <= 5`) -- as duas aceitam, o
    teste não discriminava NADA entre elas. Usando `min_to_return=2`
    com 1 participante: a implementação CORRETA REJEITA
    (`2 > len(enabled_providers)=1`), enquanto o mutante errado
    ACEITARIA (`2 <= len(all_provider_authorities)=5`) -- só a versão
    que compara contra `enabled_providers` levanta aqui, então este
    teste agora falha sob esse mutante específico."""
    config = _run_config(
        enabled_providers=["openai"],
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=2),
        claim_processor_provider="anthropic",
        judge_provider="gemini",
        editor_provider="mistral",
        source_analyzer_provider="cohere",
    )
    # all_provider_authorities tem 5 nomes distintos (>= min_to_return=2)
    # -- um mutante que comparasse contra isso aceitaria erroneamente;
    # só enabled_providers (1 participante, < min_to_return=2) é usado
    # pela implementação real, que rejeita.
    assert len(config.all_provider_authorities) == 5
    with pytest.raises(ValueError, match="min_to_return"):
        validate_quorum_feasibility(config)
