import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DeliberationOutcomes } from '../DeliberationOutcomes'

const noOutcome = {
  skipped_reason: null,
  cumulative_budget_exceeded: false,
  claim_extraction_eligible_response_count: 0,
  claim_extraction_missing_response_count: 0,
}
const noJudgeOutcome = { verdict_unavailable_reason: null, cumulative_budget_exceeded: false }
const noEditorOutcome = { fallback_reason: null, cumulative_budget_exceeded: false }

describe('DeliberationOutcomes — nunca inventar "fluxo completo"', () => {
  it('quando nada foi registrado, mostra afirmação estritamente factual, não uma inferência de sucesso', () => {
    render(
      <DeliberationOutcomes
        debateOutcome={noOutcome}
        judgeOutcome={noJudgeOutcome}
        editorOutcome={noEditorOutcome}
      />,
    )

    // NÃO pode inferir "seguiu o fluxo completo" -- isso é mais forte do
    // que a ausência de reason fields garante
    expect(screen.queryByText(/fluxo completo/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/normalmente/i)).not.toBeInTheDocument()

    // deve mostrar algo estritamente suportado pela provenance
    expect(screen.getByText(/nenhum desvio material foi registrado/i)).toBeInTheDocument()
  })

  it('mostra o motivo real quando debate_outcome tem skipped_reason', () => {
    render(
      <DeliberationOutcomes
        debateOutcome={{
          skipped_reason: 'insufficient_initial_quorum',
          cumulative_budget_exceeded: false,
          claim_extraction_eligible_response_count: 0,
          claim_extraction_missing_response_count: 0,
        }}
        judgeOutcome={noJudgeOutcome}
        editorOutcome={noEditorOutcome}
      />,
    )

    expect(screen.queryByText(/nenhum desvio material/i)).not.toBeInTheDocument()
    expect(screen.getByText(/debate:/i)).toBeInTheDocument()
  })

  // Repair (Run02 claim-extraction exhaustion; Finding B da revisão
  // adversarial) -- cobertura de extração incompleta é uma degradação
  // REGISTRADA (backend-derivada) mesmo quando skipped_reason/
  // verdict_unavailable_reason/fallback_reason são todos null (a
  // crítica/Judge/Editor podem ter rodado normalmente com claims
  // sobreviventes, mesmo com cobertura incompleta) -- "Nenhum desvio
  // material" NUNCA pode aparecer nesse caso.
  it('cobertura de extração incompleta (missing > 0) nunca mostra "Nenhum desvio material", mesmo com todos os outros reason fields null', () => {
    render(
      <DeliberationOutcomes
        debateOutcome={{
          skipped_reason: null,
          cumulative_budget_exceeded: false,
          claim_extraction_eligible_response_count: 3,
          claim_extraction_missing_response_count: 1,
        }}
        judgeOutcome={noJudgeOutcome}
        editorOutcome={noEditorOutcome}
      />,
    )

    expect(screen.queryByText(/nenhum desvio material/i)).not.toBeInTheDocument()
    expect(screen.getByText(/extração de afirmações:/i)).toBeInTheDocument()
    expect(screen.getByText(/1 resposta de participante/i)).toBeInTheDocument()
  })

  it('cobertura de extração completa (missing == 0) nunca adiciona a linha de extração', () => {
    render(
      <DeliberationOutcomes
        debateOutcome={noOutcome}
        judgeOutcome={noJudgeOutcome}
        editorOutcome={noEditorOutcome}
      />,
    )

    expect(screen.queryByText(/extração de afirmações:/i)).not.toBeInTheDocument()
  })

  it('nunca recalcula a partir de tentativas brutas -- só o count backend-derivado, plural correto pra >1', () => {
    render(
      <DeliberationOutcomes
        debateOutcome={{
          skipped_reason: null,
          cumulative_budget_exceeded: false,
          claim_extraction_eligible_response_count: 5,
          claim_extraction_missing_response_count: 3,
        }}
        judgeOutcome={noJudgeOutcome}
        editorOutcome={noEditorOutcome}
      />,
    )

    expect(screen.getByText(/3 respostas de participantes/i)).toBeInTheDocument()
  })

  // Repair (adversarial review, recheck Finding B) -- "claim_extraction_incomplete"
  // precisa de um rótulo PT-BR legível na camada de formatting; este
  // teste prova que o COMPONENTE nunca deixa o valor interno cru
  // ("claim_extraction_incomplete") vazar pra tela, mesmo que a label
  // fosse esquecida no futuro (regressão direta do bug: antes do
  // repair, o fallback cru de `formatJudgeOutcome` apareceria aqui).
  it('judge_outcome com verdict_unavailable_reason="claim_extraction_incomplete" nunca vaza o valor interno cru', () => {
    render(
      <DeliberationOutcomes
        debateOutcome={noOutcome}
        judgeOutcome={{
          verdict_unavailable_reason: 'claim_extraction_incomplete',
          cumulative_budget_exceeded: false,
        }}
        editorOutcome={noEditorOutcome}
      />,
    )

    expect(screen.queryByText('claim_extraction_incomplete')).not.toBeInTheDocument()
    expect(screen.queryByText(/nenhum desvio material/i)).not.toBeInTheDocument()
    expect(screen.getByText(/julgamento:/i)).toBeInTheDocument()
  })
})
