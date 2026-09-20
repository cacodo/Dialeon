"""
claim_grouping_v4 -- contrato da PARTIÇÃO consultiva do agrupamento
intra-round (`group_claims`).

`group_claims` pede uma partição exata das claims em `clusters` (listas de
ids), valida exata-uma-vez, registra a tentativa e devolve SÓ as tentativas.
Cluster de 1 id é válido e esperado; não há `canonical_text`, nem
`ungrouped_claim_ids`. Nenhuma partição malformada é normalizada.

Autoridade (nada é criado/unido/supersedido pelo agrupamento) e o
downstream: tests/debate/test_grouping_v4_authority.py.
"""

from __future__ import annotations

import json

import pytest

from app.debate.claim_extraction import (
    CLAIM_GROUPING_CONTRACT_VERSION,
    _parse_and_validate_grouping_partition,
    group_claims,
)
from app.debate.errors import InconsistentClaimReferenceError, MalformedClaimOutputError
from app.models.provider_models import ModelIdentitySource
from app.models.request_provenance import build_request_provenance
from tests.council.fixtures import run_config as _run_config
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response
from tests.judge.fixtures import raw_claim

_PROVIDERS = ("openai", "anthropic", "gemini")


def _claims(n: int = 4):
    return [
        raw_claim(f"claim {chr(97 + i)}", f"resp-{i}", provider=_PROVIDERS[i % 3])
        for i in range(n)
    ]


def _payload(*clusters) -> str:
    return json.dumps({"clusters": [list(c) for c in clusters]})


async def _group(claims, responses):
    provider = ScriptedProvider("anthropic", responses)
    attempts = await group_claims(
        claims,
        round_number=1,
        grouper=provider,
        max_output_tokens_per_call=8192,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )
    return attempts, provider


def _ok(payload: str, **kwargs):
    return text_response("anthropic", payload, **kwargs)


# ---------------------------------------------------------------------------
# Contrato da partição v4 (parser/validador direto)
# ---------------------------------------------------------------------------


def test_valid_mixed_partition_multi_and_singleton_clusters():
    a, b, c, d = _claims(4)
    ids = {a.id, b.id, c.id, d.id}

    parsed = _parse_and_validate_grouping_partition(_payload([a.id, b.id], [c.id], [d.id]), ids)

    assert parsed.clusters == [[a.id, b.id], [c.id], [d.id]]


def test_valid_all_singleton_partition():
    claims = _claims(3)
    parsed = _parse_and_validate_grouping_partition(
        _payload(*[[c.id] for c in claims]), {c.id for c in claims}
    )

    assert [len(c) for c in parsed.clusters] == [1, 1, 1]


def test_valid_single_cluster_with_every_claim():
    claims = _claims(3)
    parsed = _parse_and_validate_grouping_partition(
        _payload([c.id for c in claims]), {c.id for c in claims}
    )

    assert len(parsed.clusters) == 1 and len(parsed.clusters[0]) == 3


def test_every_id_appears_exactly_once_across_the_partition():
    claims = _claims(5)
    ids = {c.id for c in claims}
    parsed = _parse_and_validate_grouping_partition(
        _payload([claims[0].id, claims[3].id], [claims[1].id], [claims[2].id, claims[4].id]), ids
    )

    flat = [i for cluster in parsed.clusters for i in cluster]
    assert sorted(flat) == sorted(ids) and len(flat) == len(set(flat))


def test_returned_order_is_the_response_order_never_re_sorted():
    a, b, c = _claims(3)
    parsed = _parse_and_validate_grouping_partition(
        _payload([c.id], [b.id, a.id]), {a.id, b.id, c.id}
    )

    assert parsed.clusters == [[c.id], [b.id, a.id]]  # clusters e membros como vieram


def test_fenced_json_is_accepted():
    a, b = _claims(2)
    fenced = "```json\n" + _payload([a.id, b.id]) + "\n```"

    parsed = _parse_and_validate_grouping_partition(fenced, {a.id, b.id})

    assert parsed.clusters == [[a.id, b.id]]


# ---------------------------------------------------------------------------
# Rejeição fail-closed -- nada é normalizado
# ---------------------------------------------------------------------------


def _reject(text: str, ids: set[str], exc):
    with pytest.raises(exc):
        _parse_and_validate_grouping_partition(text, ids)


def test_missing_id_is_rejected():
    a, b, c = _claims(3)
    _reject(_payload([a.id, b.id]), {a.id, b.id, c.id}, InconsistentClaimReferenceError)


def test_extra_unknown_id_is_rejected():
    a, b = _claims(2)
    _reject(_payload([a.id], [b.id], ["id-que-nao-existe"]), {a.id, b.id}, InconsistentClaimReferenceError)


def test_duplicate_id_within_a_cluster_is_rejected():
    a, b = _claims(2)
    _reject(_payload([a.id, a.id], [b.id]), {a.id, b.id}, InconsistentClaimReferenceError)


def test_duplicate_id_across_clusters_is_rejected():
    a, b, c = _claims(3)
    _reject(_payload([a.id, b.id], [b.id, c.id]), {a.id, b.id, c.id}, InconsistentClaimReferenceError)


def test_empty_cluster_is_rejected():
    a, b = _claims(2)
    _reject(_payload([a.id, b.id], []), {a.id, b.id}, MalformedClaimOutputError)


def test_empty_clusters_list_is_rejected_because_ids_are_missing():
    a, b = _claims(2)
    _reject(_payload(), {a.id, b.id}, InconsistentClaimReferenceError)


def test_malformed_json_is_rejected():
    a, b = _claims(2)
    _reject("isto não é json", {a.id, b.id}, MalformedClaimOutputError)


def test_truncated_json_is_rejected():
    a, b = _claims(2)
    _reject('{"clusters": [["' + a.id + '", "', {a.id, b.id}, MalformedClaimOutputError)


@pytest.mark.parametrize(
    "bad",
    [
        '{"clusters": "nao-e-lista"}',
        '{"clusters": ["id-solto"]}',  # cluster é string, não lista
        '{"clusters": [[1, 2]]}',  # ids não-string
        '{"clusters": [[null]]}',
        '{"clusters": {"a": ["x"]}}',
        '["x"]',
        "{}",
    ],
    ids=["clusters-not-list", "cluster-is-string", "non-string-ids", "null-id", "clusters-object", "top-level-list", "missing-clusters-key"],
)
def test_wrong_types_and_shapes_are_rejected(bad):
    a, b = _claims(2)
    with pytest.raises((MalformedClaimOutputError, InconsistentClaimReferenceError)):
        _parse_and_validate_grouping_partition(bad, {a.id, b.id})


@pytest.mark.parametrize(
    "extra",
    [
        {"ungrouped_claim_ids": []},
        {"groups": []},
        {"canonical_text": "texto sintetizado"},
        {"notes": "qualquer coisa"},
    ],
    ids=["ungrouped-key", "groups-key", "canonical-text-key", "unknown-key"],
)
def test_unsupported_extra_structure_is_rejected(extra):
    a, b = _claims(2)
    body = {"clusters": [[a.id, b.id]], **extra}
    _reject(json.dumps(body), {a.id, b.id}, MalformedClaimOutputError)


def test_legacy_v1_v3_response_shape_is_rejected_under_v4():
    """`{"groups": [...canonical_text...], "ungrouped_claim_ids": [...]}`
    (formato v1-v3) NÃO é aceito pelo contrato v4 -- nenhuma tradução."""
    a, b = _claims(2)
    legacy = json.dumps(
        {"groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}], "ungrouped_claim_ids": []}
    )
    _reject(legacy, {a.id, b.id}, MalformedClaimOutputError)


# ---------------------------------------------------------------------------
# group_claims -- aceitação, registro, retry, contabilidade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_raw_claims_short_circuits_without_calling_llm():
    provider = ScriptedProvider("anthropic", [])

    attempts = await group_claims(
        [], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(), prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert attempts == []
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_group_claims_returns_only_attempts_no_claims():
    a, b, c = _claims(3)

    attempts, _p = await _group([a, b, c], [_ok(_payload([a.id, b.id], [c.id]))])

    assert isinstance(attempts, list) and len(attempts) == 1
    assert all(type(x).__name__ == "ClaimProcessingAttempt" for x in attempts)


@pytest.mark.asyncio
async def test_valid_v4_partition_is_plain_accepted_and_never_accepted_normalized():
    a, b, c = _claims(3)

    attempts, provider = await _group([a, b, c], [_ok(_payload([a.id, b.id], [c.id]))])

    assert [x.parse_status for x in attempts] == ["accepted"]
    assert attempts[0].parse_error_message is None
    assert len(provider.received_requests) == 1  # nenhum retry


@pytest.mark.asyncio
async def test_all_singleton_partition_is_plain_accepted_with_no_retry():
    claims = _claims(3)

    attempts, provider = await _group(claims, [_ok(_payload(*[[c.id] for c in claims]))])

    assert [x.parse_status for x in attempts] == ["accepted"]
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_raw_v4_response_is_persisted_unchanged_in_the_attempt():
    a, b, c = _claims(3)
    payload = _payload([a.id, b.id], [c.id])

    attempts, _p = await _group([a, b, c], [_ok(payload)])

    assert attempts[0].raw_output_text == payload  # byte a byte
    assert attempts[0].operation == "grouping"
    assert sorted(attempts[0].target_claim_ids) == sorted([a.id, b.id, c.id])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_builder, expected_status",
    [
        (lambda a, b, c: _payload([a.id, b.id]), "inconsistent_references"),  # c faltando
        (lambda a, b, c: _payload([a.id, b.id], [c.id], ["x"]), "inconsistent_references"),  # extra
        (lambda a, b, c: _payload([a.id, a.id], [b.id], [c.id]), "inconsistent_references"),
        (lambda a, b, c: _payload([a.id, b.id], [b.id, c.id]), "inconsistent_references"),
        (lambda a, b, c: _payload([a.id, b.id, c.id], []), "malformed"),  # cluster vazio
        (lambda a, b, c: "não é json", "malformed"),
    ],
    ids=["missing", "extra", "dup-in-cluster", "dup-across", "empty-cluster", "malformed-json"],
)
async def test_invalid_partition_keeps_the_existing_structured_retry(bad_builder, expected_status):
    a, b, c = _claims(3)
    good = _payload([a.id], [b.id], [c.id])

    attempts, provider = await _group([a, b, c], [_ok(bad_builder(a, b, c)), _ok(good)])

    assert [x.parse_status for x in attempts] == [expected_status, "accepted"]
    assert [x.attempt_number for x in attempts] == [1, 2]
    assert attempts[0].parse_error_message
    assert len(provider.received_requests) == 2


@pytest.mark.asyncio
async def test_no_invalid_partition_is_normalized_it_is_rejected_twice_then_fails_closed():
    a, b = _claims(2)
    bad = _payload([a.id])  # b faltando

    attempts, _p = await _group([a, b], [_ok(bad), _ok(bad)])

    assert [x.parse_status for x in attempts] == ["inconsistent_references"] * 2
    assert all(x.parse_status != "accepted_normalized" for x in attempts)


@pytest.mark.asyncio
async def test_known_truncation_skips_retry_and_fails_closed():
    a, b = _claims(2)
    truncated = '{"clusters": [["' + a.id + '", "'
    attempts, provider = await _group(
        [a, b],
        [_ok(truncated, input_tokens=5059, output_tokens=8192, cost_usd=0.092038, provider_finish_reason="max_tokens")],
    )

    assert [x.parse_status for x in attempts] == ["malformed"]
    assert attempts[0].provider_finish_reason == "max_tokens"
    assert len(provider.received_requests) == 1  # truncamento confirmado: sem retry
    assert attempts[0].usage.input_tokens == 5059 and attempts[0].usage.output_tokens == 8192
    assert attempts[0].cost_usd == pytest.approx(0.092038)


@pytest.mark.asyncio
async def test_malformed_without_truncation_signal_still_retries():
    a, b = _claims(2)

    attempts, provider = await _group(
        [a, b],
        [_ok("não é JSON", provider_finish_reason="end_turn"), _ok(_payload([a.id], [b.id]))],
    )

    assert [x.parse_status for x in attempts] == ["malformed", "accepted"]
    assert len(provider.received_requests) == 2


@pytest.mark.asyncio
async def test_retry_blocked_when_budget_already_exhausted():
    a, b = _claims(2)

    provider = ScriptedProvider("anthropic", [_ok("não é JSON válido")])
    attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(max_cost_usd=0.05),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.06,
    )

    assert len(attempts) == 1  # sem retry: budget já esgotado


@pytest.mark.asyncio
async def test_accounting_of_each_attempt_is_recorded_and_unchanged():
    a, b, c = _claims(3)
    bad = _ok(_payload([a.id]), input_tokens=100, output_tokens=50, cost_usd=0.01)
    good = _ok(_payload([a.id, b.id], [c.id]), input_tokens=100, output_tokens=60, cost_usd=0.012)

    attempts, _p = await _group([a, b, c], [bad, good])

    assert [x.usage.output_tokens for x in attempts] == [50, 60]
    assert [x.cost_usd for x in attempts] == [pytest.approx(0.01), pytest.approx(0.012)]
    assert attempts[1].transport_attempts == good.attempts


@pytest.mark.asyncio
async def test_transport_failure_accounting_stays_unknown():
    a, b = _claims(2)

    attempts, _p = await _group([a, b], [transport_error_response("anthropic", attempts=3)])

    assert attempts[0].transport_status == "error" and attempts[0].parse_status == "not_attempted"
    assert attempts[0].usage is None and attempts[0].cost_usd is None
    assert attempts[0].transport_attempts == 3


@pytest.mark.asyncio
async def test_grouping_preserves_each_claims_identity_source_untouched():
    """Cada claim de entrada permanece byte-a-byte a mesma (nenhuma é
    reconstruída/substituída pelo agrupamento)."""
    a = raw_claim("a", "resp-a", provider="openai")
    a.supporting_model_response_ids[0] = a.supporting_model_response_ids[0].model_copy(
        update={"model_identity_source": ModelIdentitySource.PROVIDER_REPORTED}
    )
    b = raw_claim("b", "resp-b", provider="anthropic")
    before = [a.model_dump(), b.model_dump()]

    await _group([a, b], [_ok(_payload([a.id, b.id]))])

    assert [a.model_dump(), b.model_dump()] == before


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grouping_attempts_carry_the_v4_request_provenance():
    a, b = _claims(2)

    attempts, provider = await _group([a, b], [_ok(_payload([a.id], [b.id]))])

    assert CLAIM_GROUPING_CONTRACT_VERSION == "claim_grouping_v4"
    expected = build_request_provenance(CLAIM_GROUPING_CONTRACT_VERSION, provider.received_requests[0])
    assert attempts[0].request_provenance == expected
    assert attempts[0].request_provenance.contract_version == "claim_grouping_v4"


@pytest.mark.asyncio
async def test_malformed_then_success_attempts_share_identical_request_provenance():
    a, b = _claims(2)

    attempts, _p = await _group([a, b], [_ok("não é json"), _ok(_payload([a.id], [b.id]))])

    assert attempts[0].request_provenance == attempts[1].request_provenance


# ---------------------------------------------------------------------------
# IDs EXATOS -- nenhuma normalização de whitespace (grouping v4)
# ---------------------------------------------------------------------------

_WHITESPACE_VARIANTS = [
    lambda i: " " + i,
    lambda i: i + " ",
    lambda i: "\t" + i,
    lambda i: i + "\n",
    lambda i: "\n" + i + "\t",
    lambda i: "  " + i + "  ",
]
_WS_IDS = ["leading-space", "trailing-space", "leading-tab", "trailing-newline", "both-ws", "both-spaces"]


def test_exact_original_id_is_still_accepted_and_kept_verbatim():
    a, b = _claims(2)

    parsed = _parse_and_validate_grouping_partition(_payload([a.id], [b.id]), {a.id, b.id})

    assert parsed.clusters == [[a.id], [b.id]]


@pytest.mark.parametrize("alter", _WHITESPACE_VARIANTS, ids=_WS_IDS)
def test_whitespace_altered_id_is_never_silently_normalized_and_is_rejected(alter):
    a, b = _claims(2)
    altered = alter(a.id)
    assert altered != a.id and altered.strip() == a.id  # a variante É só whitespace

    # o id alterado não vira o id válido: vira desconhecido (e o válido, faltando)
    with pytest.raises(InconsistentClaimReferenceError):
        _parse_and_validate_grouping_partition(_payload([altered], [b.id]), {a.id, b.id})
    with pytest.raises(InconsistentClaimReferenceError):
        _parse_and_validate_grouping_partition(_payload([altered, b.id]), {a.id, b.id})


def test_whitespace_altered_id_alongside_its_exact_id_is_not_a_duplicate_it_is_an_unknown_id():
    """`" id"` + `"id"` NÃO é tratado como duplicata (isso exigiria strip): é
    um id desconhecido -- a partição inteira é rejeitada de qualquer forma."""
    a, b = _claims(2)

    with pytest.raises(InconsistentClaimReferenceError) as info:
        _parse_and_validate_grouping_partition(
            _payload([a.id, " " + a.id], [b.id]), {a.id, b.id}
        )
    assert "desconhecido" in str(info.value)


def test_parsed_partition_preserves_the_exact_returned_strings():
    """Schema-level: nenhum strip acontece nem em ids inválidos (o valor
    parseado == a string bruta), então o parse aceito nunca diverge da saída."""
    from app.debate.schemas import ClaimGroupingPartitionOutput

    parsed = ClaimGroupingPartitionOutput.model_validate({"clusters": [[" a ", "b\n"], ["\tc"]]})

    assert parsed.clusters == [[" a ", "b\n"], ["\tc"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("alter", _WHITESPACE_VARIANTS[:4], ids=_WS_IDS[:4])
async def test_whitespace_altered_partition_follows_the_existing_structured_retry(alter):
    a, b, c = _claims(3)
    bad = _payload([alter(a.id)], [b.id], [c.id])
    good = _payload([a.id], [b.id], [c.id])

    attempts, provider = await _group([a, b, c], [_ok(bad), _ok(good)])

    assert [x.parse_status for x in attempts] == ["inconsistent_references", "accepted"]
    assert attempts[0].raw_output_text == bad  # saída bruta INALTERADA (com o whitespace)
    assert attempts[1].raw_output_text == good
    assert len(provider.received_requests) == 2


@pytest.mark.asyncio
async def test_whitespace_altered_partition_twice_fails_closed():
    a, b = _claims(2)
    bad = _payload([" " + a.id], [b.id])

    attempts, _p = await _group([a, b], [_ok(bad), _ok(bad)])

    assert [x.parse_status for x in attempts] == ["inconsistent_references"] * 2
    assert all(x.parse_status != "accepted_normalized" for x in attempts)


def test_other_schemas_keep_their_whitespace_stripping_config():
    """A mudança é ESCOPADA aos schemas de ids exatos (partição v4 e
    equivalência de reconciliação v2): o schema de extração continua com
    `_IO_CONFIG` (strip habilitado)."""
    from app.debate.schemas import (
        ClaimExtractionOutput,
        ClaimGroupingPartitionOutput,
        CrossRoundEquivalenceProposalOutput,
    )

    assert ClaimGroupingPartitionOutput.model_config.get("str_strip_whitespace") is False
    assert CrossRoundEquivalenceProposalOutput.model_config.get("str_strip_whitespace") is False
    assert ClaimExtractionOutput.model_config.get("str_strip_whitespace") is True
