"""Contrato de complexidade de fragmentos de auditoria (repair M2).

Nenhum teste aqui depende de em qual profundidade o Python lançaria
`RecursionError`: o contrato é da aplicação e dá o mesmo resultado em
qualquer runtime.
"""

from __future__ import annotations

import json
import math
from enum import IntEnum

import pytest

from app.audit_fragment import (
    MAX_AUDIT_FRAGMENT_DEPTH,
    MAX_AUDIT_FRAGMENT_INT,
    MAX_AUDIT_FRAGMENT_NODES,
    MIN_AUDIT_FRAGMENT_INT,
    audit_fragment_violation,
    bound_audit_fragment,
    json_text_exceeds_depth,
)


def _nested_list(depth: int) -> list:
    value: list = []
    for _ in range(depth - 1):
        value = [value]
    return value


def _nested_dict(depth: int) -> dict:
    value: dict = {}
    for _ in range(depth - 1):
        value = {"k": value}
    return value


def _nested_mixed(depth: int) -> object:
    value: object = []
    for level in range(depth - 1):
        value = {"k": value} if level % 2 else [value]
    return value


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, -7, 2**62, 1.5, "", "texto", [], {}, {"kind": "arithmetic", "left": "1"}],
    ids=repr,
)
def test_scalars_and_normal_objects_fit(value):
    assert audit_fragment_violation(value) is None
    assert bound_audit_fragment(value) == (value, None)


@pytest.mark.parametrize("build", [_nested_list, _nested_dict, _nested_mixed])
def test_depth_limit_is_exact(build):
    assert audit_fragment_violation(build(MAX_AUDIT_FRAGMENT_DEPTH)) is None
    assert audit_fragment_violation(build(MAX_AUDIT_FRAGMENT_DEPTH + 1)) == "complexity_limit_exceeded"


@pytest.mark.parametrize("build", [_nested_list, _nested_dict, _nested_mixed])
def test_far_deeper_than_the_interpreter_can_serialize_is_rejected_without_recursion(build):
    deep = build(200_000)
    with pytest.raises(RecursionError):
        json.dumps(deep)  # a estrutura realmente quebraria o serializer
    assert audit_fragment_violation(deep) == "complexity_limit_exceeded"


def test_wide_shallow_structure_fits_until_the_node_limit():
    # a lista conta como 1 nó; cada item, mais 1
    assert audit_fragment_violation(list(range(MAX_AUDIT_FRAGMENT_NODES - 1))) is None
    assert audit_fragment_violation(list(range(MAX_AUDIT_FRAGMENT_NODES))) == "complexity_limit_exceeded"
    wide = {f"k{i}": [i, str(i)] for i in range(3_000)}
    assert audit_fragment_violation(wide) is None


def test_huge_programmatic_list_is_rejected_before_being_walked():
    assert audit_fragment_violation([0] * 10_000_000) == "complexity_limit_exceeded"


def test_cyclic_structure_terminates():
    cycle: list = []
    cycle.append(cycle)
    assert audit_fragment_violation(cycle) == "complexity_limit_exceeded"


class _Weird(IntEnum):
    ONE = 1


class _ListThatMustNotBeTouched(list):
    def __len__(self):  # pragma: no cover - nunca chamado
        raise AssertionError("o conteúdo nunca deve ser executado")

    def __iter__(self):  # pragma: no cover - nunca chamado
        raise AssertionError("o conteúdo nunca deve ser executado")


@pytest.mark.parametrize(
    "value",
    [
        math.nan,
        math.inf,
        -math.inf,
        [1, math.nan],
        {"x": math.inf},
        (1, 2),
        {1, 2},
        b"bytes",
        {1: "chave não-str"},
        _Weird.ONE,
        _ListThatMustNotBeTouched([1]),
        object(),
    ],
)
def test_non_json_values_are_never_accepted_ambiguously(value):
    assert audit_fragment_violation(value) == "non_json_value"
    assert bound_audit_fragment(value) == (None, "non_json_value")


def test_bound_keeps_an_already_recorded_omission():
    assert bound_audit_fragment(None, "complexity_limit_exceeded") == (None, "complexity_limit_exceeded")


def test_bound_returns_the_same_object_when_within_contract():
    value = {"kind": "arithmetic", "left": ["x"]}
    kept, reason = bound_audit_fragment(value)
    assert kept is value
    assert reason is None


# ---------------------------------------------------------------------------
# Strings: só Unicode scalar values (nenhum code point substituto)
# ---------------------------------------------------------------------------

_HIGH = chr(0xD800)
_LOW = chr(0xDFFF)

VALID_STRINGS = {
    "ascii": "Paris é a capital",
    "empty": "",
    "bmp": "中文 ñ ß Ω",
    "non_bmp_emoji": "\U0001F600\U0001F9D1\u200d\U0001F4BB",
    "combining": "e\u0301 a\u0308 \u0915\u094d\u0937",
    "bmp_edges": "\u0000\u007f\u0080\u07ff\u0800\ud7ff\ue000\ufffd\ufffe\uffff",
    "plane_edges": "\U00010000\U0010FFFF",
    "long_within_limits": "x" * 200_000,
}

INVALID_STRINGS = {
    "high_surrogate": "a" + _HIGH + "b",
    "low_surrogate": _LOW,
    "pair_as_two_code_points": chr(0xD83D) + chr(0xDE00),
    "reversed_pair": chr(0xDE00) + chr(0xD83D),
    "surrogate_at_end_of_long": "x" * 50_000 + _HIGH,
}


@pytest.mark.parametrize("name", sorted(VALID_STRINGS))
def test_valid_unicode_is_accepted_as_value_and_key(name):
    text = VALID_STRINGS[name]
    assert audit_fragment_violation(text) is None
    assert audit_fragment_violation({text: [text]}) is None


@pytest.mark.parametrize("name", sorted(INVALID_STRINGS))
def test_surrogate_code_points_are_rejected_as_value_and_key(name):
    text = INVALID_STRINGS[name]
    assert audit_fragment_violation(text) == "non_json_value"
    assert audit_fragment_violation({"k": [1, {"v": text}]}) == "non_json_value"
    assert audit_fragment_violation({text: 1}) == "non_json_value"
    assert audit_fragment_violation({"ok": {text: None}}) == "non_json_value"


def test_json_escape_of_a_lone_surrogate_is_exactly_what_the_provider_path_produces():
    assert json.loads('{"x": "\\ud800"}')["x"] == _HIGH
    assert audit_fragment_violation(json.loads('{"x": "\\ud800"}')) == "non_json_value"
    # um par escapado CORRETAMENTE vira um único caractere não-BMP válido
    assert audit_fragment_violation(json.loads('{"x": "\\ud83d\\ude00"}')) is None


# ---------------------------------------------------------------------------
# Inteiros: int64, checado por comparação (nunca str())
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [0, 1, -1, MAX_AUDIT_FRAGMENT_INT, MIN_AUDIT_FRAGMENT_INT, 2**53, -(2**53)]
)
def test_int64_integers_are_accepted(value):
    assert audit_fragment_violation(value) is None
    assert audit_fragment_violation({"x": [value]}) is None


@pytest.mark.parametrize(
    "value",
    [MAX_AUDIT_FRAGMENT_INT + 1, MIN_AUDIT_FRAGMENT_INT - 1, 10**20, -(10**640), 10**5000],
    ids=["int64_max_plus_1", "int64_min_minus_1", "1e20", "minus_1e640", "1e5000"],
)
def test_integers_outside_int64_are_rejected(value):
    assert audit_fragment_violation(value) == "non_json_value"
    assert audit_fragment_violation({"x": [value]}) == "non_json_value"


def test_integer_check_never_converts_to_string():
    huge = 10**200_000  # str() disto estoura o limite padrão de dígitos
    with pytest.raises(ValueError):
        str(huge)
    assert audit_fragment_violation(huge) == "non_json_value"


def test_contract_constants_are_int64():
    assert (MIN_AUDIT_FRAGMENT_INT, MAX_AUDIT_FRAGMENT_INT) == (-(2**63), 2**63 - 1)


# ---------------------------------------------------------------------------
# Profundidade de texto armazenado, sem decodificar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "exceeds"),
    [
        ("null", False),
        ("5", False),
        ('"[[[[[["', False),
        ("[" * MAX_AUDIT_FRAGMENT_DEPTH + "]" * MAX_AUDIT_FRAGMENT_DEPTH, False),
        ("[" * (MAX_AUDIT_FRAGMENT_DEPTH + 1) + "]" * (MAX_AUDIT_FRAGMENT_DEPTH + 1), True),
        ('{"a": ' * 33 + "1" + "}" * 33, True),
        ('["\\"[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[["]', False),  # colchetes dentro de string com aspas escapadas
        ("[" * 1_000_000, True),
    ],
)
def test_stored_text_depth_is_measured_without_decoding(text, exceeds):
    assert json_text_exceeds_depth(text) is exceeds


def test_stored_text_depth_agrees_with_the_structural_contract():
    for depth in (1, 5, 31, 32, 33, 64):
        value = _nested_mixed(depth)
        text = json.dumps(value)
        assert json_text_exceeds_depth(text) is (audit_fragment_violation(value) is not None)
