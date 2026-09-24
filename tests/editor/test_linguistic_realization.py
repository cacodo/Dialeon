"""
Linguistic Realization -- contrato fechado (`linguistic_realization_v1` /
`linguistic_semantic_review_v1`), validação estrutural determinística,
digest/render puros e construção de requests. Nenhuma chamada de provider
real (ver tests/editor/test_linguistic_realization_compose.py pra
integração com `Editor.compose()`).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.editor.linguistic_realization import (
    LINGUISTIC_REALIZATION_CONTRACT_VERSION,
    LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION,
    SEMANTIC_ISSUE_CODES,
    InvalidLinguisticRealizationError,
    LinguisticRealization,
    LinguisticRealizationBlockProposal,
    LinguisticRealizationProposal,
    LinguisticSemanticReview,
    build_linguistic_realization,
    build_linguistic_realization_request,
    build_semantic_review_request,
    candidate_digest,
    linguistic_realization_is_presentation_eligible,
    parse_realization_proposal,
    parse_semantic_review,
    primary_answer_digest,
    render_linguistic_realization_text,
    validate_realization_proposal,
)
from app.editor.primary_answer import (
    PRIMARY_ANSWER_ROLE_HEADINGS,
    VERDICT_LABELS,
    PrimaryAnswerItem,
    PrimaryAnswerSection,
    ValidatedSelection,
    render_primary_answer,
)

_SUPPORTED = VERDICT_LABELS["supported"]
_PARTIAL = VERDICT_LABELS["partially_supported"]
_UNRESOLVED = VERDICT_LABELS["unresolved"]


def _item(claim_id: str, text: str, label: str = _SUPPORTED) -> PrimaryAnswerItem:
    return PrimaryAnswerItem(claim_id=claim_id, claim_text=text, verdict_label=label)


def _section(role: str, items: list[PrimaryAnswerItem]) -> PrimaryAnswerSection:
    return PrimaryAnswerSection(
        role=role, heading=PRIMARY_ANSWER_ROLE_HEADINGS[role], items=tuple(items)
    )


def _primary(sections: list[PrimaryAnswerSection], *, limitations: tuple[str, ...] = ()):
    selected = sum(len(s.items) for s in sections)
    selection = ValidatedSelection(
        sections=tuple(sections), assessed_claim_count=selected, omitted_not_established_count=0
    )
    return render_primary_answer(
        selection, based_on_verdict_id="verdict-1", limitations=limitations
    )


def _single_claim_primary(text: str = "SaaS reduz a manutenção.", label: str = _SUPPORTED):
    return _primary([_section("central_conclusion", [_item("c-1", text, label)])])


def _proposal(blocks: list[dict]) -> LinguisticRealizationProposal:
    return LinguisticRealizationProposal.model_validate({"blocks": blocks})


# ---------------------------------------------------------------------------
# Schemas fechados
# ---------------------------------------------------------------------------


def test_realization_block_requires_non_empty_claim_ids_and_text():
    with pytest.raises(ValidationError):
        LinguisticRealizationBlockProposal(claim_ids=(), text="algo")
    with pytest.raises(ValidationError):
        LinguisticRealizationBlockProposal(claim_ids=("c-1",), text="")


def test_realization_proposal_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        LinguisticRealizationProposal.model_validate(
            {"blocks": [{"claim_ids": ["c-1"], "text": "t"}], "limitations": ["x"]}
        )


def test_realization_proposal_requires_at_least_one_block():
    with pytest.raises(ValidationError):
        LinguisticRealizationProposal.model_validate({"blocks": []})


def test_realization_proposal_rejects_malformed_json():
    with pytest.raises(Exception):
        parse_realization_proposal("isto não é json")


def test_realization_proposal_accepts_fenced_json():
    payload = json.dumps({"blocks": [{"claim_ids": ["c-1"], "text": "t"}]})
    fenced = f"```json\n{payload}\n```"
    proposal = parse_realization_proposal(fenced)
    assert proposal.blocks[0].claim_ids == ("c-1",)


def test_semantic_review_accept_forbids_issue_codes():
    with pytest.raises(ValidationError):
        LinguisticSemanticReview(
            candidate_digest="a" * 64, decision="accept", issue_codes=("semantic_omission",)
        )


def test_semantic_review_reject_requires_at_least_one_issue_code():
    with pytest.raises(ValidationError):
        LinguisticSemanticReview(candidate_digest="a" * 64, decision="reject", issue_codes=())


def test_semantic_review_rejects_duplicate_issue_codes():
    with pytest.raises(ValidationError):
        LinguisticSemanticReview(
            candidate_digest="a" * 64,
            decision="reject",
            issue_codes=("semantic_omission", "semantic_omission"),
        )


def test_semantic_review_rejects_out_of_vocabulary_issue_code():
    with pytest.raises(ValidationError):
        LinguisticSemanticReview.model_validate(
            {"candidate_digest": "a" * 64, "decision": "reject", "issue_codes": ["made_up_code"]}
        )


def test_semantic_review_rejects_malformed_digest_shape():
    with pytest.raises(ValidationError):
        LinguisticSemanticReview(candidate_digest="not-hex", decision="accept", issue_codes=())


def test_issue_code_vocabulary_is_closed_and_bounded():
    assert len(SEMANTIC_ISSUE_CODES) == len(set(SEMANTIC_ISSUE_CODES))
    assert len(SEMANTIC_ISSUE_CODES) < 20


def test_linguistic_realization_rejects_rendered_text_that_diverges_from_blocks():
    primary = _single_claim_primary()
    with pytest.raises(ValidationError):
        LinguisticRealization(
            based_on_primary_answer_digest=primary_answer_digest(primary),
            blocks=[{"claim_ids": ["c-1"], "text": "A"}],
            rendered_text="algo completamente diferente",
        )


# ---------------------------------------------------------------------------
# Validação estrutural determinística
# ---------------------------------------------------------------------------


def test_valid_one_block_per_claim_is_accepted():
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "Reescrita."}])
    validate_realization_proposal(proposal, primary=primary, question="Q?")  # não levanta


def test_unknown_claim_id_is_rejected():
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-forjado"], "text": "t"}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "claim_partition"


def test_duplicate_claim_id_is_rejected():
    primary = _primary(
        [_section("central_conclusion", [_item("c-1", "A"), _item("c-2", "B")])]
    )
    proposal = _proposal(
        [{"claim_ids": ["c-1"], "text": "x"}, {"claim_ids": ["c-1", "c-2"], "text": "y"}]
    )
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "claim_partition"


def test_missing_claim_id_is_rejected():
    primary = _primary(
        [_section("central_conclusion", [_item("c-1", "A"), _item("c-2", "B")])]
    )
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "x"}])  # c-2 nunca aparece
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "claim_partition"


def test_reordered_claim_id_is_rejected():
    primary = _primary(
        [_section("central_conclusion", [_item("c-1", "A"), _item("c-2", "B")])]
    )
    proposal = _proposal([{"claim_ids": ["c-2"], "text": "y"}, {"claim_ids": ["c-1"], "text": "x"}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "claim_partition"


def test_non_contiguous_grouping_is_rejected():
    primary = _primary(
        [
            _section(
                "central_conclusion",
                [_item("c-1", "A"), _item("c-2", "B")],
            ),
            _section("supporting_reasons", [_item("c-3", "C")]),
        ]
    )
    # c-1 e c-3 não são adjacentes na ordem de PrimaryAnswer.
    proposal = _proposal(
        [{"claim_ids": ["c-1", "c-3"], "text": "x"}, {"claim_ids": ["c-2"], "text": "y"}]
    )
    with pytest.raises(InvalidLinguisticRealizationError):
        validate_realization_proposal(proposal, primary=primary, question="Q?")


def test_cross_role_grouping_is_rejected():
    primary = _primary(
        [
            _section("central_conclusion", [_item("c-1", "A")]),
            _section("supporting_reasons", [_item("c-2", "B")]),
        ]
    )
    proposal = _proposal([{"claim_ids": ["c-1", "c-2"], "text": "x"}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "grouping"


def test_mixed_verdict_label_grouping_is_rejected():
    primary = _primary(
        [
            _section(
                "central_conclusion",
                [_item("c-1", "A", _SUPPORTED), _item("c-2", "B", _PARTIAL)],
            )
        ]
    )
    proposal = _proposal([{"claim_ids": ["c-1", "c-2"], "text": "x"}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "grouping"


def test_same_role_same_label_adjacent_grouping_is_accepted():
    primary = _primary(
        [
            _section(
                "central_conclusion",
                [_item("c-1", "A", _SUPPORTED), _item("c-2", "B", _SUPPORTED)],
            )
        ]
    )
    proposal = _proposal([{"claim_ids": ["c-1", "c-2"], "text": "Junto."}])
    validate_realization_proposal(proposal, primary=primary, question="Q?")  # não levanta


@pytest.mark.parametrize(
    "hostile",
    [
        "linha 1\nlinha 2",
        "com tab\tembutido",
        "controle‮embutido",  # right-to-left override
        "isolate⁦embutido",
        "nulo\x00embutido",
    ],
)
def test_unsafe_presentation_controls_are_rejected_never_sanitized(hostile):
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-1"], "text": hostile}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "unsafe_text"


def test_novel_numeric_literal_absent_from_claim_and_question_is_rejected():
    primary = _single_claim_primary("SaaS reduz a manutenção.")
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "Reduz custos em 42%."}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "numeric_literal"


def test_numeric_literal_present_in_mapped_claim_text_is_allowed():
    primary = _single_claim_primary("SaaS reduz custos em 42%.")
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "Reduz custos em 42%."}])
    validate_realization_proposal(proposal, primary=primary, question="Q?")  # não levanta


def test_numeric_literal_present_only_in_question_is_allowed():
    # A checagem é uma comparação MECÂNICA de token exato (ver docstring de
    # `validate_realization_proposal`) -- o literal precisa aparecer,
    # caractere por caractere, em algum lugar do texto permitido (pergunta
    # ou claims mapeadas do bloco); pontuação colada muda o token.
    primary = _single_claim_primary("SaaS reduz a manutenção.")
    proposal = _proposal(
        [{"claim_ids": ["c-1"], "text": "O ano citado foi 2024 no debate."}]
    )
    validate_realization_proposal(
        proposal, primary=primary, question="O ano é 2024 no relatório?"
    )  # não levanta


def test_numeric_literal_from_a_different_claim_not_in_this_block_is_rejected():
    primary = _primary(
        [
            _section(
                "central_conclusion",
                [_item("c-1", "SaaS reduz a manutenção."), _item("c-2", "Custa 42 dólares.")],
            )
        ]
    )
    proposal = _proposal(
        [{"claim_ids": ["c-1"], "text": "Custa 42 dólares."}, {"claim_ids": ["c-2"], "text": "Y"}]
    )
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    assert exc.value.feedback_code == "numeric_literal"


def test_to_feedback_never_echoes_raw_exception_text_and_uses_closed_vocabulary():
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-forjado"], "text": "t"}])
    with pytest.raises(InvalidLinguisticRealizationError) as exc:
        validate_realization_proposal(proposal, primary=primary, question="Q?")
    feedback = exc.value.to_feedback()
    assert "c-forjado" not in feedback
    assert str(exc.value) not in feedback


# ---------------------------------------------------------------------------
# Digest / render puros
# ---------------------------------------------------------------------------


def test_primary_answer_digest_is_deterministic_and_sensitive_to_content():
    primary_a = _single_claim_primary("Texto A.")
    primary_b = _single_claim_primary("Texto A.")
    primary_c = _single_claim_primary("Texto diferente.")
    assert primary_answer_digest(primary_a) == primary_answer_digest(primary_b)
    assert primary_answer_digest(primary_a) != primary_answer_digest(primary_c)


def test_candidate_digest_changes_with_blocks_or_primary_digest():
    primary = _single_claim_primary()
    based_on = primary_answer_digest(primary)
    p1 = _proposal([{"claim_ids": ["c-1"], "text": "A"}])
    p2 = _proposal([{"claim_ids": ["c-1"], "text": "B"}])
    d1 = candidate_digest(p1, based_on_primary_answer_digest=based_on)
    d2 = candidate_digest(p2, based_on_primary_answer_digest=based_on)
    d3 = candidate_digest(p1, based_on_primary_answer_digest="f" * 64)
    assert d1 != d2
    assert d1 != d3
    assert d1 == candidate_digest(p1, based_on_primary_answer_digest=based_on)


def test_render_linguistic_realization_text_joins_blocks_with_blank_line():
    primary = _primary(
        [_section("central_conclusion", [_item("c-1", "A"), _item("c-2", "B")])]
    )
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "Primeiro."}, {"claim_ids": ["c-2"], "text": "Segundo."}])
    realization = build_linguistic_realization(proposal, primary=primary)
    assert realization.rendered_text == "Primeiro.\n\nSegundo."
    assert render_linguistic_realization_text(realization.blocks) == realization.rendered_text


def test_build_linguistic_realization_binds_digest_to_the_full_primary_answer():
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "A"}])
    realization = build_linguistic_realization(proposal, primary=primary)
    assert realization.based_on_primary_answer_digest == primary_answer_digest(primary)
    assert realization.contract_version == LINGUISTIC_REALIZATION_CONTRACT_VERSION


def test_presentation_eligibility_requires_matching_primary_answer_digest():
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "A"}])
    realization = build_linguistic_realization(proposal, primary=primary)
    assert linguistic_realization_is_presentation_eligible(realization, primary) is True

    other_primary = _single_claim_primary("Outro texto inteiramente.")
    assert linguistic_realization_is_presentation_eligible(realization, other_primary) is False


# ---------------------------------------------------------------------------
# Construção de requests -- fronteira dado/instrução
# ---------------------------------------------------------------------------


def test_realization_request_never_leaks_forbidden_signals():
    primary = _primary(
        [_section("central_conclusion", [_item("c-1", "SaaS reduz a manutenção.")])],
        limitations=("Sem dados empíricos.",),
    )
    request = build_linguistic_realization_request("Qual abordagem?", primary, 1024)
    body = request.messages[0].content

    assert "SaaS reduz a manutenção." in body
    assert "Sem dados empíricos." in body
    assert "Qual abordagem?" in body
    for forbidden in (
        "supporting_model_ratio",
        "explanation",
        "source_analysis",
        "confidence",
        "NaturalAnswer",
    ):
        assert forbidden not in body
    assert request.minimal_reasoning is True and request.max_tokens == 1024


def test_realization_request_includes_bounded_rejection_feedback_when_supplied():
    primary = _single_claim_primary()
    request = build_linguistic_realization_request(
        "Q?", primary, 1024, rejection_feedback="Siga a partição exata."
    )
    body = request.messages[0].content
    assert "Siga a partição exata." in body


def test_realization_request_without_feedback_omits_retry_section():
    primary = _single_claim_primary()
    request = build_linguistic_realization_request("Q?", primary, 1024)
    body = request.messages[0].content
    assert "REJEICAO_ESTRUTURAL_ANTERIOR" not in body


def test_semantic_review_request_carries_candidate_and_digest_as_data():
    primary = _single_claim_primary()
    proposal = _proposal([{"claim_ids": ["c-1"], "text": "A"}])
    digest = candidate_digest(
        proposal, based_on_primary_answer_digest=primary_answer_digest(primary)
    )
    request = build_semantic_review_request("Q?", primary, proposal, digest, 1024)
    body = request.messages[0].content

    assert digest in body
    assert '"claim_ids"' in body
    for code in SEMANTIC_ISSUE_CODES:
        assert code in (request.system_prompt or "")


def test_parse_semantic_review_rejects_malformed_json():
    with pytest.raises(Exception):
        parse_semantic_review("não é json")


def test_parse_semantic_review_roundtrips_a_valid_accept():
    payload = json.dumps({"candidate_digest": "a" * 64, "decision": "accept", "issue_codes": []})
    review = parse_semantic_review(payload)
    assert review.decision == "accept" and review.issue_codes == ()
