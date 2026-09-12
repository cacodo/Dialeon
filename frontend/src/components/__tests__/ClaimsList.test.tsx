import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ClaimsList } from '../ClaimsList'
import type { ClaimPublic } from '../../api/types'

function makeClaim(overrides: Partial<ClaimPublic>): ClaimPublic {
  return {
    id: 'claim-1',
    text: 'Brasília é a capital do Brasil.',
    source_model_response_id: 'mr-1',
    round_introduced: 1,
    parent_claim_id: null,
    merged_from_claim_ids: [],
    status: 'consensus',
    supporting_model_response_ids: [{ model_response_id: 'mr-1', provider: 'openai', model: 'gpt-5.5' }],
    total_models_in_round: 2,
    confidence: null,
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

describe('ClaimsList — denominador de suporte', () => {
  it('usa claim.total_models_in_round, NUNCA um total externo da rodada inicial', async () => {
    // Claim introduzida na crítica (round_introduced=2) com
    // total_models_in_round DIFERENTE do total de participantes da
    // rodada inicial da execução como um todo -- o cenário exato do bug
    // relatado.
    const claim = makeClaim({
      round_introduced: 2,
      total_models_in_round: 1, // só 1 modelo participou da rodada em que essa claim foi introduzida
      supporting_model_response_ids: [
        { model_response_id: 'mr-critique-1', provider: 'anthropic', model: 'claude-sonnet-5' },
      ],
    })

    render(<ClaimsList claims={[claim]} assessmentsByClaimId={new Map()} />)

    await userEvent.click(screen.getByRole('button', { name: /brasília é a capital/i }))

    // 1 de 1 (o total da CLAIM), nunca "1 de 3" ou qualquer total externo
    expect(await screen.findByText('1 de 1 participantes')).toBeInTheDocument()
    expect(screen.queryByText(/1 de 3/)).not.toBeInTheDocument()
  })

  it('duas claims na mesma lista podem ter denominadores diferentes, cada uma correta', async () => {
    const claimFromInitialRound = makeClaim({
      id: 'claim-initial',
      text: 'Claim da rodada inicial.',
      total_models_in_round: 3,
      supporting_model_response_ids: [
        { model_response_id: 'r1', provider: 'openai', model: 'gpt-5.5' },
        { model_response_id: 'r2', provider: 'anthropic', model: 'claude-sonnet-5' },
      ],
    })
    const claimFromCritique = makeClaim({
      id: 'claim-critique',
      text: 'Claim da rodada de crítica.',
      round_introduced: 2,
      total_models_in_round: 1,
      supporting_model_response_ids: [
        { model_response_id: 'r3', provider: 'gemini', model: 'gemini-3.7-flash' },
      ],
    })

    render(
      <ClaimsList
        claims={[claimFromInitialRound, claimFromCritique]}
        assessmentsByClaimId={new Map()}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /rodada inicial/i }))
    await userEvent.click(screen.getByRole('button', { name: /rodada de crítica/i }))

    expect(await screen.findByText('2 de 3 participantes')).toBeInTheDocument()
    expect(await screen.findByText('1 de 1 participantes')).toBeInTheDocument()
  })
})
