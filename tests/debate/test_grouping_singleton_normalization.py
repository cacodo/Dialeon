"""
claim_grouping_v3 -- normalização determinística de grupos UNITÁRIOS no
agrupamento INTRA-ROUND (e só nele).

Um "grupo" de 1 id é a representação errada, mas inequívoca, de "esta claim
não tem equivalente" (replay v2 real: 6 claims em grupos unitários com
`ungrouped_claim_ids` vazio). Depois da normalização o id vira ungrouped, o
`canonical_text` do grupo unitário é DESCARTADO, a claim ORIGINAL permanece
intacta e a tentativa é registrada como `accepted_normalized`. O
`raw_output_text` continua a resposta ORIGINAL.

A normalização NÃO é um reparo genérico: JSON malformado/truncado, id
faltando/extra/duplicado, chave extra e grupo multi-membro inválido continuam
rejeitados (e mantêm o retry estruturado existente). Reconciliação continua
rejeitando grupo unitário.
"""

from __future__ import annotations

import json
import typing

import pytest
from pydantic import ValidationError

from app.debate.claim_extraction import (
    _parse_and_validate_grouping,
    _parse_validate_and_normalize_grouping,
    _build_grouping_request,
    _run_structured_grouping_call,
    group_claims,
    reconcile_claims,
)
from app.debate.claims import get_current_claims
from app.debate.errors import InconsistentClaimReferenceError, MalformedClaimOutputError
from app.debate.processing_record import ClaimProcessingAttempt
from app.editor.attempt import EditorAttempt
from app.judge.attempt import JudgeAttempt
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType
from app.models.request_provenance import build_request_provenance
from app.source_analysis.attempt import SourceAnalysisAttempt
from tests.council.fixtures import run_config as _run_config
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response
from tests.judge.fixtures import raw_claim


def _claims(n: int = 4):
    providers = ("openai", "anthropic", "gemini")
    return [
        raw_claim(f"claim {chr(97 + i)}", f"resp-{i}", provider=providers[i % 3]) for i in range(n)
    ]


def _payload(groups=(), ungrouped=(), **extra) -> str:
    body = {
        "groups": [{"member_claim_ids": list(m), "canonical_text": t} for m, t in groups],
        "ungrouped_claim_ids": list(ungrouped),
    }
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


async def _group(claims, responses):
    provider = ScriptedProvider("anthropic", responses)
    canonical, attempts = await group_claims(
        claims,
        round_number=1,
        grouper=provider,
        max_output_tokens_per_call=8192,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )
    return canonical, attempts, provider


def _ok(payload: str, **kwargs):
    return text_response("anthropic", payload, **kwargs)


# ---------------------------------------------------------------------------
# NORMALIZAÇÃO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_singleton_plus_valid_group_normalizes_and_is_recorded():
    a, b, c, d = _claims(4)
    payload = _payload(
        groups=[((a.id, b.id), "texto de a+b"), ((c.id,), "TEXTO GERADO DO SINGLETON")],
        ungrouped=[d.id],
    )

    canonical, attempts, provider = await _group([a, b, c, d], [_ok(payload)])

    assert [x.parse_status for x in attempts] == ["accepted_normalized"]
    assert attempts[0].parse_error_message is None
    assert len(provider.received_requests) == 1  # sem retry estruturado
    assert len(canonical) == 1  # só o grupo multi-membro virou claim canônica
    assert canonical[0].text == "texto de a+b"
    assert set(canonical[0].merged_from_claim_ids) == {a.id, b.id}


@pytest.mark.asyncio
async def test_multiple_singletons_normalize_and_no_canonical_claim_is_created_for_them():
    a, b, c, d = _claims(4)
    payload = _payload(
        groups=[((a.id,), "sing a"), ((b.id,), "sing b"), ((c.id,), "sing c"), ((d.id,), "sing d")]
    )  # o caso extremo do replay: TUDO em grupos unitários, ungrouped vazio

    canonical, attempts, _p = await _group([a, b, c, d], [_ok(payload)])

    assert [x.parse_status for x in attempts] == ["accepted_normalized"]
    assert canonical == []  # nenhuma claim canônica a partir de singletons


@pytest.mark.asyncio
async def test_singleton_claim_is_preserved_exactly_and_generated_text_never_applied():
    a, b, c = _claims(3)
    originals = {x.id: x.model_dump() for x in (a, b, c)}
    payload = _payload(groups=[((a.id, b.id), "a+b"), ((c.id,), "REESCRITA DO SINGLETON")])

    canonical, _attempts, _p = await _group([a, b, c], [_ok(payload)])

    # a claim original NÃO foi tocada: id, texto, support, lineage, tudo
    assert c.model_dump() == originals[c.id]
    assert c.text == "claim c"
    assert c.parent_claim_id is None and c.merged_from_claim_ids == [] and c.superseded_by is None
    current = get_current_claims([a, b, c, *canonical])
    by_id = {x.id: x for x in current}
    assert c.id in by_id and by_id[c.id] is c  # continua a MESMA claim atual
    assert a.id not in by_id and b.id not in by_id  # membros do grupo multi foram fundidos
    # o texto gerado pro singleton não sobrevive em nenhuma claim aplicada
    assert all("REESCRITA DO SINGLETON" not in x.text for x in [*canonical, *current])


@pytest.mark.asyncio
async def test_raw_provider_output_is_persisted_unchanged():
    a, b, c = _claims(3)
    payload = _payload(groups=[((a.id, b.id), "a+b"), ((c.id,), "TEXTO DO SINGLETON")])

    _canonical, attempts, _p = await _group([a, b, c], [_ok(payload)])

    assert attempts[0].raw_output_text == payload  # byte-a-byte, singleton incluso
    assert "TEXTO DO SINGLETON" in attempts[0].raw_output_text


def test_effective_partition_covers_every_id_exactly_once_and_keeps_deterministic_order():
    a, b, c, d, e = _claims(5)
    ids = {x.id for x in (a, b, c, d, e)}
    text = _payload(
        groups=[((c.id,), "s1"), ((a.id, b.id), "ab"), ((e.id,), "s2")], ungrouped=[d.id]
    )

    parsed, normalized = _parse_validate_and_normalize_grouping(text, ids)

    assert normalized is True
    assert [set(g.member_claim_ids) for g in parsed.groups] == [{a.id, b.id}]
    # ungrouped originais primeiro, depois os singletons na ordem da resposta
    assert parsed.ungrouped_claim_ids == [d.id, c.id, e.id]
    covered = [i for g in parsed.groups for i in g.member_claim_ids] + parsed.ungrouped_claim_ids
    assert sorted(covered) == sorted(ids)  # cada id EXATAMENTE uma vez
    # o schema APLICADO segue exigindo >= 2 membros (invariante não enfraquecida)
    assert all(len(g.member_claim_ids) >= 2 for g in parsed.groups)


@pytest.mark.asyncio
async def test_ordinary_valid_grouping_remains_plain_accepted():
    a, b, c = _claims(3)
    payload = _payload(groups=[((a.id, b.id), "a+b")], ungrouped=[c.id])

    canonical, attempts, _p = await _group([a, b, c], [_ok(payload)])

    assert [x.parse_status for x in attempts] == ["accepted"]  # NUNCA accepted_normalized
    assert len(canonical) == 1
    parsed, normalized = _parse_validate_and_normalize_grouping(payload, {a.id, b.id, c.id})
    assert normalized is False and len(parsed.groups) == 1


@pytest.mark.asyncio
async def test_all_ungrouped_response_remains_plain_accepted():
    claims = _claims(3)
    payload = _payload(ungrouped=[c.id for c in claims])

    canonical, attempts, _p = await _group(claims, [_ok(payload)])

    assert [x.parse_status for x in attempts] == ["accepted"]
    assert canonical == []


# ---------------------------------------------------------------------------
# REJEIÇÃO -- a normalização nunca esconde outro defeito
# ---------------------------------------------------------------------------


async def _assert_rejected_then_retried(claims, bad_payload, expected_status):
    good = _payload(ungrouped=[c.id for c in claims])
    canonical, attempts, provider = await _group(claims, [_ok(bad_payload), _ok(good)])
    assert attempts[0].parse_status == expected_status
    assert attempts[0].parse_error_message
    assert attempts[1].parse_status == "accepted"
    assert len(provider.received_requests) == 2  # o retry estruturado EXISTENTE foi usado
    assert canonical == []


@pytest.mark.asyncio
async def test_missing_id_is_still_rejected_even_with_a_singleton_present():
    a, b, c, d = _claims(4)
    bad = _payload(groups=[((a.id, b.id), "ab"), ((c.id,), "s")])  # d ausente
    await _assert_rejected_then_retried([a, b, c, d], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_duplicate_id_singleton_also_in_a_multi_group_is_rejected():
    a, b, c = _claims(3)
    bad = _payload(groups=[((a.id, b.id), "ab"), ((a.id,), "dup")], ungrouped=[c.id])
    await _assert_rejected_then_retried([a, b, c], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_duplicate_id_across_two_singleton_groups_is_rejected():
    a, b = _claims(2)
    bad = _payload(groups=[((a.id,), "x"), ((a.id,), "y")], ungrouped=[b.id])
    await _assert_rejected_then_retried([a, b], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_singleton_id_also_listed_as_ungrouped_is_rejected():
    a, b = _claims(2)
    bad = _payload(groups=[((a.id,), "x")], ungrouped=[a.id, b.id])
    await _assert_rejected_then_retried([a, b], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_extra_unknown_id_in_a_singleton_group_is_rejected():
    a, b = _claims(2)
    bad = _payload(groups=[(("id-que-nao-existe",), "x")], ungrouped=[a.id, b.id])
    await _assert_rejected_then_retried([a, b], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_extra_unknown_id_elsewhere_is_rejected_despite_a_normalizable_singleton():
    a, b, c = _claims(3)
    bad = _payload(groups=[((a.id,), "s")], ungrouped=[b.id, c.id, "id-que-nao-existe"])
    await _assert_rejected_then_retried([a, b, c], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_malformed_json_is_still_rejected():
    claims = _claims(2)
    await _assert_rejected_then_retried(claims, "isto não é json", "malformed")


@pytest.mark.asyncio
async def test_invalid_multi_member_structure_remains_rejected():
    a, b, c = _claims(3)
    # membros duplicados DENTRO do mesmo grupo multi-membro
    bad = _payload(groups=[((a.id, a.id), "aa")], ungrouped=[b.id, c.id])
    await _assert_rejected_then_retried([a, b, c], bad, "inconsistent_references")


@pytest.mark.asyncio
async def test_group_with_zero_members_is_rejected_as_malformed():
    a, b = _claims(2)
    bad = _payload(groups=[((), "vazio")], ungrouped=[a.id, b.id])
    await _assert_rejected_then_retried([a, b], bad, "malformed")


@pytest.mark.asyncio
async def test_singleton_group_with_empty_canonical_text_is_not_a_supported_shape():
    a, b = _claims(2)
    bad = _payload(groups=[((a.id,), "")], ungrouped=[b.id])
    await _assert_rejected_then_retried([a, b], bad, "malformed")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d, ids: d.update(extra_key=1),  # chave extra no topo
        lambda d, ids: d["groups"][0].update(extra_key=1),  # chave extra no grupo
        lambda d, ids: d["groups"][0].update(member_claim_ids=ids[0]),  # str, não lista
        lambda d, ids: d.update(groups="nao-e-lista"),  # groups não é lista
        lambda d, ids: d.update(ungrouped_claim_ids=ids[1]),  # ungrouped não é lista
    ],
    ids=["extra-top-key", "extra-group-key", "members-not-list", "groups-not-list", "ungrouped-not-list"],
)
@pytest.mark.asyncio
async def test_unsupported_shapes_stay_rejected_even_alongside_a_singleton(mutate):
    a, b = _claims(2)
    data = json.loads(_payload(groups=[((a.id,), "sing")], ungrouped=[b.id]))
    mutate(data, [a.id, b.id])
    await _assert_rejected_then_retried([a, b], json.dumps(data), "malformed")


@pytest.mark.asyncio
async def test_truncation_keeps_existing_behavior_no_retry_and_fail_closed():
    """Forma REAL do replay v1: cortado no meio de uma string, `max_tokens`."""
    claims = _claims(4)
    truncated = '{"groups": [{"member_claim_ids": ["' + claims[0].id + '"], "canonical_text": "cort'
    canonical, attempts, provider = await _group(
        claims,
        [
            _ok(
                truncated,
                input_tokens=5059,
                output_tokens=8192,
                cost_usd=0.092,
                provider_finish_reason="max_tokens",
            )
        ],
    )

    assert canonical == []
    assert [x.parse_status for x in attempts] == ["malformed"]
    assert len(provider.received_requests) == 1  # truncamento confirmado: sem retry
    assert attempts[0].usage.output_tokens == 8192
    assert attempts[0].cost_usd == pytest.approx(0.092)


def test_strict_validator_still_rejects_singleton_groups_at_the_schema():
    """`_parse_and_validate_grouping` (usado por reconciliação) NÃO foi
    afrouxado: grupo unitário continua violando `ClaimGroupProposal`
    (min 2) -- a tolerância vive só no caminho de normalização."""
    a, b = _claims(2)
    text = _payload(groups=[((a.id,), "x")], ungrouped=[b.id])

    with pytest.raises(MalformedClaimOutputError):
        _parse_and_validate_grouping(text, {a.id, b.id})
    # o mesmo texto É aceito pelo caminho de normalização do agrupamento
    parsed, normalized = _parse_validate_and_normalize_grouping(text, {a.id, b.id})
    assert normalized is True and parsed.groups == []
    # ...mas a normalização também NÃO esconde defeito de cobertura
    with pytest.raises(InconsistentClaimReferenceError):
        _parse_validate_and_normalize_grouping(
            _payload(groups=[((a.id,), "x")]), {a.id, b.id}
        )


# ---------------------------------------------------------------------------
# RETRY / CONTABILIDADE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_singleton_only_defect_consumes_no_structured_retry_and_keeps_accounting():
    a, b, c = _claims(3)
    payload = _payload(groups=[((a.id, b.id), "ab"), ((c.id,), "s")])
    response = _ok(payload, input_tokens=5059, output_tokens=2425, cost_usd=0.034368)

    canonical, attempts, provider = await _group([a, b, c], [response])

    assert len(attempts) == 1 and len(provider.received_requests) == 1
    attempt = attempts[0]
    assert attempt.attempt_number == 1
    assert attempt.usage.input_tokens == 5059 and attempt.usage.output_tokens == 2425
    assert attempt.cost_usd == pytest.approx(0.034368)
    assert attempt.transport_attempts == response.attempts
    assert attempt.provider_finish_reason == response.provider_finish_reason
    assert len(canonical) == 1


@pytest.mark.asyncio
async def test_non_normalizable_defect_keeps_retry_and_both_attempts_account_their_usage():
    a, b, c = _claims(3)
    bad = _ok(_payload(groups=[((a.id,), "s")]), input_tokens=100, output_tokens=50, cost_usd=0.01)
    good = _ok(
        _payload(groups=[((a.id, b.id), "ab")], ungrouped=[c.id]),
        input_tokens=100,
        output_tokens=60,
        cost_usd=0.012,
    )

    canonical, attempts, provider = await _group([a, b, c], [bad, good])

    assert [x.parse_status for x in attempts] == ["inconsistent_references", "accepted"]
    assert [x.attempt_number for x in attempts] == [1, 2]
    assert attempts[0].cost_usd == pytest.approx(0.01) and attempts[1].cost_usd == pytest.approx(0.012)
    assert len(provider.received_requests) == 2
    assert len(canonical) == 1


@pytest.mark.asyncio
async def test_transport_failure_accounting_is_unchanged():
    a, b = _claims(2)
    canonical, attempts, _p = await _group(
        [a, b], [transport_error_response("anthropic", attempts=3)]
    )

    assert canonical == []
    assert attempts[0].parse_status == "not_attempted"
    assert attempts[0].usage is None and attempts[0].cost_usd is None
    assert attempts[0].transport_attempts == 3


@pytest.mark.asyncio
async def test_every_attempt_of_a_call_shares_the_same_v3_provenance():
    a, b, c = _claims(3)
    bad = _ok("não é json")
    good = _ok(_payload(groups=[((a.id,), "s")], ungrouped=[b.id, c.id]))

    _canonical, attempts, provider = await _group([a, b, c], [bad, good])

    sent = provider.received_requests[0]
    expected = build_request_provenance("claim_grouping_v3", sent)
    assert [x.request_provenance for x in attempts] == [expected, expected]
    assert [x.parse_status for x in attempts] == ["malformed", "accepted_normalized"]


# ---------------------------------------------------------------------------
# FRONTEIRAS -- reconciliação, extração, Judge, domínio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_still_rejects_singleton_groups_and_never_normalizes():
    r1 = raw_claim("A", "resp-1", provider="openai")
    r2 = raw_claim("B", "resp-2", provider="anthropic", round_introduced=2)
    singleton = _payload(groups=[((r1.id,), "x")], ungrouped=[r2.id])
    valid = _payload(ungrouped=[r1.id, r2.id])
    provider = ScriptedProvider("anthropic", [_ok(singleton), _ok(valid)])

    canonical, attempts = await reconcile_claims(
        [r1],
        [r2],
        reconciler=provider,
        max_output_tokens_per_call=8192,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
        total_models_in_round=3,
        support_scope_model_count=3,
    )

    assert [x.parse_status for x in attempts] == ["malformed", "accepted"]
    assert all(x.operation == "reconciliation" for x in attempts)
    assert "accepted_normalized" not in {x.parse_status for x in attempts}
    assert canonical == []
    assert len(provider.received_requests) == 2  # retry estruturado de reconciliação intacto


@pytest.mark.asyncio
async def test_grouping_call_rejects_a_custom_validator():
    """A normalização é derivada de `operation == "grouping"`; um
    validador customizado nesse caminho é erro de programação."""
    a, b = _claims(2)
    provider = ScriptedProvider("anthropic", [])
    request = _build_grouping_request([a, b], 8192)
    with pytest.raises(ValueError):
        await _run_structured_grouping_call(
            "grouping",
            request,
            build_request_provenance("claim_grouping_v3", request),
            {a.id, b.id},
            1,
            provider,
            run_config=_run_config(),
            prior_input_tokens=0,
            prior_output_tokens=0,
            prior_cost_usd=0.0,
            validate=lambda text, ids: None,
        )


def _attempt_kwargs(**overrides):
    fields = dict(
        operation="grouping",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        requested_model="m",
        model="m",
        target_claim_ids=["c1", "c2"],
        transport_status="success",
        transport_attempts=1,
        raw_output_text="{}",
        parse_status="accepted_normalized",
        latency_ms=1,
    )
    fields.update(overrides)
    return fields


def test_accepted_normalized_is_valid_only_for_grouping():
    ok = ClaimProcessingAttempt(**_attempt_kwargs())
    assert ok.parse_status == "accepted_normalized"

    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_attempt_kwargs(operation="reconciliation"))
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(
            **_attempt_kwargs(
                operation="extraction", target_claim_ids=[], target_model_response_id="mr-1"
            )
        )


def test_accepted_normalized_rejects_error_message_and_transport_failure():
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_attempt_kwargs(parse_error_message="algo deu errado"))
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(
            **_attempt_kwargs(
                transport_status="error",
                raw_output_text=None,
                transport_error=ProviderErrorInfo(
                    type=ProviderErrorType.TIMEOUT, message="t", retryable=True
                ),
            )
        )


def test_accepted_normalized_does_not_exist_for_judge_editor_or_source_analysis():
    for model in (JudgeAttempt, EditorAttempt, SourceAnalysisAttempt):
        values = set(typing.get_args(model.model_fields["parse_status"].annotation))
        assert "accepted_normalized" not in values, model.__name__
        assert "accepted" in values


def test_only_the_grouping_branch_of_the_shared_call_can_emit_accepted_normalized():
    """Extração e reconciliação nunca referenciam `accepted_normalized`; o
    único ponto de emissão é `_run_structured_grouping_call`, e lá ele só
    vale quando `normalizes_singleton_groups` (operation == "grouping")."""
    import inspect

    from app.debate import claim_extraction as ce

    for fn in (ce.extract_claims, ce.reconcile_claims, ce._build_reconciliation_request):
        assert "accepted_normalized" not in inspect.getsource(fn), fn.__name__
    emitter = inspect.getsource(ce._run_structured_grouping_call)
    assert "accepted_normalized" in emitter
    assert 'normalizes_singleton_groups = operation == "grouping"' in emitter
