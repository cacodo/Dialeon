"""
Historical Non-Finite Execution-Limit Public Representation V1.

Testa a conversão canônica ÚNICA `run_config_public`/
`_positive_execution_limit_public` (app/presentation/mappers.py) e o
tipo público `RunConfigPublic`/`PositiveExecutionLimitPublic`
(app/presentation/schemas.py) -- nenhuma chamada de rede/provider,
apenas construção de objetos em memória.
"""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from app.presentation.mappers import _positive_execution_limit_public, run_config_public
from app.presentation.schemas import CreateRunRequest, QuorumPublic, RunConfigPublic
from tests.storage.fixtures import run_config


def _quorum_public(**overrides) -> QuorumPublic:
    fields = dict(min_for_debate=1, min_to_return=1)
    fields.update(overrides)
    return QuorumPublic(**fields)


def _run_config_public_kwargs(**overrides) -> dict:
    fields = dict(
        question="q",
        enabled_providers=["openai"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
        source_text=None,
        max_cost_usd=1.0,
        max_total_tokens=1000,
        max_output_tokens_per_call=100,
        max_output_tokens_grouping=100,
        max_output_tokens_judge=100,
        round_dispatch_timeout_seconds=10.0,
        quorum=_quorum_public(),
    )
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# T1-T4 -- conversão via mapper canônico (run_config_public)
# ---------------------------------------------------------------------------


def test_t1_finite_cost_maps_to_numeric_public_value():
    rc = run_config(max_cost_usd=2.5)
    public = run_config_public(rc)
    assert public.max_cost_usd == 2.5
    assert isinstance(public.max_cost_usd, float)


def test_t2_infinite_cost_maps_to_positive_infinity_token():
    rc = run_config(max_cost_usd=float("inf"))
    public = run_config_public(rc)
    assert public.max_cost_usd == "positive_infinity"


def test_t3_finite_round_timeout_maps_to_numeric_public_value():
    rc = run_config(round_dispatch_timeout_seconds=42.0)
    public = run_config_public(rc)
    assert public.round_dispatch_timeout_seconds == 42.0
    assert isinstance(public.round_dispatch_timeout_seconds, float)


def test_t4_infinite_round_timeout_maps_to_positive_infinity_token():
    rc = run_config(round_dispatch_timeout_seconds=float("inf"))
    public = run_config_public(rc)
    assert public.round_dispatch_timeout_seconds == "positive_infinity"


# ---------------------------------------------------------------------------
# T5 -- +inf nunca colapsa em null
# ---------------------------------------------------------------------------


def test_t5_infinite_cost_is_never_null():
    rc = run_config(max_cost_usd=float("inf"))
    public = run_config_public(rc)
    assert public.max_cost_usd is not None
    assert public.max_cost_usd == "positive_infinity"

    dumped = public.model_dump(mode="json")
    assert dumped["max_cost_usd"] == "positive_infinity"
    assert dumped["max_cost_usd"] is not None


def test_t5_infinite_round_timeout_is_never_null():
    rc = run_config(round_dispatch_timeout_seconds=float("inf"))
    public = run_config_public(rc)
    dumped = public.model_dump(mode="json")
    assert dumped["round_dispatch_timeout_seconds"] == "positive_infinity"
    assert dumped["round_dispatch_timeout_seconds"] is not None


# ---------------------------------------------------------------------------
# T6 -- +inf bruto não sobrevive dentro do schema público
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["max_cost_usd", "round_dispatch_timeout_seconds"])
@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan")])
def test_t6_raw_non_finite_float_rejected_directly_by_public_schema(field, bad_value):
    """O ÚNICO caminho suportado pro token é a conversão explícita do
    mapper -- construir `RunConfigPublic` diretamente com um float não-
    finito (mesmo +inf) é rejeitado; `RunConfigPublic` nunca aceita um
    float cru não-finito, só o token de string já convertido."""
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(**{field: bad_value}))


def test_t6_arbitrary_string_other_than_the_exact_token_rejected():
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(max_cost_usd="unlimited"))
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(max_cost_usd="Infinity"))
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(max_cost_usd="inf"))


# ---------------------------------------------------------------------------
# T7/T8 -- fail-closed pra NaN/-inf no MAPPER (nunca alcançável por um
# RunConfig real, mas a função precisa recusar deterministicamente)
# ---------------------------------------------------------------------------


def test_t7_mapper_fails_closed_on_nan():
    with pytest.raises(ValueError):
        _positive_execution_limit_public(float("nan"))


def test_t8_mapper_fails_closed_on_negative_infinity():
    with pytest.raises(ValueError):
        _positive_execution_limit_public(float("-inf"))


def test_mapper_fails_closed_on_zero():
    """Review F2 note -- endurecimento defensivo de fronteira: mesmo que
    `RunConfig.max_cost_usd`/`round_dispatch_timeout_seconds`
    (`Field(gt=0)`) já excluam `0` na origem, a função precisa ser
    INTERNAMENTE fiel ao próprio contrato "positive execution limit" --
    nunca deixar `0` passar como se fosse um limite de execução válido
    só porque `math.isfinite(0.0)` é True."""
    with pytest.raises(ValueError):
        _positive_execution_limit_public(0.0)


def test_mapper_fails_closed_on_representative_negative_finite_value():
    """Idem acima, pro lado negativo finito (ex.: -5.0) -- mesma
    disciplina."""
    with pytest.raises(ValueError):
        _positive_execution_limit_public(-5.0)


def test_mapper_never_labels_nan_or_negative_infinity_as_positive_infinity():
    """Reforço explícito da distinção de section 4 do contrato -- NaN/
    -inf nunca produzem o token `"positive_infinity"`; a única saída
    possível pra eles é uma exceção, nunca um valor de retorno."""
    for bad in (float("nan"), float("-inf")):
        with pytest.raises(ValueError):
            _positive_execution_limit_public(bad)


# ---------------------------------------------------------------------------
# Não-regressão -- valores finitos permanecem numéricos, campos não
# relacionados permanecem inalterados.
# ---------------------------------------------------------------------------


def test_finite_values_never_become_strings_end_to_end():
    rc = run_config(max_cost_usd=1.0, round_dispatch_timeout_seconds=120.0)
    public = run_config_public(rc)
    dumped = public.model_dump(mode="json")
    assert isinstance(dumped["max_cost_usd"], float)
    assert isinstance(dumped["round_dispatch_timeout_seconds"], float)
    json.dumps(dumped, allow_nan=False)  # nenhum Infinity/NaN sobrevive


def test_unrelated_numeric_fields_remain_plain_int_never_weakened():
    rc = run_config()
    public = run_config_public(rc)
    assert isinstance(public.max_total_tokens, int)
    assert isinstance(public.max_output_tokens_per_call, int)
    assert isinstance(public.max_output_tokens_grouping, int)
    assert isinstance(public.max_output_tokens_judge, int)


def test_source_text_null_semantics_unaffected_by_this_slice():
    """T17 (contraparte de null genuíno) -- `source_text=None` continua
    significando "nenhuma fonte fornecida", nunca confundido com
    "+inf histórico" de um campo totalmente diferente."""
    rc = run_config(source_text=None)
    public = run_config_public(rc)
    assert public.source_text is None


def test_run_config_public_max_cost_usd_and_round_timeout_are_not_nullable():
    """Seção 17 do contrato -- nenhum dos dois campos historicamente
    afetados se tornou nullable pra "resolver" a serialização; ambos
    continuam obrigatórios, com o valor histórico +inf representado
    pelo token, nunca por None."""
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(max_cost_usd=None))
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(round_dispatch_timeout_seconds=None))


# ---------------------------------------------------------------------------
# Review F1 LOW (M8) -- o token nunca pode ser contrabandeado como
# configuração de execução-limite via o request público de criação de
# Run. `CreateRunRequest` nem sequer TEM um campo de execution-limit
# (`extra="forbid"`, model_config compartilhado) -- então basta provar
# que tentar introduzir um não é aceito, sem inventar um campo novo só
# pra testar rejeição.
# ---------------------------------------------------------------------------


def test_create_run_request_cannot_smuggle_execution_limit_configuration():
    """`CreateRunRequest` (app/presentation/schemas.py) só aceita
    question/enabled_providers/source_text -- todo o resto da
    configuração de execução vem exclusivamente de Settings via
    `RunConfig.from_settings`. `extra="forbid"` já garante isso
    estruturalmente; este teste documenta e trava o invariante contra
    QUALQUER tentativa futura de aceitar o token de compatibilidade (ou
    qualquer limite de execução) como input de criação de Run."""
    with pytest.raises(ValidationError):
        CreateRunRequest(
            question="pergunta",
            enabled_providers=["openai"],
            max_cost_usd="positive_infinity",
        )
    with pytest.raises(ValidationError):
        CreateRunRequest(
            question="pergunta",
            enabled_providers=["openai"],
            round_dispatch_timeout_seconds="positive_infinity",
        )


# ---------------------------------------------------------------------------
# Review F1 LOW (M12) -- um campo público numérico NÃO relacionado
# (`max_total_tokens`) precisa continuar aceitando SÓ número --
# provando que o union `PositiveExecutionLimitPublic` não vazou pra
# nenhum outro campo por acidente.
# ---------------------------------------------------------------------------


def test_unrelated_numeric_field_max_total_tokens_rejects_the_compatibility_token():
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(max_total_tokens="positive_infinity"))
    with pytest.raises(ValidationError):
        RunConfigPublic(**_run_config_public_kwargs(max_total_tokens="unlimited"))
