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

Repair (closure repair sobre 9464fdf, Blocker 1 -- "bind PrimaryAnswer to
accepted plan"): as checagens por-item abaixo (claim_id elegível,
claim_text/verdict_label batendo com a avaliação autoritativa) provam que
CADA item, isoladamente, é uma claim/rótulo válidos -- mas não provam que a
SELEÇÃO/PAPÉIS exatos persistidos correspondem ao que a tentativa de
planejamento REALMENTE aceita continha. Uma mutação coordenada podia trocar
um item selecionado por outra claim igualmente elegível, mover uma claim pra
outro papel compatível, remover um item, ou reordenar itens dentro de um
papel -- recomputando `rendered_text`/contagens/scope_note pra continuar
internamente coerente -- sem que nenhuma checagem por-item existente
percebesse, porque cada item isolado continuava válido.

A correção fecha essa lacuna reconstruindo o `PrimaryAnswerPlan` ACEITO a
partir do `raw_output_text` real da última tentativa de planejamento aceita
(`editor_result.primary_answer_attempts[-1]`, já exigida acima como ACEITA e
com a proveniência do contrato certo) -- NUNCA confiando no `PrimaryAnswer`
já persistido como fonte da seleção esperada -- validando esse plano
reconstruído através da MESMA `validate_plan` que a aplicação usaria ao vivo
(mesmas regras de elegibilidade/papel-veredito, nunca afrouxadas aqui), e
exigindo igualdade semântica EXATA entre o `PrimaryAnswer` reconstruído
deterministicamente a partir dele e o `PrimaryAnswer` persistido. O plano
aceito é a autoridade de seleção/apresentação; o `PrimaryAnswer` persistido
é só a sua realização renderizada, nunca o inverso.

Repair (closure repair, Blocker 2 -- "canonical limitation authority"): pela
mesma razão, `PrimaryAnswer.limitations` deixou de ser comparado só contra
`FinalAnswer.limitations` (dois registros que uma mutação coordenada podia
alterar em bloco, de forma consistente entre si, sem nunca ser comparados
contra a fonte real) -- os dois agora são exigidos iguais à derivação
CANÔNICA e autoritativa (`canonical_limitations`, app/editor/limitations.py:
`JudgeVerdict.debate_limitations` + nota de cobertura de extração
determinística), a MESMA função usada por `app/editor/compose.py` pra
produzir `FinalAnswer.limitations` em primeiro lugar.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.debate.claims import get_current_claims
from app.debate.result import DebateResult
from app.editor.errors import EditorError, MalformedEditorOutputError
from app.editor.limitations import canonical_limitations
from app.editor.primary_answer import (
    PRIMARY_ANSWER_CONTRACT_VERSION,
    InvalidPrimaryAnswerPlanError,
    count_omitted_not_established,
    eligible_assessed_claims,
    parse_primary_answer_plan,
    render_primary_answer,
    validate_plan,
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

    # Repair (closure repair, Blocker 2) -- equality chain contra a
    # derivação CANÔNICA (JudgeVerdict.debate_limitations + nota de
    # cobertura de extração determinística, app/editor/limitations.py),
    # nunca só primary_answer<->final_answer entre si (ver docstring do
    # módulo pra por que isso não bastava).
    authoritative_limitations = canonical_limitations(debate_result, verdict)
    if list(editor_result.final_answer.limitations) != authoritative_limitations:
        raise PrimaryAnswerCoherenceError(
            "limitations da resposta final divergem da derivação canônica autoritativa "
            "(JudgeVerdict.debate_limitations + cobertura de extração)"
        )
    if tuple(primary.limitations) != tuple(authoritative_limitations):
        raise PrimaryAnswerCoherenceError(
            "limitations do primary_answer divergem da derivação canônica autoritativa "
            "(JudgeVerdict.debate_limitations + cobertura de extração)"
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

    # Repair (closure repair, Blocker 1) -- reconstrói o PrimaryAnswer
    # ESPERADO a partir do plano REALMENTE aceito (raw_output_text da
    # última tentativa, já confirmada aceita e com a proveniência certa
    # acima), validado através da MESMA `validate_plan` autoritativa
    # (elegibilidade/papel-veredito NUNCA afrouxados aqui) e renderizado
    # deterministicamente -- e exige igualdade EXATA com o `PrimaryAnswer`
    # persistido. Isso fecha a lacuna que as checagens por-item acima
    # (cada item isolado válido) não fechavam: a seleção/papéis/ordem
    # exatos persistidos agora precisam corresponder ao plano aceito, não
    # só cada item individualmente ser uma claim elegível.
    accepted_attempt = attempts[-1]
    try:
        accepted_plan = parse_primary_answer_plan(accepted_attempt.raw_output_text)
        expected_selection = validate_plan(
            accepted_plan,
            verdict=verdict,
            current_claims=current_claims,
            all_claims=debate_result.claims,
        )
        expected_primary = render_primary_answer(
            expected_selection,
            based_on_verdict_id=verdict.id,
            limitations=tuple(authoritative_limitations),
        )
    except (MalformedEditorOutputError, InvalidPrimaryAnswerPlanError, ValidationError, ValueError) as exc:
        raise PrimaryAnswerCoherenceError(
            "primary_answer não pôde ser reconstruído a partir do plano aceito autoritativo "
            f"desta execução: {exc}"
        ) from exc
    if primary != expected_primary:
        raise PrimaryAnswerCoherenceError(
            "primary_answer diverge da reconstrução determinística do plano aceito "
            "(seleção/papel/ordem não correspondem à autoridade de apresentação)"
        )
