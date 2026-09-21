"""
Coerência ENTRE REGISTROS de um Primary Answer persistido.

`PrimaryAnswer` valida sua própria consistência INTERNA (rendered_text ==
render(sections), contagens == itens, papel/rótulo compatíveis), mas um objeto
internamente coerente pode reivindicar uma autoridade que o validador AO VIVO
do plano (`validate_plan`) jamais aceitaria -- p.ex. um `claim_id` trocado por
uma claim retirada, ou um texto/rótulo/contagem/limitação alterados em bloco
com o `rendered_text`. Esta é a ÚNICA função que confere o Primary Answer
contra os registros reais da MESMA execução; é chamada em dois pontos (sem
duplicar regra): na CONSTRUÇÃO/RECONSTRUÇÃO de `CouncilRunResult` (validator,
portanto também no reload do repositório, fail-closed) e no `save_success`
(pra objetos montados sem validação, ex. `model_copy`).

`None` (histórico, sem veredito, sem plano válido) nunca é verificado nem
reconstruído -- nenhum Primary Answer é fabricado ou reparado aqui.
"""

from __future__ import annotations

from app.debate.claims import get_current_claims
from app.debate.result import DebateResult
from app.editor.errors import EditorError
from app.editor.primary_answer import (
    PRIMARY_ANSWER_CONTRACT_VERSION,
    count_omitted_not_established,
    eligible_assessed_claims,
)
from app.editor.result import EditorResult
from app.judge.result import JudgeResult


class PrimaryAnswerCoherenceError(EditorError):
    """O Primary Answer persistido/construído contradiz os registros da execução."""


def validate_primary_answer_coherence(
    debate_result: DebateResult, judge_result: JudgeResult, editor_result: EditorResult
) -> None:
    primary = editor_result.final_answer.primary_answer
    if primary is None:
        return

    verdict = judge_result.verdict
    if verdict is None:
        raise PrimaryAnswerCoherenceError("primary_answer sem veredito aceito do Judge nesta execução")
    if primary.based_on_verdict_id != verdict.id:
        raise PrimaryAnswerCoherenceError(
            "primary_answer.based_on_verdict_id não é o veredito aceito desta execução"
        )

    all_ids = {claim.id for claim in debate_result.claims}
    current_claims = get_current_claims(debate_result.claims)
    current_ids = {claim.id for claim in current_claims}
    eligible = eligible_assessed_claims(verdict, current_claims)

    selected_ids: set[str] = set()
    for section in primary.sections:
        for item in section.items:
            if item.claim_id not in eligible:
                if item.claim_id not in all_ids:
                    reason = "não existe nesta execução"
                elif item.claim_id not in current_ids:
                    reason = "é uma claim retirada/substituída (não atual)"
                else:
                    reason = "não foi avaliada pelo Judge"
                raise PrimaryAnswerCoherenceError(f"claim {item.claim_id!r} {reason}")
            text, label = eligible[item.claim_id]
            if item.claim_text != text:
                raise PrimaryAnswerCoherenceError(
                    f"claim_text da claim {item.claim_id!r} diverge do texto autoritativo"
                )
            if item.verdict_label != label:
                raise PrimaryAnswerCoherenceError(
                    f"verdict_label da claim {item.claim_id!r} diverge da avaliação do Judge"
                )
            selected_ids.add(item.claim_id)

    if primary.assessed_claim_count != len(eligible):
        raise PrimaryAnswerCoherenceError("assessed_claim_count diverge das avaliações autoritativas")
    if primary.omitted_not_established_count != count_omitted_not_established(eligible, selected_ids):
        raise PrimaryAnswerCoherenceError(
            "omitted_not_established_count diverge das avaliações autoritativas"
        )
    if tuple(primary.limitations) != tuple(editor_result.final_answer.limitations):
        raise PrimaryAnswerCoherenceError(
            "limitations do primary_answer divergem das limitações da resposta final da execução"
        )

    attempts = editor_result.primary_answer_attempts
    if not attempts or attempts[-1].parse_status != "accepted":
        raise PrimaryAnswerCoherenceError(
            "primary_answer exige uma tentativa ACEITA de planejamento da resposta principal"
        )
    for attempt in attempts:
        provenance = attempt.request_provenance
        if provenance is None or provenance.contract_version != PRIMARY_ANSWER_CONTRACT_VERSION:
            raise PrimaryAnswerCoherenceError(
                "tentativa de planejamento sem proveniência do contrato "
                f"{PRIMARY_ANSWER_CONTRACT_VERSION!r}"
            )
