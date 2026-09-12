from __future__ import annotations

from app.debate.claims import get_current_claims
from app.models.domain import Claim, ClaimSupport


def _support(response_id: str, provider: str = "openai") -> ClaimSupport:
    return ClaimSupport(model_response_id=response_id, provider=provider, model="test-model")


def _raw(text: str, response_id: str, **overrides) -> Claim:
    fields = dict(
        text=text,
        source_model_response_id=response_id,
        round_introduced=1,
        status="active",
        supporting_model_response_ids=[_support(response_id)],
        total_models_in_round=3,
    )
    fields.update(overrides)
    return Claim(**fields)


def test_raw_claim_absorbed_by_canonical_is_excluded():
    raw_a = _raw("A", "resp-a")
    raw_b = _raw("B", "resp-b")
    canonical = Claim(
        text="A e B juntas",
        source_model_response_id=None,
        round_introduced=1,
        merged_from_claim_ids=[raw_a.id, raw_b.id],
        status="consensus",
        supporting_model_response_ids=[_support("resp-a"), _support("resp-b", "anthropic")],
        total_models_in_round=2,
    )

    current = get_current_claims([raw_a, raw_b, canonical])

    assert raw_a not in current
    assert raw_b not in current
    assert canonical in current


def test_claim_replaced_by_1_to_1_revision_is_excluded():
    original = _raw("versão original", "resp-1")
    revised = _raw("versão corrigida", "resp-2", parent_claim_id=original.id, round_introduced=2)

    current = get_current_claims([original, revised])

    assert original not in current
    assert revised in current


def test_canonical_claim_that_is_current_stays():
    raw_a = _raw("A", "resp-a")
    raw_b = _raw("B", "resp-b")
    canonical = Claim(
        text="fusão",
        source_model_response_id=None,
        round_introduced=1,
        merged_from_claim_ids=[raw_a.id, raw_b.id],
        status="consensus",
        supporting_model_response_ids=[_support("resp-a"), _support("resp-b", "anthropic")],
        total_models_in_round=2,
    )
    current = get_current_claims([raw_a, raw_b, canonical])
    assert current == [canonical]


def test_independent_claim_never_referenced_stays():
    lone = _raw("nunca mencionada por ninguém", "resp-x")
    other = _raw("outra claim qualquer", "resp-y")
    current = get_current_claims([lone, other])
    assert lone in current
    assert other in current


def test_chain_of_revisions_only_the_last_one_survives():
    v1 = _raw("v1", "resp-1")
    v2 = _raw("v2", "resp-2", parent_claim_id=v1.id, round_introduced=2)
    v3 = _raw("v3", "resp-3", parent_claim_id=v2.id, round_introduced=2)

    current = get_current_claims([v1, v2, v3])

    assert current == [v3]


def test_merged_revisions_do_not_inherit_parent_claim_id():
    """Se duas claims de revisão (mesmo parent_claim_id) forem fundidas
    pelo agrupamento do round 2, a canônica de merge NÃO herda
    parent_claim_id automaticamente — mas a lineage de revisão continua
    auditável seguindo merged_from_claim_ids até as brutas."""
    original = _raw("original", "resp-1")
    revision_a = _raw("correção A", "resp-2", parent_claim_id=original.id, round_introduced=2)
    revision_b = _raw("correção B", "resp-3", parent_claim_id=original.id, round_introduced=2)
    merged_revisions = Claim(
        text="correção unificada",
        source_model_response_id=None,
        round_introduced=2,
        merged_from_claim_ids=[revision_a.id, revision_b.id],
        parent_claim_id=None,  # não herda — decisão deliberada (Etapa 5)
        status="consensus",
        supporting_model_response_ids=[_support("resp-2"), _support("resp-3", "anthropic")],
        total_models_in_round=2,
    )

    current = get_current_claims([original, revision_a, revision_b, merged_revisions])

    assert current == [merged_revisions]
    # lineage ainda é rastreável: merged_revisions -> revision_a/revision_b -> original
    assert revision_a.parent_claim_id == original.id
    assert revision_b.parent_claim_id == original.id


def test_get_current_claims_never_reads_status_or_superseded_by():
    """Mesmo uma claim com status='superseded'/superseded_by preenchido
    (dormente nesta etapa, mas ainda representável no schema) não afeta a
    função — ela só olha parent_claim_id/merged_from_claim_ids."""
    new_claim = _raw("nova", "resp-2")
    old_claim = _raw(
        "antiga, marcada superseded manualmente",
        "resp-1",
        status="superseded",
        superseded_by=new_claim.id,
    )
    # old_claim NÃO é referenciada por parent_claim_id/merged_from_claim_ids
    # de ninguém — então, pela regra real (não por status), ela CONTINUA atual.
    current = get_current_claims([old_claim, new_claim])
    assert old_claim in current
    assert new_claim in current
