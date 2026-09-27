"""A regra da forma apresentada da resposta final do Conselho
(`app/presentation/answer_presentation.py`) conferida contra a MESMA tabela
de casos que a interface web usa (`frontend/src/lib/answerPresentation.ts`):
as duas implementações não podem divergir em silêncio."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.presentation.answer_presentation import (
    select_answer_presentation,
    select_answer_presentation_kind,
)

CASES_FILE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "lib"
    / "__tests__"
    / "answer_presentation_cases.json"
)
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


def _final_answer(case: dict) -> SimpleNamespace:
    """Só a PRESENÇA e as flags de elegibilidade decidem a forma."""
    return SimpleNamespace(
        linguistic_realization=object() if case["has_linguistic_realization"] else None,
        linguistic_realization_presentation_eligible=case["linguistic_realization_presentation_eligible"],
        natural_answer=object() if case["has_natural_answer"] else None,
        natural_answer_presentation_eligible=case["natural_answer_presentation_eligible"],
        primary_answer=object() if case["has_primary_answer"] else None,
    )


def test_the_shared_table_covers_every_combination():
    assert len(CASES) == 32
    assert {c["expected"] for c in CASES} == {
        "linguistic_realization",
        "natural_answer",
        "primary_answer",
        "complete_assessment",
    }


@pytest.mark.parametrize("case", CASES, ids=lambda c: json.dumps(c, sort_keys=True))
def test_backend_rule_matches_the_shared_table(case):
    assert select_answer_presentation_kind(_final_answer(case)) == case["expected"]


def _primary(limitations):
    return SimpleNamespace(rendered_text="principal", limitations=list(limitations))


@pytest.mark.parametrize(
    "fields, expected_kind, expected_text, carries",
    [
        (
            dict(linguistic_realization=SimpleNamespace(rendered_text="redigida"),
                 linguistic_realization_presentation_eligible=True, primary_answer=_primary(["L"])),
            "linguistic_realization", "redigida", False,
        ),
        (
            dict(natural_answer=SimpleNamespace(rendered_text="natural"),
                 natural_answer_presentation_eligible=True, primary_answer=_primary(["L"])),
            "natural_answer", "natural", True,
        ),
        (
            dict(natural_answer=SimpleNamespace(rendered_text="natural"),
                 natural_answer_presentation_eligible=True, primary_answer=_primary(["outra"])),
            "natural_answer", "natural", False,
        ),
        (dict(primary_answer=_primary(["L"])), "primary_answer", "principal", True),
        (dict(status="deterministic_from_verdict"), "complete_assessment", "completa", True),
        (dict(status="llm_planned"), "complete_assessment", "completa", True),
        (dict(status="deterministic_no_verdict"), "complete_assessment", "completa", False),
        (dict(status="llm_composed"), "complete_assessment", "completa", False),
    ],
)
def test_selected_text_and_whether_it_already_carries_the_limitations(fields, expected_kind, expected_text, carries):
    base = dict(
        linguistic_realization=None,
        linguistic_realization_presentation_eligible=False,
        natural_answer=None,
        natural_answer_presentation_eligible=False,
        primary_answer=None,
        answer_text="completa",
        limitations=["L"],
        status="llm_planned",
    )
    presentation = select_answer_presentation(SimpleNamespace(**{**base, **fields}))

    assert (presentation.kind, presentation.text, presentation.text_carries_limitations) == (
        expected_kind,
        expected_text,
        carries,
    )


def test_an_ineligible_realization_is_never_selected_even_when_present():
    fa = SimpleNamespace(
        linguistic_realization=SimpleNamespace(rendered_text="redigida"),
        linguistic_realization_presentation_eligible=False,
        natural_answer=None,
        natural_answer_presentation_eligible=False,
        primary_answer=_primary([]),
        answer_text="completa",
        limitations=[],
        status="llm_planned",
    )
    assert select_answer_presentation(fa).kind == "primary_answer"
