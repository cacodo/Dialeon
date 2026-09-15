import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReconciliationView } from '../ReconciliationView'
import ReconciliationViewSource from '../ReconciliationView.tsx?raw'
import type { ClaimPublic, SourceJudgeReconciliationResultPublic } from '../../api/types'

function makeClaim(overrides: Partial<ClaimPublic>): ClaimPublic {
  return {
    id: 'claim-1',
    text: 'A receita cresceu 12% em 2025.',
    source_model_response_id: 'mr-1',
    round_introduced: 1,
    parent_claim_id: null,
    merged_from_claim_ids: [],
    status: 'consensus',
    supporting_model_response_ids: [],
    supporting_models: [],
    total_models_in_round: 2,
    support_scope_model_count: null,
    confidence: null,
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

describe('ReconciliationView — Cross-Channel Reconciliation V1', () => {
  it('trata reconciliation=null como execução histórica, sem inventar nenhum estado', () => {
    render(<ReconciliationView reconciliation={null} claims={[]} />)

    expect(screen.getByText(/execução anterior a este recurso/i)).toBeInTheDocument()
  })

  it('status complete: mostra relacionamento e estado da fonte por claim, com referências de auditoria', () => {
    const claim = makeClaim({ id: 'c1', text: 'A receita cresceu 12% em 2025.' })
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'complete',
      claim_outcomes: [
        {
          claim_id: 'c1',
          judge_verdict_id: 'verdict-1',
          source_claim_result_ids: ['rel-1'],
          source_state: 'supports',
          channel_relationship: 'directionally_aligned',
        },
      ],
    }

    render(<ReconciliationView reconciliation={reconciliation} claims={[claim]} />)

    expect(screen.getByText('A receita cresceu 12% em 2025.')).toBeInTheDocument()
    expect(screen.getByText(/mesma direção/i)).toBeInTheDocument()
    expect(screen.getByText(/a fonte apoia esta afirmação/i)).toBeInTheDocument()
    expect(screen.getByText('verdict-1')).toBeInTheDocument()
    expect(screen.getByText('rel-1')).toBeInTheDocument()
  })

  it('status judge_unavailable: relationship é sempre not_comparable, mas source_state honesto continua visível', () => {
    const claim = makeClaim({ id: 'c1' })
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'judge_unavailable',
      claim_outcomes: [
        {
          claim_id: 'c1',
          judge_verdict_id: null,
          source_claim_result_ids: ['rel-1'],
          source_state: 'contradicts',
          channel_relationship: 'not_comparable',
        },
      ],
    }

    render(<ReconciliationView reconciliation={reconciliation} claims={[claim]} />)

    expect(screen.getByText(/o juiz não ficou disponível/i)).toBeInTheDocument()
    expect(screen.getByText(/não são comparáveis/i)).toBeInTheDocument()
    expect(screen.getByText(/a fonte contradiz esta afirmação/i)).toBeInTheDocument()
    // judge_verdict_id null nunca aparece como uma referência inventada.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('claim não encontrada nos dados desta execução degrada explicitamente, nunca some silenciosamente', () => {
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'complete',
      claim_outcomes: [
        {
          claim_id: 'claim-desconhecida',
          judge_verdict_id: 'verdict-1',
          source_claim_result_ids: [],
          source_state: 'not_supplied',
          channel_relationship: 'not_comparable',
        },
      ],
    }

    render(<ReconciliationView reconciliation={reconciliation} claims={[]} />)

    expect(screen.getByText(/não encontrada nos dados desta execução/i)).toBeInTheDocument()
  })

  it('claim_outcomes vazio não quebra, mostra mensagem honesta', () => {
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'complete',
      claim_outcomes: [],
    }

    render(<ReconciliationView reconciliation={reconciliation} claims={[]} />)

    expect(screen.getByText(/nenhuma afirmação corrente para reconciliar/i)).toBeInTheDocument()
  })

  it('nunca usa dangerouslySetInnerHTML/innerHTML (texto vindo do backend é sempre renderizado como texto)', () => {
    expect(ReconciliationViewSource).not.toContain('dangerouslySetInnerHTML')
    expect(ReconciliationViewSource).not.toContain('innerHTML')
  })

  // Hardening (revisão focada) -- caso representativo de "mixed": uma
  // relação válida (supports) coexistindo com uma entrada rejeitada pra
  // MESMA claim -- NÃO é necessariamente um conflito direcional
  // (supports+contradicts), mas a UI precisa continuar neutra mesmo
  // assim, nunca implicando que a fonte se contradiz ou contém
  // evidência conflitante.
  it('estado mixed genérico (supports + rejeitada) usa wording neutro, atribuído à análise, nunca ao texto da fonte', () => {
    const claim = makeClaim({ id: 'c1' })
    const reconciliation: SourceJudgeReconciliationResultPublic = {
      contract_version: 'source_judge_reconciliation_v1',
      status: 'complete',
      claim_outcomes: [
        {
          claim_id: 'c1',
          judge_verdict_id: 'verdict-1',
          source_claim_result_ids: ['rel-1', 'rejected-1'],
          source_state: 'mixed',
          channel_relationship: 'source_channel_conflict',
        },
      ],
    }

    render(<ReconciliationView reconciliation={reconciliation} claims={[claim]} />)

    expect(
      screen.getAllByText(/a análise da fonte produziu entradas que não puderam ser reduzidas/i)
        .length,
    ).toBeGreaterThan(0)
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toMatch(/a fonte (contém|produziu) resultados conflitantes/i)
    expect(bodyText).not.toMatch(/conflito interno na fonte/i)
    expect(bodyText).not.toMatch(/a fonte se contradiz/i)
    expect(bodyText).not.toMatch(/fonte estabelece/i)
  })
})
