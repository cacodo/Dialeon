"""Repair M2 -- prova de serialização do contrato de fragmento de auditoria.

Todo valor ACEITO pelo contrato (app/audit_fragment.py), em qualquer um
dos dois campos brutos, atravessa a cadeia oficial do Dialeon:

    construção validada -> save_success -> commit -> get_run (reload)
    -> model_dump(mode="json") -> model_dump_json()
    -> JSON público do audit (o mesmo modelo que GET /runs/{id}/audit
       serializa, em JSON estrito) -> JSON da CLI (`emit_json`)

e volta igual (igualdade numérica JSON: um float inteiro de primeiro
nível, como `5.0`, volta `5` pela afinidade NUMERIC do SQLite; booleanos
e strings exatos). Todo valor REJEITADO atravessa a mesma cadeia
degradado, com motivo explícito.
"""

from __future__ import annotations

import json
import math

import pytest

from app.audit_fragment import (
    MAX_AUDIT_FRAGMENT_DEPTH,
    MAX_AUDIT_FRAGMENT_INT,
    MAX_AUDIT_FRAGMENT_NODES,
    MIN_AUDIT_FRAGMENT_INT,
)
from app.cli import output
from app.debate.numeric_verification import DeterministicVerificationAttempt
from app.presentation.mappers import completed_run_audit
from app.source_analysis.models import RejectedSourceEntry
from app.storage.records import CompletedRunRecord
from tests.storage.fixtures import very_rich_council_run_result


def _nested(depth: int, leaf=None) -> list:
    value: list = [] if leaf is None else [leaf]
    for _ in range(depth - 1):
        value = [value]
    return value


_HIGH, _LOW = chr(0xD800), chr(0xDFFF)

ACCEPTED = {
    "none": None,
    "true": True,
    "false": False,
    "zero": 0,
    "int64_max": MAX_AUDIT_FRAGMENT_INT,
    "int64_min": MIN_AUDIT_FRAGMENT_INT,
    "nested_int64_max": {"x": [MAX_AUDIT_FRAGMENT_INT, MIN_AUDIT_FRAGMENT_INT]},
    "float": 1.5,
    "float_integral_top_level": 5.0,
    "float_max": 1.7976931348623157e308,
    "float_min_subnormal": 5e-324,
    "negative_zero_nested": [-0.0],
    "empty_string": "",
    "ascii": "Paris",
    "bmp": "中文 ñ ß Ω",
    "non_bmp": "\U0001F600\U0001F9D1‍\U0001F4BB",
    "combining": "é क्ष",
    "controls_and_edges": "\u0000\u001f\u007f퟿�￾￿\U0010FFFF",
    "unicode_keys": {"chave ç": 1, "\U0001F600": [None, True], "": {"é": "x"}},
    "long_string": "y" * 100_000,
    "depth_limit": _nested(MAX_AUDIT_FRAGMENT_DEPTH, leaf="fundo"),
    "wide_at_node_limit": list(range(MAX_AUDIT_FRAGMENT_NODES - 1)),
    "aliased_subtree": (lambda shared: {"a": shared, "b": shared})({"s": [1, 2]}),
    "normal_invalid_proposal": {"kind": "arithmetic", "left": "dois", "operator": "+"},
}

REJECTED = {
    "surrogate_value_high": ({"x": "a" + _HIGH}, "non_json_value"),
    "surrogate_value_low": ({"x": [_LOW]}, "non_json_value"),
    "surrogate_key": ({_HIGH: 1}, "non_json_value"),
    "surrogate_nested_key": ({"ok": {_LOW: "v"}}, "non_json_value"),
    "surrogate_pair_as_two_code_points": ({"x": chr(0xD83D) + chr(0xDE00)}, "non_json_value"),
    "surrogate_top_level": (_HIGH, "non_json_value"),
    "int64_max_plus_1_top_level": (MAX_AUDIT_FRAGMENT_INT + 1, "non_json_value"),
    "int_1e20_nested": ({"x": 10**20}, "non_json_value"),
    "int_5001_digits": ({"x": 10**5000}, "non_json_value"),
    "nan": ({"x": math.nan}, "non_json_value"),
    "infinity": ([math.inf], "non_json_value"),
    "tuple": ((1, 2), "non_json_value"),
    "depth_33": (_nested(MAX_AUDIT_FRAGMENT_DEPTH + 1), "complexity_limit_exceeded"),
    "nodes_10001": (list(range(MAX_AUDIT_FRAGMENT_NODES)), "complexity_limit_exceeded"),
    "cycle": ((lambda c: (c.append(c), c)[1])([]), "complexity_limit_exceeded"),
    "exponential_aliasing": (
        (lambda: (lambda f: f(f, 14))(lambda f, n: [1] if n == 0 else [f(f, n - 1)] * 2))(),
        "complexity_limit_exceeded",
    ),
}


def _json_equal(a, b) -> bool:
    """Igualdade de valor JSON: bool/str exatos, números por valor."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _with_fragment(value):
    result = very_rich_council_run_result()
    debate = result.debate_result
    numeric = list(debate.numeric_verification_attempts)
    i = next(i for i, a in enumerate(numeric) if a.state == "invalid_proposal")
    numeric[i] = DeterministicVerificationAttempt(
        **{**numeric[i].model_dump(), "raw_proposal": value}
    )
    source = result.source_analysis_result
    claims = list(source.claim_results)
    j = next(j for j, c in enumerate(claims) if c.kind == "rejected" and c.reason == "invalid_entry")
    claims[j] = RejectedSourceEntry(**{**claims[j].model_dump(), "raw_entry": value})
    built = result.model_copy(
        update={
            "debate_result": debate.model_copy(update={"numeric_verification_attempts": numeric}),
            "source_analysis_result": source.model_copy(update={"claim_results": claims}),
        }
    )
    return built, numeric[i].id, claims[j].id


async def _through_the_chain(repo, capsys, value):
    built, attempt_id, entry_id = _with_fragment(value)
    await repo.save_success(built)
    record = await repo.get_run(built.id)
    assert isinstance(record, CompletedRunRecord)
    reloaded = record.council_run_result

    dumped = reloaded.model_dump(mode="json")
    assert json.loads(reloaded.model_dump_json()) == dumped
    audit = completed_run_audit(
        reloaded,
        provider_execution_policy=record.provider_execution_policy,
        default_model_authority_snapshot=record.default_model_authority_snapshot,
    )
    public = json.loads(audit.model_dump_json())
    json.dumps(public, allow_nan=False)  # resposta HTTP estrita
    output.emit_json(audit)  # caminho JSON da CLI
    assert json.loads(capsys.readouterr().out) == public

    attempt = next(a for a in reloaded.debate_result.numeric_verification_attempts if a.id == attempt_id)
    entry = next(c for c in reloaded.source_analysis_result.claim_results if c.id == entry_id)
    in_memory = (
        next(a for a in built.debate_result.numeric_verification_attempts if a.id == attempt_id),
        next(c for c in built.source_analysis_result.claim_results if c.id == entry_id),
    )
    return (attempt.raw_proposal, attempt.raw_proposal_omitted_reason), (
        entry.raw_entry,
        entry.raw_entry_omitted_reason,
    ), in_memory


@pytest.mark.parametrize("name", sorted(ACCEPTED))
async def test_every_accepted_fragment_survives_the_whole_chain_unchanged(repo, capsys, name):
    value = ACCEPTED[name]

    proposal, entry, (built_attempt, built_entry) = await _through_the_chain(repo, capsys, value)

    assert built_attempt.raw_proposal_omitted_reason is None
    assert built_entry.raw_entry_omitted_reason is None
    assert proposal[1] is None and entry[1] is None
    assert _json_equal(proposal[0], value)
    assert _json_equal(entry[0], value)


@pytest.mark.parametrize("name", sorted(REJECTED))
async def test_every_rejected_fragment_is_degraded_explicitly_and_the_chain_still_renders(
    repo, capsys, name
):
    value, reason = REJECTED[name]

    proposal, entry, (built_attempt, built_entry) = await _through_the_chain(repo, capsys, value)

    assert (built_attempt.raw_proposal, built_attempt.raw_proposal_omitted_reason) == (None, reason)
    assert (built_entry.raw_entry, built_entry.raw_entry_omitted_reason) == (None, reason)
    assert proposal == (None, reason)
    assert entry == (None, reason)
