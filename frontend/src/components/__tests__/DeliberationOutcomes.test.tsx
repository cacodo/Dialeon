import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DeliberationOutcomes } from '../DeliberationOutcomes'

const noOutcome = { skipped_reason: null, cumulative_budget_exceeded: false }
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
        debateOutcome={{ skipped_reason: 'insufficient_initial_quorum', cumulative_budget_exceeded: false }}
        judgeOutcome={noJudgeOutcome}
        editorOutcome={noEditorOutcome}
      />,
    )

    expect(screen.queryByText(/nenhum desvio material/i)).not.toBeInTheDocument()
    expect(screen.getByText(/debate:/i)).toBeInTheDocument()
  })
})
