"""
Coerência ENTRE REGISTROS de um `NaturalAnswer` persistido -- mesma
disciplina de app/editor/primary_answer_coherence.py, uma camada acima:
`NaturalAnswer` valida sua PRÓPRIA forma (campos não vazios, ver
app/editor/natural_answer.py), mas um objeto internamente bem formado
ainda pode reivindicar um `rendered_text` que NÃO é o que o renderizador
determinístico da versão declarada produziria a partir do
`PrimaryAnswer` real desta execução -- p.ex. texto trocado à mão,
`based_on_verdict_id` apontando pro veredito errado, ou uma
`renderer_contract_version` inventada.

Depende do `PrimaryAnswer` já ser coerente (validado separadamente por
`validate_primary_answer_coherence`) -- este módulo NUNCA re-deriva nada
sobre claims/veredito/limitações a partir de `DebateResult`/`JudgeResult`;
ele só confere `NaturalAnswer` CONTRA o `PrimaryAnswer` que o acompanha na
mesma `FinalAnswer`, porque `NaturalAnswer` é, por contrato, uma função
pura e determinística SÓ do `PrimaryAnswer` (nenhuma outra entrada).

`None` (histórico, ou renderização opcional que não foi produzida) nunca é
verificado nem reconstruído/fabricado aqui.
"""

from __future__ import annotations

from app.editor.errors import EditorError
from app.editor.natural_answer import expected_rendered_text
from app.editor.result import FinalAnswer


class NaturalAnswerCoherenceError(EditorError):
    """O `NaturalAnswer` persistido/construído contradiz o `PrimaryAnswer`
    (já coerente) da mesma `FinalAnswer`."""


def validate_natural_answer_coherence(final_answer: FinalAnswer) -> None:
    natural = final_answer.natural_answer
    if natural is None:
        return

    primary = final_answer.primary_answer
    if primary is None:
        # Estruturalmente já rejeitado por FinalAnswer (ver
        # `_natural_answer_requires_a_primary_answer`), mas esta função
        # também é chamada sobre objetos montados sem essa validação
        # (ex. `model_copy`) -- mesma disciplina de
        # `validate_primary_answer_coherence`.
        raise NaturalAnswerCoherenceError(
            "natural_answer presente sem primary_answer correspondente nesta execução"
        )
    if natural.based_on_verdict_id != primary.based_on_verdict_id:
        raise NaturalAnswerCoherenceError(
            "natural_answer.based_on_verdict_id diverge de primary_answer.based_on_verdict_id"
        )
    try:
        expected = expected_rendered_text(natural.renderer_contract_version, primary)
    except ValueError as exc:
        raise NaturalAnswerCoherenceError(str(exc)) from exc
    if natural.rendered_text != expected:
        raise NaturalAnswerCoherenceError(
            "natural_answer.rendered_text diverge da renderização determinística da "
            f"versão {natural.renderer_contract_version!r} a partir do primary_answer atual"
        )
