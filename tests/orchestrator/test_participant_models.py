"""Council Accepted Effective Participant Model Choice V1 -- regra única de
resolução, validação da entrada e o mapa congelado no `RunConfig`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.orchestrator.config import RunConfig
from app.orchestrator.participant_models import (
    MAX_MODEL_IDENTIFIER_CHARACTERS,
    ParticipantModelChoice,
    resolve_participant_models,
    validate_participant_model_overrides,
)
from tests.storage.fixtures import run_config

DEFAULTS = {"openai": "gpt-default", "anthropic": "claude-default", "gemini": "gemini-default"}


def _choices(overrides=None, participants=("openai", "anthropic")):
    return resolve_participant_models(participants, overrides, DEFAULTS)


def test_explicit_override_wins_over_the_configured_default():
    [openai, anthropic] = _choices({"openai": "gpt-explicit"})

    assert openai == ParticipantModelChoice(provider="openai", requested_model="gpt-explicit", origin="run_override")
    assert anthropic == ParticipantModelChoice(
        provider="anthropic", requested_model="claude-default", origin="configured_default"
    )


def test_without_overrides_every_participant_gets_its_configured_default():
    assert _choices(None) == _choices({}) == (
        ParticipantModelChoice(provider="openai", requested_model="gpt-default", origin="configured_default"),
        ParticipantModelChoice(provider="anthropic", requested_model="claude-default", origin="configured_default"),
    )


def test_choosing_the_default_value_explicitly_is_still_recorded_as_a_run_override():
    [openai, _] = _choices({"openai": "gpt-default"})

    assert (openai.requested_model, openai.origin) == ("gpt-default", "run_override")


def test_the_map_follows_the_selection_order():
    choices = resolve_participant_models(("gemini", "openai"), {"openai": "x"}, DEFAULTS)

    assert [c.provider for c in choices] == ["gemini", "openai"]


def test_the_same_identifier_under_two_providers_stays_two_provider_scoped_requests():
    choices = resolve_participant_models(("openai", "gemini"), {"openai": "shared-id", "gemini": "shared-id"}, DEFAULTS)

    assert [(c.provider, c.requested_model) for c in choices] == [("openai", "shared-id"), ("gemini", "shared-id")]
    assert choices[0] != choices[1]


def test_an_override_for_an_unselected_provider_is_rejected():
    with pytest.raises(ValueError, match="não selecionado"):
        _choices({"gemini": "gemini-x"})


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        " gpt-x",
        "gpt-x ",
        "gpt x",
        "gpt\tx",
        "gpt-x\n",
        "gpt\x00x",
        "g" * (MAX_MODEL_IDENTIFIER_CHARACTERS + 1),
    ],
)
def test_malformed_identifiers_are_rejected_never_rewritten(value):
    with pytest.raises(ValueError):
        validate_participant_model_overrides({"openai": value}, ["openai"])


@pytest.mark.parametrize(
    "char, category",
    [
        ("\x80", "Cc"),  # C1
        ("\x85", "Cc"),  # C1 (NEL)
        ("\x9b", "Cc"),  # C1 (CSI)
        ("\x9f", "Cc"),  # C1
        ("\u202e", "Cf"),  # RIGHT-TO-LEFT OVERRIDE
        ("\u2066", "Cf"),  # LEFT-TO-RIGHT ISOLATE
        ("\u200b", "Cf"),  # ZERO WIDTH SPACE
        ("\u200d", "Cf"),  # ZERO WIDTH JOINER
        ("\u00ad", "Cf"),  # SOFT HYPHEN
        ("\ufeff", "Cf"),  # BOM / ZERO WIDTH NO-BREAK SPACE
        ("\u00a0", "Zs"),  # NO-BREAK SPACE
        ("\u2028", "Zl"),  # LINE SEPARATOR
        ("\u2029", "Zp"),  # PARAGRAPH SEPARATOR
        ("\ue000", "Co"),  # uso privado
        ("\u0378", "Cn"),  # não atribuído
    ],
)
@pytest.mark.parametrize("position", ["middle", "end"])
def test_invisible_control_and_format_characters_are_rejected(char, category, position):
    value = f"gpt{char}x" if position == "middle" else f"gpt-x{char}"

    with pytest.raises(ValueError) as caught:
        validate_participant_model_overrides({"openai": value}, ["openai"])

    message = str(caught.value)
    assert char not in message  # nunca ecoado cru
    if position == "middle":
        assert f"U+{ord(char):04X}, {category}" in message
    # no fim, um caractere de espaço (Zs/Zl/Zp, NEL) cai antes na regra comum
    # de espaço líder/final -- rejeitado do mesmo jeito


@pytest.mark.parametrize(
    "char, category",
    [
        # Default_Ignorable_Code_Point fora das categorias Cc/Cf/Cs/Co/Cn/Z*
        ("\u034f", "Mn"),  # COMBINING GRAPHEME JOINER
        ("\ufe0f", "Mn"),  # VARIATION SELECTOR-16
        ("\U000e0100", "Mn"),  # VARIATION SELECTOR-17
        ("\u17b4", "Mn"),  # KHMER VOWEL INHERENT AQ
        ("\u180b", "Mn"),  # MONGOLIAN FREE VARIATION SELECTOR ONE
        ("\u180f", "Mn"),  # MONGOLIAN FREE VARIATION SELECTOR FOUR
        ("\u115f", "Lo"),  # HANGUL CHOSEONG FILLER
        ("\u3164", "Lo"),  # HANGUL FILLER
        ("\uffa0", "Lo"),  # HALFWIDTH HANGUL FILLER
    ],
)
def test_default_ignorable_code_points_are_rejected_even_outside_the_blocked_categories(char, category):
    with pytest.raises(ValueError) as caught:
        validate_participant_model_overrides({"openai": f"gpt{char}x"}, ["openai"])

    assert f"U+{ord(char):04X}, {category}" in str(caught.value)


def test_default_ignorable_table_is_the_unicode_16_property():
    """A tabela é a propriedade `Default_Ignorable_Code_Point` completa do
    Unicode 16.0.0 (conferida contra a enumeração do motor de regex do Perl
    5.42, que embute o mesmo Unicode 16.0.0): intervalos ordenados, sem
    sobreposição nem adjacência, 4174 code points, só de categorias
    Cf/Cn/Mn/Lo."""
    import unicodedata

    from app.orchestrator.participant_models import _DEFAULT_IGNORABLE_CODE_POINT_RANGES as ranges

    assert all(start <= end for start, end in ranges)
    assert all(a_end + 1 < b_start for (_, a_end), (b_start, _) in zip(ranges, ranges[1:]))
    assert sum(end - start + 1 for start, end in ranges) == 4174
    if unicodedata.unidata_version == "16.0.0":
        categories = {unicodedata.category(chr(c)) for start, end in ranges for c in range(start, end + 1)}
        assert categories == {"Cf", "Cn", "Mn", "Lo"}


@pytest.mark.parametrize(
    "value",
    [
        "mode\u0301le",  # combinação visível (acento agudo combinante): aceita, sem normalizar
        "e\u0308\u0301-model",
    ],
)
def test_visible_combining_marks_are_accepted_verbatim(value):
    assert validate_participant_model_overrides({"openai": value}, ["openai"]) == {"openai": value}


def test_a_stored_choice_with_an_ignorable_character_stays_readable():
    """Leitura histórica continua leniente: a regra estrita vale só pra
    entrada nova, nunca reinterpreta o que já foi aceito e gravado."""
    stored = run_config().model_dump(mode="json")
    stored["participant_models"] = [
        {"provider": "openai", "requested_model": "gpt\u034fx", "origin": "run_override"},
        {"provider": "anthropic", "requested_model": "claude-default", "origin": "configured_default"},
    ]

    rc = RunConfig(**stored)

    assert rc.requested_model_for("openai") == "gpt\u034fx"


@pytest.mark.parametrize(
    "value",
    [
        "gpt-5.5",
        "models/gemini-3.7-pro",
        "claude-sonnet-5@20260901",
        "g" * MAX_MODEL_IDENTIFIER_CHARACTERS,
        # não ASCII, mas visível: letras/marcas/números/símbolos de qualquer escrita
        "modèle-é",
        "模型-1",
        "model_x:v2+beta~1",
    ],
)
def test_well_formed_identifiers_are_accepted_verbatim(value):
    assert validate_participant_model_overrides({"openai": value}, ["openai"]) == {"openai": value}


def test_a_participant_without_a_known_configured_default_is_a_programming_error():
    with pytest.raises(ValueError, match="sem modelo padrão"):
        resolve_participant_models(("openai", "mistral"), None, DEFAULTS)


# ---------------------------------------------------------------------------
# RunConfig: mapa congelado e leitura histórica
# ---------------------------------------------------------------------------


def test_run_config_freezes_the_complete_effective_map_and_round_trips():
    rc = run_config().with_participant_models(_choices({"openai": "gpt-explicit"}))

    assert rc.requested_model_for("openai") == "gpt-explicit"
    assert rc.requested_model_for("anthropic") == "claude-default"
    assert RunConfig(**rc.model_dump(mode="json")) == rc


def test_legacy_run_config_without_the_map_reads_as_not_captured():
    legacy = run_config().model_dump(mode="json")
    legacy.pop("participant_models")

    rc = RunConfig(**legacy)

    assert rc.participant_models is None
    assert rc.requested_model_for("openai") is None  # nunca reconstruído do padrão atual


@pytest.mark.parametrize(
    "choices",
    [
        # faltando um participante
        (ParticipantModelChoice(provider="openai", requested_model="a", origin="configured_default"),),
        # participante a mais
        (
            ParticipantModelChoice(provider="openai", requested_model="a", origin="configured_default"),
            ParticipantModelChoice(provider="anthropic", requested_model="b", origin="configured_default"),
            ParticipantModelChoice(provider="gemini", requested_model="c", origin="configured_default"),
        ),
        # ordem diferente da seleção
        (
            ParticipantModelChoice(provider="anthropic", requested_model="b", origin="configured_default"),
            ParticipantModelChoice(provider="openai", requested_model="a", origin="configured_default"),
        ),
    ],
)
def test_run_config_rejects_a_map_that_is_not_exactly_one_model_per_participant(choices):
    with pytest.raises(ValidationError):
        run_config().with_participant_models(choices)


# ---------------------------------------------------------------------------
# Contrato de request: modelo explícito entra no digest (v2); v1 intocado
# ---------------------------------------------------------------------------


def test_explicit_participant_model_is_part_of_the_request_and_its_digest():
    from app.debate.context import build_critique_requests
    from app.models.request_provenance import compute_request_digest
    from app.orchestrator.orchestrator import _build_initial_request

    legacy = _build_initial_request("q", 100)
    x = _build_initial_request("q", 100, "model-x")
    y = _build_initial_request("q", 100, "model-y")

    assert legacy.model is None  # initial_response_v1: sem modelo, como sempre
    assert (x.model, y.model) == ("model-x", "model-y")
    assert len({compute_request_digest(r) for r in (legacy, x, y)}) == 3

    critique = build_critique_requests("q", [], ["openai", "gemini"], 100, requested_models={"openai": "a", "gemini": "b"})
    assert {p: r.model for p, r in critique.items()} == {"openai": "a", "gemini": "b"}
    assert {r.model for r in build_critique_requests("q", [], ["openai"], 100).values()} == {None}
