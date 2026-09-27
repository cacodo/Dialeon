"""
Qual forma da resposta final do Conselho é apresentada como "a resposta".

Regra ÚNICA do backend (CLI humana e exportação legível usam esta função);
a interface web tem a mesma regra em `frontend/src/lib/answerPresentation.ts`
e as duas são conferidas contra a MESMA tabela de casos
(`frontend/src/lib/__tests__/answer_presentation_cases.json`), pra que nunca
divirjam em silêncio.

Ordem de fallback já estabelecida:

1. realização linguística, se ELEGÍVEL (e houver resposta principal);
2. resposta natural, se ELEGÍVEL (e houver resposta principal);
3. resposta principal estruturada;
4. avaliação completa (`answer_text`).

A elegibilidade vem pronta de `FinalAnswerPublic`
(`*_presentation_eligible`, calculada pela política ATUAL em
`app/presentation/mappers.py::final_answer_public`) -- esta função nunca a
recalcula nem escolhe uma forma inelegível. A escolha é a de AGORA (leitura),
não necessariamente a forma mostrada quando a execução terminou.

Também decide se o texto escolhido já carrega as limitações registradas (e
portanto uma seção separada seria duplicação), pelos campos estruturados --
nunca procurando o texto dentro da resposta:

- realização linguística: não carrega;
- resposta natural/principal: renderizadas da resposta principal, que carrega
  as próprias limitações; carrega se forem as mesmas da resposta final;
- avaliação completa: `llm_planned`/`deterministic_from_verdict` sempre ecoam
  as limitações em `answer_text` (app/editor/compose.py); os demais status não.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.presentation.schemas import FinalAnswerPublic

AnswerPresentationKind = Literal[
    "linguistic_realization", "natural_answer", "primary_answer", "complete_assessment"
]

_STATUSES_WHOSE_TEXT_INCLUDES_LIMITATIONS = frozenset({"llm_planned", "deterministic_from_verdict"})


@dataclass(frozen=True)
class AnswerPresentation:
    kind: AnswerPresentationKind
    # O texto da forma escolhida, exatamente como persistido (nunca reescrito).
    text: str
    # Se o texto já traz as limitações registradas da resposta final.
    text_carries_limitations: bool


def select_answer_presentation_kind(final_answer: FinalAnswerPublic) -> AnswerPresentationKind:
    if (
        final_answer.linguistic_realization is not None
        and final_answer.primary_answer is not None
        and final_answer.linguistic_realization_presentation_eligible is True
    ):
        return "linguistic_realization"
    if (
        final_answer.natural_answer is not None
        and final_answer.primary_answer is not None
        and final_answer.natural_answer_presentation_eligible is True
    ):
        return "natural_answer"
    if final_answer.primary_answer is not None:
        return "primary_answer"
    return "complete_assessment"


def select_answer_presentation(final_answer: FinalAnswerPublic) -> AnswerPresentation:
    kind = select_answer_presentation_kind(final_answer)
    if kind == "linguistic_realization":
        assert final_answer.linguistic_realization is not None
        return AnswerPresentation(kind, final_answer.linguistic_realization.rendered_text, False)
    if kind in ("natural_answer", "primary_answer"):
        primary = final_answer.primary_answer
        assert primary is not None
        carries = list(primary.limitations) == list(final_answer.limitations)
        if kind == "natural_answer":
            assert final_answer.natural_answer is not None
            return AnswerPresentation(kind, final_answer.natural_answer.rendered_text, carries)
        return AnswerPresentation(kind, primary.rendered_text, carries)
    return AnswerPresentation(
        kind,
        final_answer.answer_text,
        final_answer.status in _STATUSES_WHOSE_TEXT_INCLUDES_LIMITATIONS,
    )
