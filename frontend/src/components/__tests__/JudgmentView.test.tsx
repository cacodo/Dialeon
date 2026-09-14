import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JudgmentView } from '../JudgmentView'
import type { JudgeVerdictPublic } from '../../api/types'

function makeVerdict(overrides: Partial<JudgeVerdictPublic> = {}): JudgeVerdictPublic {
  return {
    id: 'verdict-1',
    evaluated_through_round: 1,
    judge_model: 'claude-sonnet-5',
    judge_model_identity_source: 'provider_reported',
    claim_assessments: [],
    best_arguments_by: {},
    debate_limitations: [],
    confidence: 0.8,
    reasoning: 'justificativa',
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

describe('JudgmentView — provenance de identidade do modelo do juiz', () => {
  it('mostra "reportado pelo provider" quando judge_model_identity_source é provider_reported', () => {
    render(<JudgmentView verdict={makeVerdict({ judge_model_identity_source: 'provider_reported' })} />)

    expect(screen.getByText(/reportado pelo provider/i)).toBeInTheDocument()
  })

  it('mostra "fallback do modelo solicitado" quando judge_model_identity_source é requested_fallback', () => {
    render(<JudgmentView verdict={makeVerdict({ judge_model_identity_source: 'requested_fallback' })} />)

    expect(screen.getByText(/fallback do modelo solicitado/i)).toBeInTheDocument()
  })

  it('histórico (null) renderiza honestamente "não registrada", nunca como fallback', () => {
    render(<JudgmentView verdict={makeVerdict({ judge_model_identity_source: null })} />)

    expect(screen.getByText(/não registrada/i)).toBeInTheDocument()
    expect(screen.queryByText(/fallback do modelo solicitado/i)).not.toBeInTheDocument()
  })
})
