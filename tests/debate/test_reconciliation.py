"""
Testes de `reconcile_claims` (app/debate/claim_extraction.py) -- cross-round
claim reconciliation. Mesma disciplina de tests/debate/test_grouping.py
(claims fabricadas diretamente, `ScriptedProvider` roteirizado) -- aqui
focados no que é ESPECÍFICO de `reconcile_claims`: rótulo de auditoria
distinto ("reconciliation", nunca "grouping"), round_number fixo (2), o
denominador de suporte fornecido pelo chamador (total_models_in_round/
support_scope_model_count), nunca derivado internamente, e (correção
pós-revisão independente, HIGH 1) a restrição estrutural de que todo grupo
precisa interseccionar OS DOIS lados (Round 1 E Round 2) -- nunca aceito um
grupo same-side.

Cenários semânticos completos (revisão/contradição/refinamento/claim
independente coexistindo com reconciliação) são cobertos em
tests/debate/test_debate_engine_reconciliation.py, que exercita o pipeline
completo via DebateEngine.run() com fakes roteados por conteúdo -- aqui
testamos só `reconcile_claims` isoladamente, como test_grouping.py já faz
pra `group_claims`.
"""

from __future__ import annotations

import json

import pytest

from app.debate.claim_extraction import (
    CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION,
    _RECONCILIATION_ATTEMPT_ROUND_NUMBER,
    reconcile_claims,
)
from app.models.domain import Claim, ClaimSupport
from tests.council.fixtures import run_config as _run_config
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response


def _current_claim(
    claim_id_suffix: str, provider: str, round_introduced: int, total: int = 2
) -> Claim:
    return Claim(
        text=f"claim de {provider} (round {round_introduced})",
        source_model_response_id=f"resp-{claim_id_suffix}",
        round_introduced=round_introduced,
        status="active",
        supporting_model_response_ids=[
            ClaimSupport(model_response_id=f"resp-{claim_id_suffix}", provider=provider, model="m")
        ],
        total_models_in_round=total,
    )


async def _reconcile(round1, round2, provider, **overrides):
    fields = dict(
        max_output_tokens_per_call=1024,
        total_models_in_round=2,
        support_scope_model_count=2,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )
    fields.update(overrides)
    return await reconcile_claims(round1, round2, reconciler=provider, **fields)


@pytest.mark.asyncio
async def test_empty_round1_side_short_circuits_without_calling_llm():
    """9 -- nenhuma chamada real quando o lado Round 1 está vazio (o
    chamador já deveria ter decidido pular, ver DebateEngine.run -- este
    short-circuit é só defensivo, mesma disciplina de group_claims)."""
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    provider = ScriptedProvider("anthropic", [])
    canonical, attempts = await _reconcile([], [r2_claim], provider)
    assert canonical == []
    assert attempts == []
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_empty_round2_side_short_circuits_without_calling_llm():
    """9 -- idem, lado Round 2 vazio."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    provider = ScriptedProvider("anthropic", [])
    canonical, attempts = await _reconcile([r1_claim], [], provider)
    assert canonical == []
    assert attempts == []
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_successful_merge_labeled_as_reconciliation_never_grouping():
    """3 -- attempt de sucesso é rotulado operation='reconciliation',
    NUNCA 'grouping', mesmo reusando o mesmo mecanismo de validação."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    payload = json.dumps(
        {
            "groups": [
                {"member_claim_ids": [r1_claim.id, r2_claim.id], "canonical_text": "proposição unificada"}
            ],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await _reconcile([r1_claim], [r2_claim], provider)

    assert len(attempts) == 1
    assert attempts[0].operation == "reconciliation"
    assert attempts[0].round_number == _RECONCILIATION_ATTEMPT_ROUND_NUMBER == 2
    assert attempts[0].parse_status == "accepted"
    assert len(canonical) == 1
    merged = canonical[0]
    assert merged.text == "proposição unificada"
    assert set(merged.merged_from_claim_ids) == {r1_claim.id, r2_claim.id}
    assert merged.parent_claim_id is None
    assert merged.source_model_response_id is None


@pytest.mark.asyncio
async def test_caller_supplied_denominators_are_used_verbatim():
    """6/16 -- total_models_in_round/support_scope_model_count vêm
    SEMPRE do chamador -- reconcile_claims nunca os deriva a partir das
    claims recebidas."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1, total=3)
    r2_claim = _current_claim("r2", "gemini", round_introduced=2, total=2)
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_claim.id, r2_claim.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, _ = await _reconcile(
        [r1_claim],
        [r2_claim],
        provider,
        total_models_in_round=2,  # valor de rodada ORDINÁRIA (Round 2), fornecido pelo chamador
        support_scope_model_count=4,  # união fornecida pelo chamador -- não é 2, não é 3, não é max(2,3)=3, não é 2+3=5
    )

    assert canonical[0].total_models_in_round == 2
    assert canonical[0].support_scope_model_count == 4
    assert canonical[0].supporting_model_ratio == pytest.approx(2 / 4)


@pytest.mark.asyncio
async def test_round_introduced_is_max_of_constituent_rounds():
    """19 -- round_introduced da canônica de reconciliação = max(1, 2) =
    2 -- nunca o mais antigo (1), nunca uma Round 3 inventada."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_claim.id, r2_claim.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, _ = await _reconcile([r1_claim], [r2_claim], provider)

    assert canonical[0].round_introduced == 2


@pytest.mark.asyncio
async def test_no_genuine_duplicate_leaves_everything_ungrouped():
    """5 -- quando não há equivalência real, tudo fica em
    ungrouped_claim_ids -- nenhuma canônica criada, as claims originais
    seguem sendo elas mesmas (o chamador não precisa fazer nada
    especial)."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    payload = json.dumps(
        {"groups": [], "ungrouped_claim_ids": [r1_claim.id, r2_claim.id]}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await _reconcile([r1_claim], [r2_claim], provider)

    assert canonical == []
    assert attempts[0].parse_status == "accepted"
    assert r1_claim.text.startswith("claim de openai")  # objeto original intacto
    assert r2_claim.text.startswith("claim de anthropic")


@pytest.mark.asyncio
async def test_reconciliation_malformed_twice_preserves_original_claim_set():
    """8 -- agrupamento/reconciliação malformada nas 2 tentativas ->
    ([], attempts) -- o conjunto pré-reconciliação (as claims que o
    chamador já tinha) NUNCA é alterado; quem chama simplesmente não usa
    nenhuma canônica."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "não é JSON válido"),
            text_response("anthropic", "ainda não é JSON válido"),
        ],
    )

    canonical, attempts = await _reconcile([r1_claim], [r2_claim], provider)

    assert canonical == []
    assert len(attempts) == 2
    assert all(a.parse_status == "malformed" for a in attempts)
    assert all(a.operation == "reconciliation" for a in attempts)
    # nada foi mutado -- claims originais continuam intactas
    assert r1_claim.round_introduced == 1
    assert r2_claim.round_introduced == 2


@pytest.mark.asyncio
async def test_reconciliation_transport_failure_no_retry_preserves_original_set():
    """7 -- falha de transporte (sem retry desta camada) também degrada
    pro conjunto pré-reconciliação, mesma disciplina de group_claims."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    provider = ScriptedProvider("anthropic", [transport_error_response("anthropic")])

    canonical, attempts = await _reconcile([r1_claim], [r2_claim], provider)

    assert canonical == []
    assert len(attempts) == 1
    assert attempts[0].transport_status == "error"
    assert attempts[0].operation == "reconciliation"


@pytest.mark.asyncio
async def test_reconciliation_retry_blocked_when_budget_already_exhausted():
    """8 -- budget já esgotado bloqueia o retry, mesma disciplina de
    test_retry_blocked_when_budget_already_exhausted (test_grouping.py)."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    provider = ScriptedProvider("anthropic", [text_response("anthropic", "não é JSON válido")])

    canonical, attempts = await _reconcile(
        [r1_claim],
        [r2_claim],
        provider,
        run_config=_run_config(max_cost_usd=0.05),
        prior_cost_usd=0.06,
    )

    assert len(attempts) == 1  # sem retry
    assert canonical == []


@pytest.mark.asyncio
async def test_equivalent_claims_from_different_providers_across_rounds_merge():
    """6 -- claims equivalentes de PROVIDERS DIFERENTES, uma de cada
    rodada, se fundem corretamente numa única canônica."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "gemini", round_introduced=2)
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_claim.id, r2_claim.id], "canonical_text": "proposição comum"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, _ = await _reconcile([r1_claim], [r2_claim], provider)

    assert len(canonical) == 1
    assert {s.provider for s in canonical[0].supporting_model_response_ids} == {"openai", "gemini"}


@pytest.mark.asyncio
async def test_same_provider_model_across_rounds_dedupes_in_numerator():
    """7/18.C -- o MESMO provider/model respondendo em Round 1 e Round 2
    conta uma vez só no numerador de supporting_models, mesmo fundido por
    reconciliação."""
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "openai", round_introduced=2)  # mesmo provider/model
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_claim.id, r2_claim.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, _ = await _reconcile(
        [r1_claim],
        [r2_claim],
        provider,
        total_models_in_round=1,
        support_scope_model_count=1,  # só 1 identidade única participou (openai/m)
    )

    merged = canonical[0]
    assert merged.supporting_models == ["openai/m"]  # 1 modelo único, nunca 2
    assert merged.supporting_model_ratio == 1.0  # 1/1, nunca 2/1 (que violaria o validator)


# ---------------------------------------------------------------------------
# Correção pós-revisão independente (HIGH 1) -- restrição cross-side:
# todo grupo precisa interseccionar OS DOIS lados (Round 1 E Round 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round1_only_group_retried_then_succeeds_with_valid_cross_side_group():
    """5.A -- 1ª tentativa same-side inválida (rejeitada), 2ª tentativa
    cross-side válida -> reconciliação SUCEDE na 2ª tentativa, mesmo
    padrão de retry já usado por qualquer outra
    InconsistentClaimReferenceError."""
    r1_a = _current_claim("r1a", "openai", round_introduced=1)
    r1_b = _current_claim("r1b", "gemini", round_introduced=1)
    r2_a = _current_claim("r2a", "anthropic", round_introduced=2)
    same_side_payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_a.id, r1_b.id], "canonical_text": "fusão inválida"}],
            "ungrouped_claim_ids": [r2_a.id],
        }
    )
    valid_payload = json.dumps(
        {
            "groups": [
                {
                    "member_claim_ids": [r1_a.id, r1_b.id, r2_a.id],
                    "canonical_text": "fusão cross-round válida",
                }
            ],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", same_side_payload),
            text_response("anthropic", valid_payload),
        ],
    )

    canonical, attempts = await _reconcile(
        [r1_a, r1_b], [r2_a], provider, support_scope_model_count=3
    )

    assert len(attempts) == 2
    assert attempts[0].parse_status == "inconsistent_references"
    assert attempts[1].parse_status == "accepted"
    assert len(canonical) == 1
    assert set(canonical[0].merged_from_claim_ids) == {r1_a.id, r1_b.id, r2_a.id}


@pytest.mark.asyncio
async def test_round1_only_group_both_attempts_invalid_exhausts_without_merge():
    """5.B -- as 2 tentativas propõem grupos same-side -> reconciliação
    esgota o retry SEM produzir nenhuma canônica; o conjunto original
    (r1_a, r1_b, r2_a, todos independentes) permanece current -- nenhuma
    fusão indevida."""
    r1_a = _current_claim("r1a", "openai", round_introduced=1)
    r1_b = _current_claim("r1b", "gemini", round_introduced=1)
    r2_a = _current_claim("r2a", "anthropic", round_introduced=2)
    same_side_payload_1 = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_a.id, r1_b.id], "canonical_text": "fusão inválida 1"}],
            "ungrouped_claim_ids": [r2_a.id],
        }
    )
    same_side_payload_2 = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_a.id, r1_b.id], "canonical_text": "fusão inválida 2"}],
            "ungrouped_claim_ids": [r2_a.id],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", same_side_payload_1),
            text_response("anthropic", same_side_payload_2),
        ],
    )

    canonical, attempts = await _reconcile(
        [r1_a, r1_b], [r2_a], provider, support_scope_model_count=3
    )

    assert len(attempts) == 2
    assert all(a.parse_status == "inconsistent_references" for a in attempts)
    assert canonical == []
    # claims originais seguem intactas -- nenhuma fusão indevida
    assert {r1_a.text, r1_b.text, r2_a.text} == {
        "claim de openai (round 1)",
        "claim de gemini (round 1)",
        "claim de anthropic (round 2)",
    }


@pytest.mark.asyncio
async def test_round2_only_group_is_rejected_then_retry_exhausts_without_merge():
    """6/HIGH 1 -- espelho do caso Round-1-only: R1={r1-a}, R2={r2-a,
    r2-b}; group=[r2-a, r2-b] (same-side, Round 2 puro) é REJEITADO
    estruturalmente nas 2 tentativas, mesmo padrão de
    test_round1_only_group_both_attempts_invalid_exhausts_without_merge
    -- nenhuma fusão indevida, claims originais permanecem current."""
    r1_a = _current_claim("r1a", "openai", round_introduced=1)
    r2_a = _current_claim("r2a", "anthropic", round_introduced=2)
    r2_b = _current_claim("r2b", "gemini", round_introduced=2)
    same_side_payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r2_a.id, r2_b.id], "canonical_text": "fusão inválida"}],
            "ungrouped_claim_ids": [r1_a.id],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", same_side_payload),
            text_response("anthropic", same_side_payload),
        ],
    )

    canonical, attempts = await _reconcile(
        [r1_a], [r2_a, r2_b], provider, support_scope_model_count=3
    )

    assert len(attempts) == 2
    assert all(a.parse_status == "inconsistent_references" for a in attempts)
    assert canonical == []


@pytest.mark.asyncio
async def test_round2_only_group_retried_then_succeeds_with_valid_cross_side_group():
    """6/HIGH 1 -- espelho de test_round1_only_group_retried_then_succeeds_with_valid_cross_side_group,
    agora com o same-side no lado Round 2."""
    r1_a = _current_claim("r1a", "openai", round_introduced=1)
    r2_a = _current_claim("r2a", "anthropic", round_introduced=2)
    r2_b = _current_claim("r2b", "gemini", round_introduced=2)
    same_side_payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r2_a.id, r2_b.id], "canonical_text": "fusão inválida"}],
            "ungrouped_claim_ids": [r1_a.id],
        }
    )
    valid_payload = json.dumps(
        {
            "groups": [
                {
                    "member_claim_ids": [r1_a.id, r2_a.id, r2_b.id],
                    "canonical_text": "fusão cross-round válida",
                }
            ],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", same_side_payload),
            text_response("anthropic", valid_payload),
        ],
    )

    canonical, attempts = await _reconcile(
        [r1_a], [r2_a, r2_b], provider, support_scope_model_count=3
    )

    assert len(attempts) == 2
    assert attempts[0].parse_status == "inconsistent_references"
    assert attempts[1].parse_status == "accepted"
    assert len(canonical) == 1
    assert set(canonical[0].merged_from_claim_ids) == {r1_a.id, r2_a.id, r2_b.id}


@pytest.mark.asyncio
async def test_mixed_group_sizes_are_all_valid_when_crossing_both_sides():
    """7 -- grupos válidos podem ter qualquer combinação não-vazia de
    cada lado (1+1, 2+1, 1+2, ...) -- NUNCA exatamente 2 membros, NUNCA
    contagem igual dos dois lados exigida."""
    r1_a = _current_claim("r1a", "openai", round_introduced=1)
    r1_b = _current_claim("r1b", "gemini", round_introduced=1)
    r2_a = _current_claim("r2a", "anthropic", round_introduced=2)
    r2_b = _current_claim("r2b", "mistral", round_introduced=2)
    # grupo 1: 2 R1 + 1 R2; grupo 2: 1 R1 + 2 R2 -- ambos válidos.
    valid_payload = json.dumps(
        {
            "groups": [
                {"member_claim_ids": [r1_a.id, r1_b.id, r2_a.id], "canonical_text": "grupo A"},
            ],
            "ungrouped_claim_ids": [r2_b.id],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", valid_payload)])

    canonical, attempts = await _reconcile(
        [r1_a, r1_b], [r2_a, r2_b], provider, support_scope_model_count=4
    )

    assert attempts[0].parse_status == "accepted"
    assert len(canonical) == 1
    assert set(canonical[0].merged_from_claim_ids) == {r1_a.id, r1_b.id, r2_a.id}


@pytest.mark.asyncio
async def test_cross_side_metadata_only_assigned_to_genuine_cross_round_merge():
    """14 -- confirma que uma claim canônica de reconciliação SÓ pode
    existir com membros de AMBOS os lados -- portanto seus metadados
    (round_introduced/total_models_in_round/support_scope_model_count)
    nunca são atribuídos a uma fusão same-side (que a validação
    estrutural já impede de sequer ser construída, ver testes acima).
    Este teste prova a consequência positiva: toda canônica que de fato
    é produzida tem exatamente essa propriedade."""
    r1_a = _current_claim("r1a", "openai", round_introduced=1)
    r2_a = _current_claim("r2a", "anthropic", round_introduced=2)
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_a.id, r2_a.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    round1_ids = {r1_a.id}
    round2_ids = {r2_a.id}

    canonical, _ = await _reconcile(
        [r1_a], [r2_a], provider, total_models_in_round=1, support_scope_model_count=2
    )

    assert len(canonical) == 1
    merged_ids = set(canonical[0].merged_from_claim_ids)
    # a canônica produzida SEMPRE intersecciona os dois lados -- nunca
    # só round1_ids, nunca só round2_ids.
    assert merged_ids & round1_ids
    assert merged_ids & round2_ids
    assert canonical[0].round_introduced == 2
    assert canonical[0].support_scope_model_count == 2


# ---------------------------------------------------------------------------
# Ordinary grouping regression (15) -- a restrição cross-side NUNCA se
# aplica a operation="grouping"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinary_grouping_still_allows_same_round_merge_of_two_claims():
    """15 -- group_claims (operation='grouping') continua livre pra
    fundir múltiplas claims da MESMA rodada -- a restrição cross-side é
    exclusiva de reconcile_claims, nunca vaza pro validador comum.
    Verificação direta via reconcile_claims não se aplica aqui -- ver
    tests/debate/test_grouping.py::test_group_of_two_or_more_creates_canonical_claim
    pra confirmação completa; este teste só documenta a distinção."""
    from app.debate.claim_extraction import group_claims

    a = _current_claim("a", "openai", round_introduced=1)
    b = _current_claim("b", "anthropic", round_introduced=1)
    payload = json.dumps(
        {"groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "fusão dentro do round 1"}], "ungrouped_claim_ids": []}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await group_claims(
        [a, b],
        round_number=1,
        grouper=provider,
        max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )

    assert attempts[0].parse_status == "accepted"
    assert len(canonical) == 1  # nunca rejeitado por ser "same-side" -- grouping não tem esse conceito


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_reconciliation_attempt_carries_request_provenance():
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [r1_claim.id, r2_claim.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    _canonical, attempts = await _reconcile([r1_claim], [r2_claim], provider)

    assert attempts[0].request_provenance is not None
    assert (
        attempts[0].request_provenance.contract_version
        == CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION
    )


@pytest.mark.asyncio
async def test_reconciliation_malformed_then_success_share_identical_request_provenance():
    r1_claim = _current_claim("r1", "openai", round_introduced=1)
    r2_claim = _current_claim("r2", "anthropic", round_introduced=2)
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "não é JSON"),
            text_response(
                "anthropic",
                json.dumps({"groups": [], "ungrouped_claim_ids": [r1_claim.id, r2_claim.id]}),
            ),
        ],
    )

    _canonical, attempts = await _reconcile([r1_claim], [r2_claim], provider)

    assert len(attempts) == 2
    assert attempts[0].request_provenance is not None
    assert attempts[0].request_provenance == attempts[1].request_provenance


def test_reconciliation_contract_version_is_distinct_from_grouping():
    """H (independência) -- reconciliação reusa o MESMO mecanismo de
    `group_claims`, mas nunca sua versão de contrato."""
    from app.debate.claim_extraction import CLAIM_GROUPING_CONTRACT_VERSION

    assert (
        CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION != CLAIM_GROUPING_CONTRACT_VERSION
    )
