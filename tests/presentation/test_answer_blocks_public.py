"""
UI Slice 3 (Structured Final Answer) -- fechamento do contrato público
(Repair, fechamento do contrato estruturado): `AnswerClaimItemPublic.verdict_label`/
`AnswerClaimSectionBlockPublic.heading` (app/presentation/schemas.py) usam
os MESMOS Literals fechados de app/editor/answer_blocks.py, nunca `str`
livre -- testa que o vocabulário público continua idêntico ao vocabulário
de domínio, e que o schema público rejeita um valor fora dele (a mesma
garantia que já existia internamente, agora também na fronteira da API).
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from app.editor.answer_blocks import AnswerSectionHeading, AnswerVerdictLabel
from app.presentation.schemas import AnswerClaimItemPublic, AnswerClaimSectionBlockPublic


def test_public_verdict_label_vocabulary_matches_domain_vocabulary_exactly():
    from app.presentation.schemas import AnswerClaimItemPublic

    public_label_type = AnswerClaimItemPublic.model_fields["verdict_label"].annotation
    assert set(get_args(public_label_type)) == set(get_args(AnswerVerdictLabel))


def test_public_section_heading_vocabulary_matches_domain_vocabulary_exactly():
    from app.presentation.schemas import AnswerClaimSectionBlockPublic

    public_heading_type = AnswerClaimSectionBlockPublic.model_fields["heading"].annotation
    assert set(get_args(public_heading_type)) == set(get_args(AnswerSectionHeading))


def test_public_claim_item_rejects_an_unknown_verdict_label():
    with pytest.raises(ValidationError):
        AnswerClaimItemPublic(
            claim_text="c",
            verdict_label="um rótulo qualquer",
            explanation="e",
            source_relationship_note=None,
        )


def test_public_claim_section_rejects_an_unknown_heading():
    item = AnswerClaimItemPublic(
        claim_text="c",
        verdict_label="sustentada pelo debate",
        explanation="e",
        source_relationship_note=None,
    )
    with pytest.raises(ValidationError):
        AnswerClaimSectionBlockPublic(kind="claim_section", heading="Um heading forjado:", items=[item])
