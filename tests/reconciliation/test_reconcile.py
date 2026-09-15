"""
Testes de `reconcile_source_and_judge` -- Cross-Channel Reconciliation V1.

Cobre a mapping EXAUSTIVA (Judge verdict x source state), os estados
Judge-indisponível, ausência/indisponibilidade/rejeição de fonte, a
redução de grupos anômalos (duplicatas/rejeitados coexistindo com
relações válidas), e o fail-closed pra dado manual/persistido
inconsistente. Função PURA -- nenhum destes testes chama um provider
real.
"""

from __future__ import annotations

import pytest

from app.models.domain import ClaimAssessment
from app.reconciliation.errors import ReconciliationError
from app.reconciliation.models import ChannelRelationship, SourceChannelState
from app.reconciliation.reconcile import (
    _relationship_for_directional_source,
    reconcile_source_and_judge,
    validate_reconciliation_coherence,
)
from app.source_analysis.models import RejectedSourceEntry
from tests.editor.fixtures import (
    judge_result,
    raw_claim,
    source_analysis_result,
    source_relation,
    verdict,
)

_ALL_JUDGE_VERDICTS = [
    "supported",
    "partially_supported",
    "rejected",
    "conflicting",
    "unresolved",
]


def _single_claim_setup(judge_verdict: str):
    c1 = raw_claim("Claim única.", "resp-1", provider="openai")
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict=judge_verdict, explanation="ok")])
    jr = judge_result(v)
    return c1, jr


# ---------------------------------------------------------------------------
# Seção 21 -- full finite mapping test: todos os 5 vereditos x supports/
# contradicts (as duas linhas de "SOURCE supports"/"SOURCE contradicts" do
# contrato, seção 5).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "judge_verdict,expected",
    [
        ("supported", ChannelRelationship.DIRECTIONALLY_ALIGNED),
        ("partially_supported", ChannelRelationship.DIRECTIONALLY_ALIGNED),
        ("rejected", ChannelRelationship.IN_TENSION),
        ("conflicting", ChannelRelationship.SOURCE_ADDS_DIRECTION),
        ("unresolved", ChannelRelationship.SOURCE_ADDS_DIRECTION),
    ],
)
def test_source_supports_full_mapping(judge_verdict, expected):
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result([source_relation(c1.id, "supports")])

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.claim_outcomes[0].channel_relationship == expected
    assert result.claim_outcomes[0].source_state == SourceChannelState.SUPPORTS


@pytest.mark.parametrize(
    "judge_verdict,expected",
    [
        ("supported", ChannelRelationship.IN_TENSION),
        ("partially_supported", ChannelRelationship.IN_TENSION),
        ("rejected", ChannelRelationship.DIRECTIONALLY_ALIGNED),
        ("conflicting", ChannelRelationship.SOURCE_ADDS_DIRECTION),
        ("unresolved", ChannelRelationship.SOURCE_ADDS_DIRECTION),
    ],
)
def test_source_contradicts_full_mapping(judge_verdict, expected):
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result([source_relation(c1.id, "contradicts")])

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.claim_outcomes[0].channel_relationship == expected
    assert result.claim_outcomes[0].source_state == SourceChannelState.CONTRADICTS


@pytest.mark.parametrize("judge_verdict", _ALL_JUDGE_VERDICTS)
def test_source_unresolved_always_maps_to_source_unresolved(judge_verdict):
    """Item 8 do contrato -- qualquer Judge + source unresolved ->
    source_unresolved."""
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result([source_relation(c1.id, "unresolved")])

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.claim_outcomes[0].channel_relationship == ChannelRelationship.SOURCE_UNRESOLVED
    assert result.claim_outcomes[0].source_state == SourceChannelState.UNRESOLVED


@pytest.mark.parametrize("judge_verdict", _ALL_JUDGE_VERDICTS)
def test_source_mixed_always_maps_to_source_channel_conflict(judge_verdict):
    """Item 9 do contrato -- qualquer Judge + mixed -> source_channel_conflict."""
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result(
        [
            source_relation(c1.id, "supports"),
            source_relation(c1.id, "contradicts"),
        ]
    )

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.claim_outcomes[0].channel_relationship == (
        ChannelRelationship.SOURCE_CHANNEL_CONFLICT
    )
    assert result.claim_outcomes[0].source_state == SourceChannelState.MIXED


@pytest.mark.parametrize("judge_verdict", _ALL_JUDGE_VERDICTS)
def test_no_source_supplied_always_not_comparable(judge_verdict):
    """Item 10 do contrato."""
    c1, jr = _single_claim_setup(judge_verdict)

    result = reconcile_source_and_judge([c1], jr, None)

    assert result.claim_outcomes[0].channel_relationship == ChannelRelationship.NOT_COMPARABLE
    assert result.claim_outcomes[0].source_state == SourceChannelState.NOT_SUPPLIED


@pytest.mark.parametrize("judge_verdict", _ALL_JUDGE_VERDICTS)
def test_source_analysis_unavailable_always_not_comparable(judge_verdict):
    """Item 11 do contrato."""
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result(
        [], skipped_reason="source_analysis_transport_failed", attempts=[]
    )

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.claim_outcomes[0].channel_relationship == ChannelRelationship.NOT_COMPARABLE
    assert result.claim_outcomes[0].source_state == SourceChannelState.ANALYSIS_UNAVAILABLE


@pytest.mark.parametrize("judge_verdict", _ALL_JUDGE_VERDICTS)
def test_rejected_source_entry_always_not_comparable(judge_verdict):
    """Item 12 do contrato -- RejectedSourceEntry nunca vira supports/
    contradicts/unresolved só porque existe."""
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result(
        [RejectedSourceEntry(claim_id=c1.id, reason="omitted_by_model")]
    )

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.claim_outcomes[0].channel_relationship == ChannelRelationship.NOT_COMPARABLE
    assert result.claim_outcomes[0].source_state == SourceChannelState.ENTRY_REJECTED


# ---------------------------------------------------------------------------
# Seção 6/22 (itens 13/14) -- Judge indisponível: channel_relationship
# SEMPRE not_comparable, mas source_state permanece HONESTO (nunca apagado).
# ---------------------------------------------------------------------------


def test_judge_unavailable_with_supports_stays_not_comparable_but_source_state_honest():
    c1 = raw_claim("Claim única.", "resp-1", provider="openai")
    jr = judge_result(None)
    sa = source_analysis_result([source_relation(c1.id, "supports")])

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.status == "judge_unavailable"
    outcome = result.claim_outcomes[0]
    assert outcome.judge_verdict_id is None
    assert outcome.channel_relationship == ChannelRelationship.NOT_COMPARABLE
    assert outcome.source_state == SourceChannelState.SUPPORTS


def test_judge_unavailable_with_contradicts_stays_not_comparable_but_source_state_honest():
    c1 = raw_claim("Claim única.", "resp-1", provider="openai")
    jr = judge_result(None)
    sa = source_analysis_result([source_relation(c1.id, "contradicts")])

    result = reconcile_source_and_judge([c1], jr, sa)

    assert result.status == "judge_unavailable"
    outcome = result.claim_outcomes[0]
    assert outcome.judge_verdict_id is None
    assert outcome.channel_relationship == ChannelRelationship.NOT_COMPARABLE
    assert outcome.source_state == SourceChannelState.CONTRADICTS


def test_judge_unavailable_every_current_claim_still_gets_an_outcome():
    c1 = raw_claim("A", "resp-1", provider="openai")
    c2 = raw_claim("B", "resp-2", provider="openai")
    jr = judge_result(None)

    result = reconcile_source_and_judge([c1, c2], jr, None)

    assert result.status == "judge_unavailable"
    assert {o.claim_id for o in result.claim_outcomes} == {c1.id, c2.id}
    assert all(o.judge_verdict_id is None for o in result.claim_outcomes)
    assert all(
        o.channel_relationship == ChannelRelationship.NOT_COMPARABLE
        for o in result.claim_outcomes
    )


# ---------------------------------------------------------------------------
# Seção 23 -- estados anômalos/duplicados: nunca last-write-wins, nunca
# descarta um id.
# ---------------------------------------------------------------------------


def test_supports_plus_supports_retains_both_ids_state_supports():
    c1, jr = _single_claim_setup("supported")
    r1 = source_relation(c1.id, "supports", excerpt="trecho 1", excerpt_start=0, excerpt_end=8)
    r2 = source_relation(c1.id, "supports", excerpt="trecho 2", excerpt_start=9, excerpt_end=17)
    sa = source_analysis_result([r1, r2])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.SUPPORTS
    assert set(outcome.source_claim_result_ids) == {r1.id, r2.id}
    assert len(outcome.source_claim_result_ids) == 2


def test_contradicts_plus_contradicts_retains_both_ids_state_contradicts():
    c1, jr = _single_claim_setup("supported")
    r1 = source_relation(c1.id, "contradicts", excerpt="trecho 1", excerpt_start=0, excerpt_end=8)
    r2 = source_relation(c1.id, "contradicts", excerpt="trecho 2", excerpt_start=9, excerpt_end=17)
    sa = source_analysis_result([r1, r2])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.CONTRADICTS
    assert set(outcome.source_claim_result_ids) == {r1.id, r2.id}
    assert len(outcome.source_claim_result_ids) == 2


def test_supports_plus_contradicts_retains_both_ids_mixed_and_channel_conflict():
    c1, jr = _single_claim_setup("supported")
    r1 = source_relation(c1.id, "supports")
    r2 = source_relation(c1.id, "contradicts")
    sa = source_analysis_result([r1, r2])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.MIXED
    assert outcome.channel_relationship == ChannelRelationship.SOURCE_CHANNEL_CONFLICT
    assert set(outcome.source_claim_result_ids) == {r1.id, r2.id}


def test_rejected_plus_rejected_retains_both_ids_state_entry_rejected():
    c1, jr = _single_claim_setup("supported")
    r1 = RejectedSourceEntry(claim_id=c1.id, reason="omitted_by_model")
    r2 = RejectedSourceEntry(claim_id=c1.id, reason="invalid_entry", raw_entry={"x": 1})
    sa = source_analysis_result([r1, r2])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.ENTRY_REJECTED
    assert outcome.channel_relationship == ChannelRelationship.NOT_COMPARABLE
    assert set(outcome.source_claim_result_ids) == {r1.id, r2.id}


def test_valid_relation_coexisting_with_rejected_becomes_mixed_never_discards_ids():
    """Combinação anômala explícita (seção 8/9 do contrato): uma relação
    válida coexistindo com uma rejeição pra mesma claim NUNCA promove a
    relação válida silenciosamente nem descarta o id rejeitado -- vira
    mixed, retendo os dois ids."""
    c1, jr = _single_claim_setup("supported")
    r1 = source_relation(c1.id, "supports")
    r2 = RejectedSourceEntry(claim_id=c1.id, reason="duplicate_claim_id")
    sa = source_analysis_result([r1, r2])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.MIXED
    assert outcome.channel_relationship == ChannelRelationship.SOURCE_CHANNEL_CONFLICT
    assert set(outcome.source_claim_result_ids) == {r1.id, r2.id}


def test_unresolved_plus_supports_is_mixed_not_silently_reduced_to_supports():
    """Duas relações válidas DIFERENTES (unresolved + supports) pra
    mesma claim -- não redutível a um único estado coerente, vira mixed
    (nunca escolhe supports por ser "mais forte")."""
    c1, jr = _single_claim_setup("supported")
    r1 = source_relation(c1.id, "unresolved")
    r2 = source_relation(c1.id, "supports")
    sa = source_analysis_result([r1, r2])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.MIXED
    assert set(outcome.source_claim_result_ids) == {r1.id, r2.id}


# ---------------------------------------------------------------------------
# Cobertura/ordem/fail-closed
# ---------------------------------------------------------------------------


def test_outcome_order_matches_current_claims_order():
    c1 = raw_claim("Primeira.", "resp-1", provider="openai")
    c2 = raw_claim("Segunda.", "resp-2", provider="openai")
    c3 = raw_claim("Terceira.", "resp-3", provider="openai")
    v = verdict(
        [
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok"),
            ClaimAssessment(claim_id=c2.id, verdict="supported", explanation="ok"),
            ClaimAssessment(claim_id=c3.id, verdict="supported", explanation="ok"),
        ]
    )
    jr = judge_result(v)

    result = reconcile_source_and_judge([c1, c2, c3], jr, None)

    assert [o.claim_id for o in result.claim_outcomes] == [c1.id, c2.id, c3.id]


def test_no_duplicate_claim_outcomes():
    c1 = raw_claim("Única.", "resp-1", provider="openai")
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr = judge_result(v)

    result = reconcile_source_and_judge([c1], jr, None)

    ids = [o.claim_id for o in result.claim_outcomes]
    assert len(ids) == len(set(ids))


def test_unknown_claim_id_in_verdict_fails_closed():
    """JudgeVerdict referenciando um claim_id que não é mais corrente
    (dado manual/persistido violando a garantia real de
    SingleJudge._parse_and_validate) -- reconciliação recusa inventar
    uma comparação, levanta em vez de ignorar silenciosamente."""
    c1 = raw_claim("Única.", "resp-1", provider="openai")
    v = verdict(
        [ClaimAssessment(claim_id="claim-id-desconhecida", verdict="supported", explanation="ok")]
    )
    jr = judge_result(v)

    with pytest.raises(ReconciliationError):
        reconcile_source_and_judge([c1], jr, None)


def test_missing_assessment_for_current_claim_fails_closed():
    """Judge disponível mas sem NENHUMA avaliação pra uma claim corrente
    -- nunca selecionado arbitrariamente."""
    c1 = raw_claim("Avaliada.", "resp-1", provider="openai")
    c2 = raw_claim("Não avaliada.", "resp-2", provider="openai")
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr = judge_result(v)

    with pytest.raises(ReconciliationError):
        reconcile_source_and_judge([c1, c2], jr, None)


def test_complete_analysis_missing_result_for_current_claim_fails_closed():
    """Análise "concluída" (sem skipped_reason) mas sem NENHUM resultado
    pra uma claim corrente -- violação da garantia real do analyzer
    (`_build_claim_results` cobre toda claim corrente); reconciliação
    recusa inventar um estado."""
    c1 = raw_claim("Coberta.", "resp-1", provider="openai")
    c2 = raw_claim("Sem cobertura.", "resp-2", provider="openai")
    v = verdict(
        [
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok"),
            ClaimAssessment(claim_id=c2.id, verdict="supported", explanation="ok"),
        ]
    )
    jr = judge_result(v)
    sa = source_analysis_result([source_relation(c1.id, "supports")])  # c2 sem resultado

    with pytest.raises(ReconciliationError):
        reconcile_source_and_judge([c1, c2], jr, sa)


def test_unknown_entries_claim_id_none_never_matched_to_any_current_claim():
    """RejectedSourceEntry com claim_id=None (entrada não endereçável a
    nenhuma claim corrente, ver app/source_analysis/analyzer.py,
    `unknown_entries`) é estruturalmente irrelevante pra reconciliação --
    nunca conta como cobertura de nenhuma claim corrente."""
    c1 = raw_claim("Única.", "resp-1", provider="openai")
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr = judge_result(v)
    sa = source_analysis_result(
        [
            source_relation(c1.id, "supports"),
            RejectedSourceEntry(claim_id=None, reason="invalid_entry", raw_entry={"x": 1}),
        ]
    )

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.SUPPORTS
    assert len(outcome.source_claim_result_ids) == 1


def test_reconciliation_is_a_pure_function_no_provider_no_randomness():
    """Chamar a mesma entrada duas vezes produz um resultado
    estruturalmente idêntico (exceto o `id`/`created_at` do próprio
    SourceJudgeReconciliationResult, que são novos a cada chamada por
    design -- mesma disciplina de JudgeVerdict/FinalAnswer)."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result([source_relation(c1.id, "supports")])

    result_a = reconcile_source_and_judge([c1], jr, sa)
    result_b = reconcile_source_and_judge([c1], jr, sa)

    assert result_a.status == result_b.status
    assert result_a.claim_outcomes == result_b.claim_outcomes


# ---------------------------------------------------------------------------
# Repair #1 (revisão adversarial) -- um resultado de fonte referenciando um
# claim_id que não é corrente NUNCA pode desaparecer silenciosamente (o loop
# principal só itera `current_claims`) -- reconciliação recusa produzir um
# resultado "complete" que descartaria um registro real.
# ---------------------------------------------------------------------------


def test_unknown_valid_source_relation_claim_id_fails_closed():
    """A: resultado corrente válido + ValidSourceRelation pra claim
    desconhecida -- levanta em vez de simplesmente ignorar o extra."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result(
        [
            source_relation(c1.id, "supports"),
            source_relation("claim-desconhecida", "contradicts"),
        ]
    )

    with pytest.raises(ReconciliationError):
        reconcile_source_and_judge([c1], jr, sa)


def test_unknown_rejected_source_entry_with_non_null_claim_id_fails_closed():
    """B: resultado corrente válido + RejectedSourceEntry com claim_id
    não-nulo desconhecido -- levanta (diferente de claim_id=None, que é
    estruturalmente não-atribuível por design)."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result(
        [
            source_relation(c1.id, "supports"),
            RejectedSourceEntry(claim_id="claim-desconhecida", reason="invalid_entry"),
        ]
    )

    with pytest.raises(ReconciliationError):
        reconcile_source_and_judge([c1], jr, sa)


def test_rejected_source_entry_with_null_claim_id_is_not_an_unknown_claim():
    """C: RejectedSourceEntry(claim_id=None) continua tratada pela
    semântica pré-existente (nunca atribuída, nunca conta como cobertura)
    -- NUNCA confundida com um claim_id desconhecido (não deve levantar)."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result(
        [
            source_relation(c1.id, "supports"),
            RejectedSourceEntry(claim_id=None, reason="invalid_entry", raw_entry={"x": 1}),
        ]
    )

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state == SourceChannelState.SUPPORTS
    assert len(outcome.source_claim_result_ids) == 1


def test_superseded_non_current_claim_id_in_source_results_fails_closed():
    """D: resultado de fonte referenciando um claim_id que já foi
    superseded/revisado (não é mais corrente) -- levanta, nunca some."""
    c1 = raw_claim("Corrente.", "resp-1", provider="openai")
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr = judge_result(v)
    sa = source_analysis_result(
        [
            source_relation(c1.id, "supports"),
            source_relation("claim-revisada-nao-corrente", "supports"),
        ]
    )

    with pytest.raises(ReconciliationError):
        reconcile_source_and_judge([c1], jr, sa)


# ---------------------------------------------------------------------------
# Seção 13 -- teste de deriva de esquema (finite mapping): a tabela de
# mapeamento cobre EXAUSTIVAMENTE todo veredito atual do Judge x todo
# SourceChannelState direcional atual -- sem wildcard/default. Constrói/
# reconcilia cada combinação de verdade e afirma que existe um
# relacionamento explícito; se um novo membro de enum for adicionado sem
# mapeamento correspondente, este teste falha (em vez de aceitar
# silenciosamente um default).
# ---------------------------------------------------------------------------

_DIRECTIONAL_SOURCE_RELATIONS = ["supports", "contradicts"]


@pytest.mark.parametrize("judge_verdict", _ALL_JUDGE_VERDICTS)
@pytest.mark.parametrize("source_relation_kind", _DIRECTIONAL_SOURCE_RELATIONS)
def test_finite_mapping_covers_every_judge_verdict_x_directional_source_state(
    judge_verdict, source_relation_kind
):
    c1, jr = _single_claim_setup(judge_verdict)
    sa = source_analysis_result([source_relation(c1.id, source_relation_kind)])

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert isinstance(outcome.channel_relationship, ChannelRelationship)
    # Vazamento de wildcard/default seria estruturalmente inviável -- a
    # asserção real de exaustividade já é a chamada acima não levantar
    # `ReconciliationError` pra NENHUM veredito atual (ver
    # `_relationship_for_directional_source`, que levanta em vez de
    # escolher um default pra qualquer veredito fora do mapeamento).


def test_finite_mapping_exhaustively_covers_all_current_judge_verdicts():
    """Confirma que `_ALL_JUDGE_VERDICTS` (usado por todo o resto deste
    arquivo) é exatamente o domínio real do enum -- se um veredito novo
    for adicionado a `ClaimAssessment.verdict` sem atualizar esta lista,
    os testes acima deixariam de cobrir esse veredito silenciosamente."""
    from app.models.domain import ClaimAssessment as _CA

    literal_values = _CA.model_fields["verdict"].annotation
    # Literal[...] -- extrai os valores exatos do tipo fechado.
    import typing

    actual_verdicts = set(typing.get_args(literal_values))
    assert actual_verdicts == set(_ALL_JUDGE_VERDICTS)


# ---------------------------------------------------------------------------
# Hardening (revisão focada) -- SourceChannelState schema-drift guard,
# equivalente ao já existente pro Judge verdict acima. O Judge verdict já
# era guardado explicitamente contra deriva futura; SourceChannelState
# não era -- `_relationship_for_directional_source` tratava
# implicitamente qualquer estado não-SUPPORTS como CONTRADICTS (ver
# repair acima). Lista INDEPENDENTEMENTE declarada -- nunca importa o
# mapeamento de produção como "valor esperado" (cobertura tautológica).
# ---------------------------------------------------------------------------

_ALL_SOURCE_CHANNEL_STATES_V1 = [
    "not_supplied",
    "analysis_unavailable",
    "entry_rejected",
    "supports",
    "contradicts",
    "unresolved",
    "mixed",
]


def test_source_channel_state_enum_domain_is_exactly_the_seven_approved_v1_states():
    """Um `SourceChannelState` novo adicionado sem atualizar esta lista
    (declarada de forma independente, nunca lida do enum de produção)
    faz este teste falhar ANTES de qualquer coisa em reconcile.py
    precisar ser tocada -- mesma disciplina de
    `test_finite_mapping_exhaustively_covers_all_current_judge_verdicts`
    acima, agora do lado do source_state."""
    actual_states = {s.value for s in SourceChannelState}
    assert actual_states == set(_ALL_SOURCE_CHANNEL_STATES_V1)


def _setup_for_source_state(judge_verdict: str, source_state_name: str):
    """Constrói (claim, judge_result, source_analysis_result) reais que
    produzem EXATAMENTE `source_state_name` na reconciliação, pra um
    dado veredito do Judge."""
    c1, jr = _single_claim_setup(judge_verdict)
    if source_state_name == "not_supplied":
        return c1, jr, None
    if source_state_name == "analysis_unavailable":
        sa = source_analysis_result(
            [], skipped_reason="source_analysis_transport_failed", attempts=[]
        )
        return c1, jr, sa
    if source_state_name == "entry_rejected":
        sa = source_analysis_result(
            [RejectedSourceEntry(claim_id=c1.id, reason="omitted_by_model")]
        )
        return c1, jr, sa
    if source_state_name == "unresolved":
        sa = source_analysis_result([source_relation(c1.id, "unresolved")])
        return c1, jr, sa
    if source_state_name == "mixed":
        sa = source_analysis_result(
            [source_relation(c1.id, "supports"), source_relation(c1.id, "contradicts")]
        )
        return c1, jr, sa
    # "supports"/"contradicts"
    sa = source_analysis_result([source_relation(c1.id, source_state_name)])
    return c1, jr, sa


# Tabela EXPLICITAMENTE declarada de forma independente (nunca lê
# `_SUPPORTS_MAPPING`/`_CONTRADICTS_MAPPING` de produção) -- os 5
# vereditos atuais x os 7 SourceChannelState atuais, 35 combinações. Uma
# entrada removida, um veredito novo, ou um SourceChannelState novo sem
# entrada correspondente aqui faz este teste falhar.
_EXPECTED_RELATIONSHIP_FOR_VERDICT_AND_STATE: dict[tuple[str, str], ChannelRelationship] = {
    # source_state = not_supplied / analysis_unavailable / entry_rejected
    # -- sempre NOT_COMPARABLE, independente do veredito.
    **{
        (verdict_value, state): ChannelRelationship.NOT_COMPARABLE
        for verdict_value in _ALL_JUDGE_VERDICTS
        for state in ("not_supplied", "analysis_unavailable", "entry_rejected")
    },
    # source_state = unresolved -- sempre SOURCE_UNRESOLVED.
    **{
        (verdict_value, "unresolved"): ChannelRelationship.SOURCE_UNRESOLVED
        for verdict_value in _ALL_JUDGE_VERDICTS
    },
    # source_state = mixed -- sempre SOURCE_CHANNEL_CONFLICT.
    **{
        (verdict_value, "mixed"): ChannelRelationship.SOURCE_CHANNEL_CONFLICT
        for verdict_value in _ALL_JUDGE_VERDICTS
    },
    # source_state = supports (seção 5 do contrato original, "SOURCE supports").
    ("supported", "supports"): ChannelRelationship.DIRECTIONALLY_ALIGNED,
    ("partially_supported", "supports"): ChannelRelationship.DIRECTIONALLY_ALIGNED,
    ("rejected", "supports"): ChannelRelationship.IN_TENSION,
    ("conflicting", "supports"): ChannelRelationship.SOURCE_ADDS_DIRECTION,
    ("unresolved", "supports"): ChannelRelationship.SOURCE_ADDS_DIRECTION,
    # source_state = contradicts ("SOURCE contradicts").
    ("supported", "contradicts"): ChannelRelationship.IN_TENSION,
    ("partially_supported", "contradicts"): ChannelRelationship.IN_TENSION,
    ("rejected", "contradicts"): ChannelRelationship.DIRECTIONALLY_ALIGNED,
    ("conflicting", "contradicts"): ChannelRelationship.SOURCE_ADDS_DIRECTION,
    ("unresolved", "contradicts"): ChannelRelationship.SOURCE_ADDS_DIRECTION,
}


def test_expected_table_covers_the_full_5x7_domain():
    """A própria tabela de expectativa precisa cobrir as 35 combinações
    -- se este teste falhar, a tabela acima está incompleta, não o
    código de produção."""
    expected_keys = {
        (verdict_value, state)
        for verdict_value in _ALL_JUDGE_VERDICTS
        for state in _ALL_SOURCE_CHANNEL_STATES_V1
    }
    assert set(_EXPECTED_RELATIONSHIP_FOR_VERDICT_AND_STATE) == expected_keys


@pytest.mark.parametrize(
    "judge_verdict,source_state_name",
    [
        (verdict_value, state)
        for verdict_value in _ALL_JUDGE_VERDICTS
        for state in _ALL_SOURCE_CHANNEL_STATES_V1
    ],
)
def test_complete_5x7_judge_verdict_x_source_state_domain(judge_verdict, source_state_name):
    """C -- prova COMPORTAMENTAL (não introspecção de dict privado) de
    que toda combinação atual de veredito x source_state produz
    EXATAMENTE o relacionamento pretendido, comparado contra uma tabela
    declarada de forma independente. Um veredito novo, um
    SourceChannelState novo, ou uma entrada de mapeamento removida faz
    este teste falhar."""
    c1, jr, sa = _setup_for_source_state(judge_verdict, source_state_name)

    result = reconcile_source_and_judge([c1], jr, sa)

    outcome = result.claim_outcomes[0]
    assert outcome.source_state.value == source_state_name
    expected = _EXPECTED_RELATIONSHIP_FOR_VERDICT_AND_STATE[(judge_verdict, source_state_name)]
    assert outcome.channel_relationship == expected


# ---------------------------------------------------------------------------
# D -- regressão direta do helper: `_relationship_for_directional_source`
# precisa recusar um source_state não-direcional explicitamente, nunca
# tratá-lo implicitamente como CONTRADICTS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "non_directional_state",
    [
        SourceChannelState.MIXED,
        SourceChannelState.UNRESOLVED,
        SourceChannelState.NOT_SUPPLIED,
        SourceChannelState.ANALYSIS_UNAVAILABLE,
        SourceChannelState.ENTRY_REJECTED,
    ],
)
def test_directional_helper_rejects_non_directional_source_state(non_directional_state):
    with pytest.raises(ReconciliationError):
        _relationship_for_directional_source(non_directional_state, "supported")


# ---------------------------------------------------------------------------
# Seção 4/15 -- validate_reconciliation_coherence: valida um
# SourceJudgeReconciliationResult JÁ CONSTRUÍDO contra os inputs exatos.
# Recomputa via reconcile_source_and_judge (mesma implementação canônica) e
# compara -- nunca uma segunda tabela de regras.
# ---------------------------------------------------------------------------


def test_coherence_accepts_a_result_freshly_produced_by_reconcile():
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result([source_relation(c1.id, "supports")])
    result = reconcile_source_and_judge([c1], jr, sa)

    validate_reconciliation_coherence(result, [c1], jr, sa)  # não levanta


def test_coherence_rejects_unknown_source_claim_id_indirectly_via_recompute():
    """1: um resultado de fonte referenciando um claim_id desconhecido já
    faz `reconcile_source_and_judge` levantar internamente durante a
    recomputação -- a validação nunca aceita silenciosamente."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result([source_relation(c1.id, "supports")])
    result = reconcile_source_and_judge([c1], jr, sa)

    bad_sa = source_analysis_result(
        [source_relation(c1.id, "supports"), source_relation("outra-claim", "supports")]
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result, [c1], jr, bad_sa)


def test_coherence_rejects_unknown_judge_claim_id():
    """2: JudgeVerdict referenciando claim_id desconhecido -- recomputar
    contra os inputs reais levanta."""
    c1, jr = _single_claim_setup("supported")
    result = reconcile_source_and_judge([c1], jr, None)

    bad_v = verdict(
        [ClaimAssessment(claim_id="claim-desconhecida", verdict="supported", explanation="ok")]
    )
    bad_jr = judge_result(bad_v)
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result, [c1], bad_jr, None)


def test_coherence_rejects_missing_current_claim_outcome():
    """3: reconciliação não cobre uma claim corrente nova."""
    c1, jr = _single_claim_setup("supported")
    result = reconcile_source_and_judge([c1], jr, None)

    c2 = raw_claim("Nova claim corrente.", "resp-2", provider="openai")
    v2 = verdict(
        [
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok"),
            ClaimAssessment(claim_id=c2.id, verdict="supported", explanation="ok"),
        ]
    )
    jr2 = judge_result(v2)
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result, [c1, c2], jr2, None)


def test_coherence_rejects_extra_outcome_for_non_current_claim():
    """4: reconciliação tem um outcome extra pra uma claim que não é
    (mais) corrente."""
    c1, jr = _single_claim_setup("supported")
    c2 = raw_claim("Vai sumir.", "resp-2", provider="openai")
    v = verdict(
        [
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok"),
            ClaimAssessment(claim_id=c2.id, verdict="supported", explanation="ok"),
        ]
    )
    jr_both = judge_result(v)
    result = reconcile_source_and_judge([c1, c2], jr_both, None)

    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result, [c1], jr, None)


def test_coherence_rejects_wrong_judge_verdict_id():
    """5: outcome referencia um judge_verdict_id que não é o veredito
    real desta execução."""
    c1, jr = _single_claim_setup("supported")
    result = reconcile_source_and_judge([c1], jr, None)

    other_v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    other_jr = judge_result(other_v)
    tampered = result.model_copy(
        update={
            "claim_outcomes": [
                result.claim_outcomes[0].model_copy(
                    update={"judge_verdict_id": other_v.id}
                )
            ]
        }
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1], jr, None)


def test_coherence_rejects_source_result_id_that_does_not_exist():
    """6: outcome referencia um source_claim_result_id que não existe na
    SourceAnalysisResult real."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result([source_relation(c1.id, "supports")])
    result = reconcile_source_and_judge([c1], jr, sa)

    tampered = result.model_copy(
        update={
            "claim_outcomes": [
                result.claim_outcomes[0].model_copy(
                    update={"source_claim_result_ids": ("id-inexistente",)}
                )
            ]
        }
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1], jr, sa)


def test_coherence_rejects_source_result_belonging_to_another_claim():
    """7: outcome da claim A referencia um source_claim_result_id que na
    verdade pertence à claim B (reprodução direta do probe Codex --
    EXCERPT-FROM-B nunca pode ser aceito sob claim A)."""
    c1 = raw_claim("Claim A.", "resp-1", provider="openai")
    c2 = raw_claim("Claim B.", "resp-2", provider="openai")
    jr = judge_result(None)  # Judge indisponível -- isola a checagem em source.
    rel_a = source_relation(c1.id, "supports")
    rel_b = source_relation(c2.id, "supports")
    sa = source_analysis_result([rel_a, rel_b])
    result = reconcile_source_and_judge([c1, c2], jr, sa)

    outcome_a = next(o for o in result.claim_outcomes if o.claim_id == c1.id)
    outcome_b = next(o for o in result.claim_outcomes if o.claim_id == c2.id)
    tampered_a = outcome_a.model_copy(update={"source_claim_result_ids": (rel_b.id,)})
    tampered = result.model_copy(
        update={"claim_outcomes": [tampered_a, outcome_b]}
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1, c2], jr, sa)


def test_coherence_rejects_source_result_group_omitting_a_canonical_result():
    """8: source_claim_result_ids omite um id que o grupo canônico real
    contém (perda de dado)."""
    c1, jr = _single_claim_setup("supported")
    r1 = source_relation(c1.id, "supports")
    r2 = source_relation(c1.id, "supports")
    sa = source_analysis_result([r1, r2])
    result = reconcile_source_and_judge([c1], jr, sa)

    tampered = result.model_copy(
        update={
            "claim_outcomes": [
                result.claim_outcomes[0].model_copy(
                    update={"source_claim_result_ids": (r1.id,)}
                )
            ]
        }
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1], jr, sa)


def test_coherence_rejects_source_result_group_inventing_an_id():
    """9: source_claim_result_ids da claim A inclui um id que existe de
    verdade (pertencente à claim B, corrente), mas não faz parte do
    grupo reduzido real da claim A -- id "inventado" pra este outcome."""
    c1 = raw_claim("Claim A.", "resp-1", provider="openai")
    c2 = raw_claim("Claim B.", "resp-2", provider="openai")
    jr = judge_result(None)
    r1 = source_relation(c1.id, "supports")
    r2 = source_relation(c2.id, "supports")
    sa = source_analysis_result([r1, r2])
    result = reconcile_source_and_judge([c1, c2], jr, sa)

    outcome_a = next(o for o in result.claim_outcomes if o.claim_id == c1.id)
    outcome_b = next(o for o in result.claim_outcomes if o.claim_id == c2.id)
    tampered_a = outcome_a.model_copy(
        update={"source_claim_result_ids": (r1.id, r2.id)}
    )
    tampered = result.model_copy(update={"claim_outcomes": [tampered_a, outcome_b]})
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1, c2], jr, sa)


def test_coherence_rejects_source_state_disagreeing_with_canonical_reduction():
    """10: source_state divergente do que a redução real produziria."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result([source_relation(c1.id, "supports")])
    result = reconcile_source_and_judge([c1], jr, sa)

    tampered = result.model_copy(
        update={
            "claim_outcomes": [
                result.claim_outcomes[0].model_copy(
                    update={
                        "source_state": SourceChannelState.CONTRADICTS,
                        "source_claim_result_ids": result.claim_outcomes[0].source_claim_result_ids,
                    }
                )
            ]
        }
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1], jr, sa)


def test_coherence_rejects_relationship_disagreeing_with_mapping():
    """11: channel_relationship divergente do que o mapeamento canônico
    Judge x source_state produziria."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result([source_relation(c1.id, "supports")])
    result = reconcile_source_and_judge([c1], jr, sa)
    assert result.claim_outcomes[0].channel_relationship == ChannelRelationship.DIRECTIONALLY_ALIGNED

    tampered = result.model_copy(
        update={
            "claim_outcomes": [
                result.claim_outcomes[0].model_copy(
                    update={"channel_relationship": ChannelRelationship.IN_TENSION}
                )
            ]
        }
    )
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1], jr, sa)


def test_coherence_rejects_not_supplied_when_source_was_actually_supplied():
    """12: outcome diz not_supplied, mas uma SourceAnalysisResult real
    (mesmo que vazia/skipped) foi de fato fornecida -- deveria ser
    analysis_unavailable, nunca not_supplied."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result(
        [], skipped_reason="source_analysis_transport_failed", attempts=[]
    )
    result_no_source = reconcile_source_and_judge([c1], jr, None)

    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result_no_source, [c1], jr, sa)


def test_coherence_rejects_analysis_unavailable_when_source_was_not_supplied():
    """13: outcome diz analysis_unavailable, mas não havia fonte
    nenhuma -- deveria ser not_supplied."""
    c1, jr = _single_claim_setup("supported")
    sa = source_analysis_result(
        [], skipped_reason="source_analysis_transport_failed", attempts=[]
    )
    result_unavailable = reconcile_source_and_judge([c1], jr, sa)

    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result_unavailable, [c1], jr, None)


def test_coherence_rejects_complete_status_when_judge_unavailable():
    """14: status=complete mas o Judge real está indisponível."""
    c1 = raw_claim("Única.", "resp-1", provider="openai")
    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr_available = judge_result(v)
    result_complete = reconcile_source_and_judge([c1], jr_available, None)

    jr_unavailable = judge_result(None)
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result_complete, [c1], jr_unavailable, None)


def test_coherence_rejects_judge_unavailable_status_when_verdict_exists():
    """15: status=judge_unavailable mas o Judge real produziu um
    veredito."""
    c1 = raw_claim("Única.", "resp-1", provider="openai")
    jr_unavailable = judge_result(None)
    result_unavailable = reconcile_source_and_judge([c1], jr_unavailable, None)

    v = verdict([ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok")])
    jr_available = judge_result(v)
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result_unavailable, [c1], jr_available, None)


def test_coherence_rejects_current_claim_ordering_mismatch():
    """16: mesmo conjunto de claims correntes, ordem diferente -- ordem é
    parte do contrato público (mesma ordem de get_current_claims)."""
    c1 = raw_claim("Primeira.", "resp-1", provider="openai")
    c2 = raw_claim("Segunda.", "resp-2", provider="openai")
    v = verdict(
        [
            ClaimAssessment(claim_id=c1.id, verdict="supported", explanation="ok"),
            ClaimAssessment(claim_id=c2.id, verdict="supported", explanation="ok"),
        ]
    )
    jr = judge_result(v)
    result = reconcile_source_and_judge([c1, c2], jr, None)

    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(result, [c2, c1], jr, None)


def test_coherence_accepts_contract_version_v1():
    """Seção 4E -- contract_version precisa ser exatamente a v1 atual
    pra uma reconciliação recém-produzida (caminho normal, sem tampering)."""
    c1, jr = _single_claim_setup("supported")
    result = reconcile_source_and_judge([c1], jr, None)

    validate_reconciliation_coherence(result, [c1], jr, None)  # não levanta


def test_coherence_rejects_unsupported_contract_version():
    """Seção 4E -- um contract_version diferente da v1 atual (dado
    manual/persistido de uma versão futura/desconhecida) é rejeitado
    ANTES mesmo de qualquer recomputação -- fail closed, nunca
    reinterpretado como v1."""
    c1, jr = _single_claim_setup("supported")
    result = reconcile_source_and_judge([c1], jr, None)

    tampered = result.model_copy(update={"contract_version": "source_judge_reconciliation_v2"})
    with pytest.raises(ReconciliationError):
        validate_reconciliation_coherence(tampered, [c1], jr, None)
