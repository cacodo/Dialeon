from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from app.editor.schemas import EditorPlan


def test_editor_plan_accepts_valid_enum_combination():
    plan = EditorPlan(opening_style="direct", closing_style="concise")
    assert plan.opening_style == "direct"
    assert plan.closing_style == "concise"


@pytest.mark.parametrize(
    "opening_style,closing_style",
    [
        ("direct", "concise"),
        ("direct", "limitations_focused"),
        ("contextual", "concise"),
        ("contextual", "limitations_focused"),
    ],
)
def test_editor_plan_accepts_all_four_combinations(opening_style, closing_style):
    plan = EditorPlan(opening_style=opening_style, closing_style=closing_style)
    assert plan.opening_style == opening_style
    assert plan.closing_style == closing_style


def test_editor_plan_rejects_invalid_opening_style():
    with pytest.raises(ValidationError):
        EditorPlan(opening_style="poetic", closing_style="concise")


def test_editor_plan_rejects_invalid_closing_style():
    with pytest.raises(ValidationError):
        EditorPlan(opening_style="direct", closing_style="dramatic")


def test_editor_plan_requires_both_fields():
    with pytest.raises(ValidationError):
        EditorPlan(opening_style="direct")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EditorPlan(closing_style="concise")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# B4 (Etapa 7) regressão — o contrato antigo de prosa livre não pode
# ressurgir por nenhum campo, nem coexistir com o novo.
# ---------------------------------------------------------------------------


def test_editor_plan_rejects_old_narrative_field():
    with pytest.raises(ValidationError):
        EditorPlan(
            opening_style="direct",
            closing_style="concise",
            narrative="Esta afirmação está correta.",  # type: ignore[call-arg]
        )


def test_editor_plan_rejects_old_synthesis_fields():
    with pytest.raises(ValidationError):
        EditorPlan(
            opening_style="direct",
            closing_style="concise",
            synthesis_intro="x",  # type: ignore[call-arg]
            synthesis_conclusion="y",  # type: ignore[call-arg]
        )


def test_editor_plan_rejects_claim_narratives_field():
    with pytest.raises(ValidationError):
        EditorPlan(
            opening_style="direct",
            closing_style="concise",
            claim_narratives=[],  # type: ignore[call-arg]
        )


def test_editor_plan_rejects_verdict_reflected_field():
    with pytest.raises(ValidationError):
        EditorPlan(
            opening_style="direct",
            closing_style="concise",
            verdict_reflected="rejected",  # type: ignore[call-arg]
        )


def test_editor_plan_does_not_define_old_fields():
    assert "narrative" not in EditorPlan.model_fields
    assert "synthesis_intro" not in EditorPlan.model_fields
    assert "synthesis_conclusion" not in EditorPlan.model_fields
    assert "claim_narratives" not in EditorPlan.model_fields
    assert "verdict_reflected" not in EditorPlan.model_fields
    assert "claim_id" not in EditorPlan.model_fields


def test_editor_plan_has_no_arbitrary_string_field():
    """Prova estrutural, não um teste de payload específico: TODO campo
    de EditorPlan é um Literal finito -- não existe nenhum campo `str`
    livre pelo qual a LLM Editor possa emitir prosa voltada ao usuário
    (a garantia central da Etapa 17B, ver docstring do módulo)."""
    assert len(EditorPlan.model_fields) == 2
    for name, field in EditorPlan.model_fields.items():
        origin = typing.get_origin(field.annotation)
        assert origin is typing.Literal, (
            f"campo {name!r} de EditorPlan não é Literal (é {field.annotation!r}) -- "
            "todo campo do plano precisa ser um enum finito, nunca texto livre"
        )
