"""
Derivação CANÔNICA e determinística de limitações -- extraída de
app/editor/compose.py (Blocker 2 do closure repair sobre 9464fdf, ver
CLOSURE REPAIRS / NATURALANSWER) pra existir como UM helper puro e
compartilhado, nunca duas definições que pudessem divergir
silenciosamente.

Problema que este módulo fecha: antes deste repair,
`app/editor/primary_answer_coherence.py` conferia `PrimaryAnswer.limitations`
SÓ contra `FinalAnswer.limitations` já persistido -- nunca contra a
derivação AUTORITATIVA a partir do `JudgeVerdict`/`DebateResult` reais
desta execução. Uma mutação coordenada que alterasse/removesse
limitações em AMBOS os registros ao mesmo tempo (de forma consistente
entre si) passava incólume: os dois registros concordavam entre si, mas
nenhum dos dois era comparado contra a fonte real. Este módulo dá a
`primary_answer_coherence.py` (e a `compose.py`, que passa a IMPORTAR
daqui em vez de definir localmente) a MESMA função pura, pra que "os
dois concordam entre si" implique "os dois concordam com a fonte real".

Semântica (INALTERADA por este repair -- só o LOCAL do código mudou):
limitações canônicas = `JudgeVerdict.debate_limitations` (verbatim, na
ordem em que o Judge as listou) + UMA nota adicional, determinística e
app-autorada, sempre no FINAL da lista, quando a cobertura de extração
de claims ficou incompleta (ver
`DebateResult.claim_extraction_missing_response_count`). Nunca duas
notas, nunca reordenado, nunca a nota sozinha sem as limitações do Judge
antes dela.
"""

from __future__ import annotations

from app.debate.result import DebateResult
from app.models.domain import JudgeVerdict


def extraction_coverage_note(debate_result: DebateResult) -> str | None:
    """Repair (Run02 claim-extraction exhaustion) -- disclosure
    DETERMINÍSTICA de cobertura de extração incompleta: quando ao menos
    uma resposta bem-sucedida de participante não teve suas claims
    extraídas (ver `DebateResult.claim_extraction_missing_response_count`),
    a resposta final NUNCA deve dar a entender cobertura completa. `None`
    quando a cobertura é completa (`missing_response_count == 0`) -- nenhuma
    nota é anexada nesse caso, byte-idêntico ao comportamento de antes
    desta repair.

    Usada tanto pelos caminhos de `app/editor/compose.py` que produzem uma
    `FinalAnswer` (com ou sem veredito) quanto pela derivação autoritativa
    `canonical_limitations` abaixo (Blocker 2, closure repair)."""
    missing = debate_result.claim_extraction_missing_response_count
    if missing <= 0:
        return None
    eligible = debate_result.claim_extraction_eligible_response_count
    response_phrase = "resposta bem-sucedida" if eligible == 1 else "respostas bem-sucedidas"
    verb = "não pôde" if missing == 1 else "não puderam"
    return (
        f"Cobertura de extração de afirmações incompleta: {missing} de {eligible} "
        f"{response_phrase} dos participantes {verb} ter suas afirmações extraídas "
        "para avaliação -- o resultado acima considera só as afirmações que puderam "
        "ser extraídas."
    )


def limitations_with_coverage_note(
    base_limitations: list[str], coverage_note: str | None
) -> list[str]:
    """Repair (adversarial review, Finding B) -- ÚNICO ponto que combina
    as limitações VERBATIM do Judge (`base_limitations`) com a
    disclosure DETERMINÍSTICA e APP-AUTORADA de cobertura de extração
    (`coverage_note`, ver `extraction_coverage_note`).

    `coverage_note is None` (cobertura completa) devolve
    `base_limitations` inalterada -- NUNCA adiciona uma entrada vazia/
    duplicada quando não há nada a dizer sobre cobertura. Quando
    presente, é SEMPRE a ÚLTIMA entrada da lista -- as limitações do
    Judge (sobre o CONTEÚDO do debate) vêm primeiro, a limitação
    OPERACIONAL (sobre o PROCESSAMENTO de extração, nunca escrita/
    escolhida pela LLM) vem depois, nunca misturada/intercalada."""
    if coverage_note is None:
        return base_limitations
    return base_limitations + [coverage_note]


def canonical_limitations(debate_result: DebateResult, verdict: JudgeVerdict) -> list[str]:
    """Derivação ÚNICA e autoritativa de limitações sobre um veredito
    REAL -- usada tanto pela composição bem-sucedida/fallback (ver
    `Editor.compose`/`Editor._deterministic_from_verdict_answer`,
    app/editor/compose.py) quanto pela checagem de coerência entre
    registros do Primary Answer (`validate_primary_answer_coherence`,
    app/editor/primary_answer_coherence.py, Blocker 2 do closure
    repair). `verdict.debate_limitations` (verbatim, ordem do Judge) +
    nota de cobertura de extração (`extraction_coverage_note`), quando
    aplicável -- nunca uma segunda definição desta combinação."""
    return limitations_with_coverage_note(
        list(verdict.debate_limitations), extraction_coverage_note(debate_result)
    )
