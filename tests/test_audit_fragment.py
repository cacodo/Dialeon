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
    MAX_AUDIT_FRAGMENT_NODES,
    audit_fragment_violation,
    bound_audit_fragment,
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
    [None, True, False, 0, -7, 10**100, 1.5, "", "texto", [], {}, {"kind": "arithmetic", "left": "1"}],
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
