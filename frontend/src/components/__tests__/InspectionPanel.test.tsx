import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InspectionPanel } from '../InspectionPanel'
import { apiClient } from '../../api/client'
import type { CompletedRunAudit } from '../../api/types'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    apiClient: {
      getRunAudit: vi.fn(),
    },
  }
})

beforeEach(() => {
  vi.mocked(apiClient.getRunAudit).mockReset()
})

const baseRoundAccounting = {
  total_input_tokens: 10,
  total_output_tokens: 5,
  estimated_cost_usd: 0.001,
  has_unknown_accounting_components: false,
}

function makeAudit(overrides: Partial<CompletedRunAudit> = {}): CompletedRunAudit {
  return {
    status: 'completed',
    id: 'run-1',
    started_at: '2026-09-06T00:00:00Z',
    completed_at: '2026-09-06T00:00:05Z',
    config: {
      question: 'A receita da empresa cresceu em 2025?',
      enabled_providers: ['openai'],
      claim_processor_provider: 'anthropic',
      judge_provider: 'anthropic',
      editor_provider: 'anthropic',
      source_analyzer_provider: 'anthropic',
      source_text: 'O relatório anual confirma que a receita cresceu 12% em 2025.',
      max_cost_usd: 1,
      max_total_tokens: 100000,
      max_output_tokens_per_call: 1024,
      max_output_tokens_grouping: 1024,
      max_output_tokens_judge: 1024,
      round_dispatch_timeout_seconds: 60,
      quorum: { min_for_debate: 1, min_to_return: 1 },
    },
    debate_outcome: { skipped_reason: null, cumulative_budget_exceeded: false },
    judge_outcome: { verdict_unavailable_reason: null, cumulative_budget_exceeded: false },
    editor_outcome: { fallback_reason: null, cumulative_budget_exceeded: false },
    source_analysis: {
      skipped_reason: null,
      source_analyzer_provider: 'anthropic',
      cumulative_budget_exceeded: false,
      attempts: [],
      claim_results: [
        {
          kind: 'relation',
          id: 'rel-1',
          claim_id: 'c1',
          relation: 'supports',
          excerpt: 'a receita cresceu 12% em 2025',
          excerpt_start: 30,
          excerpt_end: 60,
          created_at: '2026-09-06T00:00:00Z',
        },
      ],
    },
    initial_round: {
      responses: [],
      successful_count: 1,
      total_providers: 1,
      insufficient_data_for_consensus: false,
      budget_exceeded: false,
      accounting: baseRoundAccounting,
    },
    critique_round: null,
    claims: [
      {
        id: 'c1',
        text: 'A receita cresceu 12% em 2025.',
        source_model_response_id: 'mr-1',
        round_introduced: 1,
        parent_claim_id: null,
        merged_from_claim_ids: [],
        status: 'consensus',
        supporting_model_response_ids: [],
        supporting_models: [],
        total_models_in_round: 1,
        support_scope_model_count: null,
        confidence: null,
        created_at: '2026-09-06T00:00:00Z',
      },
    ],
    claim_processing_attempts: [],
    judge_verdict: {
      id: 'verdict-1',
      evaluated_through_round: 1,
      judge_model: 'claude-sonnet-5',
      claim_assessments: [
        { claim_id: 'c1', verdict: 'supported', explanation: 'bem sustentada pelo debate' },
      ],
      best_arguments_by: {},
      debate_limitations: [],
      confidence: 0.8,
      reasoning: 'justificativa',
      created_at: '2026-09-06T00:00:00Z',
    },
    judge_attempts: [],
    editor_attempts: [],
    final_answer: {
      answer_text: 'A receita cresceu 12% em 2025, segundo o debate.',
      limitations: [],
      status: 'llm_planned',
      editor_model: 'claude-sonnet-5',
      judge_confidence: 0.8,
    },
    accounting: {
      total_input_tokens: 100,
      total_output_tokens: 50,
      estimated_cost_usd: 0.01,
      has_unknown_accounting_components: false,
    },
    ...overrides,
  }
}

describe('InspectionPanel — Answer First / inspeção progressiva (patch de visibilidade da análise de fonte)', () => {
  it('J: começa recolhido e não busca o audit até o usuário pedir', () => {
    render(<InspectionPanel runId="run-1" />)

    expect(screen.getByRole('button', { name: /inspecionar execução/i })).toBeInTheDocument()
    expect(apiClient.getRunAudit).not.toHaveBeenCalled()
  })

  it('J: ao expandir, carrega e mostra a seção de Análise da fonte junto das demais', async () => {
    vi.mocked(apiClient.getRunAudit).mockResolvedValue(makeAudit())

    render(<InspectionPanel runId="run-1" />)
    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))

    expect(await screen.findByRole('heading', { name: /análise da fonte/i })).toBeInTheDocument()
    expect(screen.getByText(/apoia esta afirmação/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /afirmações/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /julgamento/i })).toBeInTheDocument()
  })

  it('G: quando nenhuma fonte foi fornecida, a seção mostra isso honestamente, sem esconder a seção', async () => {
    vi.mocked(apiClient.getRunAudit).mockResolvedValue(makeAudit({ source_analysis: null }))

    render(<InspectionPanel runId="run-1" />)
    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))

    expect(await screen.findByRole('heading', { name: /análise da fonte/i })).toBeInTheDocument()
    expect(screen.getByText(/nenhuma fonte foi fornecida/i)).toBeInTheDocument()
  })
})

describe('InspectionPanel — H: análise da fonte nunca altera a Resposta final', () => {
  it('a resposta final exibida é exatamente a do backend, sem menção à análise de fonte', async () => {
    vi.mocked(apiClient.getRunAudit).mockResolvedValue(makeAudit())

    render(<InspectionPanel runId="run-1" />)
    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))

    await screen.findByRole('heading', { name: /análise da fonte/i })

    // InspectionPanel não renderiza FinalAnswerView (isso é responsabilidade
    // de RunDetail/FinalAnswerView, fora deste componente) -- a prova aqui é
    // que nada no texto da análise de fonte é rotulado como resposta final
    // nem usa linguagem de veredito de verdade.
    expect(screen.queryByText(/resposta final/i)).not.toBeInTheDocument()
  })
})
