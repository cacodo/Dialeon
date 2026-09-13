import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ClaimsList } from '../ClaimsList'
import type { ClaimPublic, ClaimSupportPublic } from '../../api/types'

// Mesma dedup por provider/model que o backend já faz em
// Claim.supporting_models (app/models/domain.py) -- usada aqui só pra
// computar um default CONSISTENTE quando um teste não passa
// supporting_models explicitamente, nunca uma segunda implementação
// incompatível sendo testada (o componente real nunca chama esta função
// -- ele só lê claim.supporting_models, já deduplicado pela API).
function dedupeProviderModel(supports: ClaimSupportPublic[]): string[] {
  const seen: string[] = []
  for (const s of supports) {
    const label = `${s.provider}/${s.model}`
    if (!seen.includes(label)) seen.push(label)
  }
  return seen
}

function makeClaim(overrides: Partial<ClaimPublic>): ClaimPublic {
  const supporting_model_response_ids = overrides.supporting_model_response_ids ?? [
    { model_response_id: 'mr-1', provider: 'openai', model: 'gpt-5.5' },
  ]
  return {
    id: 'claim-1',
    text: 'Brasília é a capital do Brasil.',
    source_model_response_id: 'mr-1',
    round_introduced: 1,
    parent_claim_id: null,
    merged_from_claim_ids: [],
    status: 'consensus',
    supporting_model_response_ids,
    supporting_models: dedupeProviderModel(supporting_model_response_ids),
    total_models_in_round: 2,
    support_scope_model_count: null,
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

  it('usa support_scope_model_count, não total_models_in_round, quando presente (claim de reconciliação cross-round)', async () => {
    // Claim canônica de reconciliação: total_models_in_round preserva o
    // valor de rodada ORDINÁRIA (2), mas o universo de suporte real
    // atravessa Round 1 + Round 2 (3) -- support_scope_model_count é o
    // denominador que deve aparecer, nunca total_models_in_round sozinho.
    const claim = makeClaim({
      round_introduced: 2,
      total_models_in_round: 2,
      support_scope_model_count: 3,
      merged_from_claim_ids: ['r1-claim', 'r2-claim'],
      supporting_model_response_ids: [
        { model_response_id: 'r1', provider: 'openai', model: 'gpt-5.5' },
        { model_response_id: 'r2', provider: 'anthropic', model: 'claude-sonnet-5' },
      ],
    })

    render(<ClaimsList claims={[claim]} assessmentsByClaimId={new Map()} />)

    await userEvent.click(screen.getByRole('button', { name: /brasília é a capital/i }))

    expect(await screen.findByText('2 de 3 participantes')).toBeInTheDocument()
    expect(screen.queryByText('2 de 2 participantes')).not.toBeInTheDocument()
  })

  // -------------------------------------------------------------------
  // Correção pós-revisão independente (HIGH 2) -- numerador de
  // participantes precisa ser claim.supporting_models (deduplicado por
  // provider/model), NUNCA claim.supporting_model_response_ids.length
  // (a lista bruta, não deduplicada por design).
  // -------------------------------------------------------------------

  it('mesmo provider/model repetido em múltiplos registros de suporte exibe 1 de 1, nunca 2 de 1', async () => {
    // 2 ClaimSupport (2 ModelResponse reais -- ex.: Round 1 + Round 2)
    // com o MESMO provider/model -- exatamente o cenário de reconciliação
    // cross-round que motivou esta correção. Se o numerador usasse
    // supporting_model_response_ids.length (bruto, =2), exibiria
    // "2 de 1 participantes" -- numerador > denominador, impossível.
    const claim = makeClaim({
      total_models_in_round: 1,
      supporting_model_response_ids: [
        { model_response_id: 'r1', provider: 'openai', model: 'gpt-5.5' },
        { model_response_id: 'r2', provider: 'openai', model: 'gpt-5.5' },
      ],
      // supporting_models já vem deduplicado pela API real -- makeClaim
      // computa isso automaticamente a partir de supporting_model_response_ids
      // acima (mesma dedup que o backend faz), então nem precisa ser
      // passado aqui explicitamente -- mas está expresso pra deixar
      // claro o que a API realmente devolveria.
      supporting_models: ['openai/gpt-5.5'],
    })

    render(<ClaimsList claims={[claim]} assessmentsByClaimId={new Map()} />)
    await userEvent.click(screen.getByRole('button', { name: /brasília é a capital/i }))

    expect(await screen.findByText('1 de 1 participantes')).toBeInTheDocument()
    expect(screen.queryByText('2 de 1 participantes')).not.toBeInTheDocument()
  })

  it('dois supporters provider/model distintos exibem 2 de 2', async () => {
    const claim = makeClaim({
      total_models_in_round: 2,
      supporting_model_response_ids: [
        { model_response_id: 'r1', provider: 'openai', model: 'gpt-5.5' },
        { model_response_id: 'r2', provider: 'anthropic', model: 'claude-sonnet-5' },
      ],
      supporting_models: ['openai/gpt-5.5', 'anthropic/claude-sonnet-5'],
    })

    render(<ClaimsList claims={[claim]} assessmentsByClaimId={new Map()} />)
    await userEvent.click(screen.getByRole('button', { name: /brasília é a capital/i }))

    expect(await screen.findByText('2 de 2 participantes')).toBeInTheDocument()
  })

  it('claim ordinária (support_scope_model_count null) cai pra total_models_in_round', async () => {
    const claim = makeClaim({
      total_models_in_round: 3,
      support_scope_model_count: null,
      supporting_model_response_ids: [
        { model_response_id: 'r1', provider: 'openai', model: 'gpt-5.5' },
      ],
      supporting_models: ['openai/gpt-5.5'],
    })

    render(<ClaimsList claims={[claim]} assessmentsByClaimId={new Map()} />)
    await userEvent.click(screen.getByRole('button', { name: /brasília é a capital/i }))

    expect(await screen.findByText('1 de 3 participantes')).toBeInTheDocument()
  })

  it('claim de reconciliação (support_scope_model_count presente) usa esse valor como denominador', async () => {
    const claim = makeClaim({
      round_introduced: 2,
      total_models_in_round: 1,
      support_scope_model_count: 2,
      merged_from_claim_ids: ['r1-claim', 'r2-claim'],
      supporting_model_response_ids: [
        { model_response_id: 'r1', provider: 'openai', model: 'gpt-5.5' },
        { model_response_id: 'r2', provider: 'openai', model: 'gpt-5.5' },
      ],
      supporting_models: ['openai/gpt-5.5'],
    })

    render(<ClaimsList claims={[claim]} assessmentsByClaimId={new Map()} />)
    await userEvent.click(screen.getByRole('button', { name: /brasília é a capital/i }))

    expect(await screen.findByText('1 de 2 participantes')).toBeInTheDocument()
    expect(screen.queryByText('1 de 1 participantes')).not.toBeInTheDocument()
  })
})
