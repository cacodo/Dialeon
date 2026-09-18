// EXPLAIN FROM PROVENANCE -- só mostra o que o backend realmente
// registrou como causa (debate_outcome/judge_outcome/editor_outcome).
// Quando não há nada a explicar (reason=null), a fase simplesmente não
// aparece aqui -- nunca inventamos "tudo correu normalmente" como texto.

import type { DebateOutcome, EditorOutcome, JudgeOutcome } from '../api/types'
import { formatDebateOutcome, formatEditorOutcome, formatJudgeOutcome } from '../api/formatting'

interface DeliberationOutcomesProps {
  debateOutcome: DebateOutcome
  judgeOutcome: JudgeOutcome
  editorOutcome: EditorOutcome
}

// Repair (Run02 claim-extraction exhaustion; Finding B da revisão
// adversarial) -- `claim_extraction_missing_response_count` é um sinal
// BACKEND-DERIVADO (ver DebateOutcome, ../api/types.ts) de degradação
// registrada, mesmo quando `debate_outcome.skipped_reason` é `null`
// (cobertura pode ficar incompleta sem que a crítica tenha sido pulada
// -- ver app/debate/claim_extraction_coverage.py). Nunca recalculado
// aqui a partir de tentativas brutas -- só o COUNT que o backend já
// derivou é lido.
function formatMissingExtractionCoverage(count: number): string | null {
  if (count <= 0) return null
  return count === 1
    ? '1 resposta de participante não pôde ter suas afirmações extraídas.'
    : `${count} respostas de participantes não puderam ter suas afirmações extraídas.`
}

export function DeliberationOutcomes({
  debateOutcome,
  judgeOutcome,
  editorOutcome,
}: DeliberationOutcomesProps) {
  const debateNote = formatDebateOutcome(debateOutcome)
  const judgeNote = formatJudgeOutcome(judgeOutcome)
  const editorNote = formatEditorOutcome(editorOutcome)
  const missingCoverageNote = formatMissingExtractionCoverage(
    debateOutcome.claim_extraction_missing_response_count,
  )

  const anyBudgetExceeded =
    debateOutcome.cumulative_budget_exceeded ||
    judgeOutcome.cumulative_budget_exceeded ||
    editorOutcome.cumulative_budget_exceeded

  if (!debateNote && !judgeNote && !editorNote && !missingCoverageNote && !anyBudgetExceeded) {
    return (
      <p className="deliberation-outcomes__normal">
        Nenhum desvio material foi registrado nos outcomes desta execução.
      </p>
    )
  }

  return (
    <ul className="deliberation-outcomes">
      {debateNote && <li>Debate: {debateNote}</li>}
      {judgeNote && <li>Julgamento: {judgeNote}</li>}
      {editorNote && <li>Edição: {editorNote}</li>}
      {missingCoverageNote && <li>Extração de afirmações: {missingCoverageNote}</li>}
      {anyBudgetExceeded && <li>O orçamento configurado foi atingido em algum momento da execução.</li>}
    </ul>
  )
}
