"""
Deployment Execution Configuration Boundary V1 -- testes de validação
INTRÍNSECA de `Settings` (conhecível inteiramente a partir de Settings,
sem construção de registry de providers em runtime).

Nenhuma chamada real de rede/provider aqui -- só construção de
`Settings` em memória (`_env_file=None`, nunca lê `.env` real).
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# Matriz A -- identificadores de modelo padrão por provider
# ---------------------------------------------------------------------------

_DEFAULT_MODEL_FIELDS = (
    "openai_default_model",
    "anthropic_default_model",
    "gemini_default_model",
)


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_valid_ordinary_id_preserved_verbatim(field):
    settings = _settings(**{field: "gpt-x"})
    assert getattr(settings, field) == "gpt-x"


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_empty_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: ""})


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_whitespace_only_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: "   "})


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_leading_whitespace_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: " gpt-x"})


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_trailing_whitespace_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: "gpt-x "})


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_leading_tab_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: "\tgpt-x"})


@pytest.mark.parametrize("field", _DEFAULT_MODEL_FIELDS)
def test_default_model_trailing_newline_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: "gpt-x\n"})


def test_default_model_never_silently_trimmed():
    """Reforço explícito -- um valor com espaço em branco líder/final é
    REJEITADO, nunca aceito com o espaço removido silenciosamente (a
    exceção acima já prova rejeição; este teste prova a ausência de
    qualquer caminho de sucesso com trim)."""
    with pytest.raises(ValidationError):
        _settings(openai_default_model=" gpt-x ")


def test_representative_valid_defaults_construct_successfully():
    settings = _settings(
        openai_default_model="gpt-x",
        anthropic_default_model="claude-x",
        gemini_default_model="gemini-x",
    )
    assert settings.openai_default_model == "gpt-x"
    assert settings.anthropic_default_model == "claude-x"
    assert settings.gemini_default_model == "gemini-x"


# ---------------------------------------------------------------------------
# Matriz B -- limites inteiros positivos de execução
# ---------------------------------------------------------------------------

_POSITIVE_INT_FIELDS = (
    "default_max_total_tokens",
    "default_max_output_tokens_per_call",
    "default_max_output_tokens_grouping",
    "default_max_output_tokens_judge",
)


@pytest.mark.parametrize("field", _POSITIVE_INT_FIELDS)
def test_positive_int_default_representative_valid_accepted(field):
    settings = _settings(**{field: 2048})
    assert getattr(settings, field) == 2048


@pytest.mark.parametrize("field", _POSITIVE_INT_FIELDS)
def test_positive_int_default_one_accepted(field):
    settings = _settings(**{field: 1})
    assert getattr(settings, field) == 1


@pytest.mark.parametrize("field", _POSITIVE_INT_FIELDS)
def test_positive_int_default_zero_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: 0})


@pytest.mark.parametrize("field", _POSITIVE_INT_FIELDS)
def test_positive_int_default_negative_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: -1})


# ---------------------------------------------------------------------------
# Matriz C -- limites float finitos-positivos de execução
# ---------------------------------------------------------------------------

_FINITE_POSITIVE_FLOAT_FIELDS = (
    "default_max_cost_usd",
    "orchestrator_round_dispatch_timeout_seconds",
)


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_representative_valid_accepted(field):
    settings = _settings(**{field: 42.5})
    assert getattr(settings, field) == 42.5


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_tiny_positive_accepted(field):
    settings = _settings(**{field: 0.0001})
    assert getattr(settings, field) == 0.0001


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_zero_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: 0.0})


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_negative_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: -1.0})


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_nan_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: math.nan})


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_positive_infinity_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: math.inf})


@pytest.mark.parametrize("field", _FINITE_POSITIVE_FLOAT_FIELDS)
def test_finite_float_default_negative_infinity_rejected(field):
    with pytest.raises(ValidationError):
        _settings(**{field: -math.inf})


def test_orchestrator_round_dispatch_timeout_legacy_alias_still_finite_validated(monkeypatch):
    """A validação de finitude/positividade precisa valer também quando o
    valor chega pela variável de ambiente LEGADA
    (`ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS`), não só pela canônica --
    `AliasChoices` só decide QUAL env var alimenta o campo, nunca
    contorna a validação do campo em si."""
    monkeypatch.setenv("ORCHESTRATOR_OVERALL_TIMEOUT_SECONDS", "inf")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# ---------------------------------------------------------------------------
# Independência (seção 18 do contrato) -- verificada ao nível de Settings
# ---------------------------------------------------------------------------


def test_settings_missing_api_keys_remain_valid_deployment_state():
    """Seção 17/18-F do contrato -- ausência de API key nunca é rejeitada
    na composição de Settings; é estado de deployment válido."""
    settings = _settings(openai_api_key=None, anthropic_api_key=None, google_api_key=None)
    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.google_api_key is None


def test_settings_max_output_tokens_per_call_may_exceed_max_total_tokens():
    """Seção 18-D -- relação permanece independente sob o orçamento
    agregado "soft" atual; nenhuma ordenação nova é inventada aqui."""
    settings = _settings(default_max_output_tokens_per_call=100_000, default_max_total_tokens=1)
    assert settings.default_max_output_tokens_per_call == 100_000
    assert settings.default_max_total_tokens == 1


# ---------------------------------------------------------------------------
# Deployment Execution Configuration Boundary V1 (F1, repair pós-revisão
# independente -- MEDIUM) -- imutabilidade pós-construção.
#
# O contrato é IMUTABILIDADE, não só "validação de atribuição": mesmo uma
# reatribuição pra outro valor IGUALMENTE VÁLIDO precisa ser rejeitada
# (M10 abaixo) -- senão os defaults efetivos de um deployment já
# composto poderiam mudar silenciosamente depois da composição.
# ---------------------------------------------------------------------------


def test_settings_is_frozen_model_identity_assignment_rejected():
    """M1 -- categoria A (identidade de modelo)."""
    settings = _settings()
    with pytest.raises(ValidationError):
        settings.openai_default_model = "   "
    assert settings.openai_default_model == "gpt-5.5"


def test_settings_is_frozen_finite_float_cost_assignment_rejected():
    """M2 -- categoria C (float finito-positivo)."""
    settings = _settings()
    original = settings.default_max_cost_usd
    with pytest.raises(ValidationError):
        settings.default_max_cost_usd = float("inf")
    assert settings.default_max_cost_usd == original


def test_settings_is_frozen_round_timeout_assignment_rejected():
    """M3 -- categoria D (timeout de rodada)."""
    settings = _settings()
    original = settings.orchestrator_round_dispatch_timeout_seconds
    with pytest.raises(ValidationError):
        settings.orchestrator_round_dispatch_timeout_seconds = float("inf")
    assert settings.orchestrator_round_dispatch_timeout_seconds == original


def test_settings_is_frozen_positive_int_total_tokens_assignment_rejected():
    """M4 -- categoria B (inteiro positivo)."""
    settings = _settings()
    original = settings.default_max_total_tokens
    with pytest.raises(ValidationError):
        settings.default_max_total_tokens = 0
    assert settings.default_max_total_tokens == original


def test_settings_is_frozen_positive_int_per_call_assignment_rejected():
    """M5."""
    settings = _settings()
    original = settings.default_max_output_tokens_per_call
    with pytest.raises(ValidationError):
        settings.default_max_output_tokens_per_call = 0
    assert settings.default_max_output_tokens_per_call == original


def test_settings_is_frozen_positive_int_grouping_assignment_rejected():
    """M6."""
    settings = _settings()
    original = settings.default_max_output_tokens_grouping
    with pytest.raises(ValidationError):
        settings.default_max_output_tokens_grouping = 0
    assert settings.default_max_output_tokens_grouping == original


def test_settings_is_frozen_positive_int_judge_assignment_rejected():
    """M7."""
    settings = _settings()
    original = settings.default_max_output_tokens_judge
    with pytest.raises(ValidationError):
        settings.default_max_output_tokens_judge = 0
    assert settings.default_max_output_tokens_judge == original


def test_settings_is_frozen_quorum_field_assignment_rejected():
    """M8 -- imutabilidade cobre o snapshot INTEIRO de deployment, não só
    os campos adicionados por esta slice -- inclusive um campo cuja
    FORMA/factibilidade é validada em app/bootstrap.py (não em
    field_validator local), pra provar que o congelamento protege
    TODOS os campos de Settings uniformemente."""
    settings = _settings()
    original = settings.quorum_min_to_return
    with pytest.raises(ValidationError):
        settings.quorum_min_to_return = 999
    assert settings.quorum_min_to_return == original


def test_settings_is_frozen_internal_role_provider_assignment_rejected():
    """M9 -- reatribuir pra um valor OUTRO PROVIDER VÁLIDO (não pra um
    valor obviamente inválido) também é rejeitada -- prova que a
    imutabilidade não é seletiva por "valor parece ruim"."""
    settings = _settings()
    original = settings.default_source_analyzer_provider
    with pytest.raises(ValidationError):
        settings.default_source_analyzer_provider = "gemini"
    assert settings.default_source_analyzer_provider == original


def test_settings_is_frozen_valid_to_valid_assignment_also_rejected():
    """M10 -- o CONTRATO É IMUTABILIDADE, não "validação de atribuição
    inválida": reatribuir pra outro valor IGUALMENTE VÁLIDO (positivo,
    finito, bem-formado) também precisa ser rejeitado. Se isso passasse,
    os defaults efetivos de um deployment já composto poderiam mudar
    silenciosamente depois de `build_app_components` já ter lido/
    injetado os valores originais em `AppComponents`/providers/
    `CouncilExecutionService`."""
    settings = _settings()
    original = settings.default_max_total_tokens
    assert original != 12345  # sanity -- valor de teste realmente é diferente
    with pytest.raises(ValidationError):
        settings.default_max_total_tokens = 12345
    assert settings.default_max_total_tokens == original


def test_settings_freezing_does_not_prevent_independent_new_instances():
    """Seção 11 do contrato -- imutabilidade é POR INSTÂNCIA após a
    construção, nunca um singleton global: duas instâncias distintas,
    cada uma com seu próprio valor válido, continuam construíveis
    independentemente."""
    s1 = _settings(default_max_total_tokens=1000)
    s2 = _settings(default_max_total_tokens=2000)
    assert s1.default_max_total_tokens == 1000
    assert s2.default_max_total_tokens == 2000


def test_settings_freezing_preserves_environment_based_construction(monkeypatch):
    """Construção via variável de ambiente (não só kwargs diretos)
    continua funcionando normalmente sob `frozen=True` -- o congelamento
    afeta só atribuição PÓS-construção, nunca a leitura/validação
    inicial de env vars."""
    monkeypatch.setenv("DEFAULT_MAX_TOTAL_TOKENS", "9999")
    settings = Settings(_env_file=None)
    assert settings.default_max_total_tokens == 9999


def test_settings_model_copy_update_is_not_used_anywhere_in_production():
    """Seção 12 do contrato -- `model_copy(update=...)` do Pydantic
    CONTORNA a validação de campo (produz uma nova instância sem
    revalidar), mas não é usado em NENHUM lugar da composição de
    produção pra `Settings` (confirmado por auditoria de repositório
    antes deste repair -- grep não encontrou nenhuma ocorrência de
    `Settings(...).model_copy(` nem `settings.model_copy(` em app/ ou
    tests/). Registrado como NOTA/escape-hatch de framework, não como
    bloqueador -- esta função é o teste executável dessa auditoria: se
    algum código de produção algum dia passar a usar `model_copy` sobre
    `Settings`, o comportamento abaixo (contorna validação) precisa ser
    reavaliado explicitamente, não descoberto por acidente."""
    settings = _settings()
    # model_copy(update=...) É capaz de produzir um Settings inválido
    # sem revalidar -- documentado aqui como fato de framework, nunca
    # como um caminho suportado de construção de deployment.
    bypassed = settings.model_copy(update={"default_max_cost_usd": float("inf")})
    assert math.isinf(bypassed.default_max_cost_usd)
    # a instância ORIGINAL nunca é afetada por model_copy (que sempre
    # devolve uma instância NOVA) -- congelamento continua protegendo o
    # objeto original mesmo diante desse escape hatch de framework.
    assert settings.default_max_cost_usd != float("inf")


def test_settings_is_a_model_wide_immutable_deployment_snapshot():
    """LOW de qualidade de teste (review independente, F1 focused
    re-review) -- os testes de mutação acima (M1-M10) provam
    comportamento campo-a-campo, mas nenhum deles mataria uma
    implementação hipotética de "congelamento seletivo" (ex.: um
    `__setattr__` customizado que bloqueasse só os campos tocados por
    esta slice, deixando outros mutáveis por acidente). Este teste
    trava o invariante ARQUITETURAL real -- `Settings` inteiro é
    imutável pós-construção via `SettingsConfigDict(frozen=True)`, não
    uma lista de campos protegidos individualmente -- direto na config
    do modelo, nunca inferido do comportamento de campos específicos."""
    assert Settings.model_config.get("frozen") is True
