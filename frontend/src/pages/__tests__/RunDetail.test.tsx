import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { RunDetail } from '../RunDetail'
import { apiClient, ApiError } from '../../api/client'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    apiClient: {
      getProviders: vi.fn(),
      createRun: vi.fn(),
      listRuns: vi.fn(),
      getRun: vi.fn(),
      getRunAudit: vi.fn(),
    },
  }
})

beforeEach(() => {
  vi.mocked(apiClient.getRun).mockReset()
  vi.mocked(apiClient.getRunAudit).mockReset()
})

function renderDetail(runId = 'run-1') {
  return render(
    <MemoryRouter initialEntries={[`/runs/${runId}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

const completedRun = {
  status: 'completed' as const,
  id: 'run-1',
  started_at: '2026-09-06T00:00:00Z',
  completed_at: '2026-09-06T00:00:05Z',
  final_answer: {
    answer_text: 'Brasília é a capital do Brasil.',
    limitations: [],
    status: 'llm_composed' as const,
    editor_model: 'claude-sonnet-5',
    judge_confidence: 0.9,
  },
  accounting: {
    total_input_tokens: 100,
    total_output_tokens: 20,
    estimated_cost_usd: 0.01,
    has_unknown_accounting_components: false,
  },
  config: {
    question: 'Qual a capital do Brasil?',
    enabled_providers: ['openai'],
    claim_processor_provider: 'anthropic',
    judge_provider: 'anthropic',
    editor_provider: 'anthropic',
    source_analyzer_provider: 'anthropic',
    source_text: null,
    max_cost_usd: 1,
    max_total_tokens: 1000,
    max_output_tokens_per_call: 100,
    overall_timeout_seconds: 30,
    quorum: { min_for_debate: 1, min_to_return: 1 },
  },
}

const quorumRun = {
  status: 'insufficient_quorum' as const,
  id: 'run-2',
  started_at: '2026-09-06T00:00:00Z',
  failed_at: '2026-09-06T00:00:03Z',
  successful_count: 1,
  total_providers: 3,
  min_to_return: 2,
  accounting: {
    total_input_tokens: 40,
    total_output_tokens: 8,
    estimated_cost_usd: 0.002,
    has_unknown_accounting_components: true,
  },
  config: {
    question: 'Pergunta que falhou',
    enabled_providers: ['openai', 'anthropic', 'gemini'],
    claim_processor_provider: 'anthropic',
    judge_provider: 'anthropic',
    editor_provider: 'anthropic',
    source_analyzer_provider: 'anthropic',
    source_text: null,
    max_cost_usd: 1,
    max_total_tokens: 1000,
    max_output_tokens_per_call: 100,
    overall_timeout_seconds: 30,
    quorum: { min_for_debate: 2, min_to_return: 2 },
  },
}

describe('RunDetail', () => {
  it('mostra detail de uma run completed', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    renderDetail('run-1')

    expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
    expect(screen.getByText('Qual a capital do Brasil?')).toBeInTheDocument()
  })

  it('mostra detail de uma run insufficient_quorum', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(quorumRun)
    renderDetail('run-2')

    expect(await screen.findByText(/quórum insuficiente/i)).toBeInTheDocument()
    expect(screen.getByText(/1 de 3 participantes responderam/i)).toBeInTheDocument()
  })

  it('mostra 404 quando a run não existe', async () => {
    vi.mocked(apiClient.getRun).mockRejectedValue(
      new ApiError(404, 'run_not_found', 'não encontrada', null),
    )
    renderDetail('id-inexistente')

    expect(await screen.findByText(/execução não encontrada/i)).toBeInTheDocument()
  })

  it('carrega audit somente sob ação explícita (lazy)', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    vi.mocked(apiClient.getRunAudit).mockResolvedValue({
      status: 'completed',
      id: 'run-1',
      started_at: '2026-09-06T00:00:00Z',
      completed_at: '2026-09-06T00:00:05Z',
      config: completedRun.config,
      debate_outcome: { skipped_reason: null, cumulative_budget_exceeded: false },
      judge_outcome: { verdict_unavailable_reason: null, cumulative_budget_exceeded: false },
      editor_outcome: { fallback_reason: null, cumulative_budget_exceeded: false },
      source_analysis: null,
      initial_round: {
        responses: [],
        successful_count: 1,
        total_providers: 1,
        insufficient_data_for_consensus: false,
        budget_exceeded: false,
        accounting: {
          total_input_tokens: 100,
          total_output_tokens: 20,
          estimated_cost_usd: 0.01,
          has_unknown_accounting_components: false,
        },
      },
      critique_round: null,
      claims: [],
      claim_processing_attempts: [],
      judge_verdict: null,
      judge_attempts: [],
      editor_attempts: [],
      final_answer: completedRun.final_answer,
      accounting: completedRun.accounting,
    })
    renderDetail('run-1')

    await screen.findByText('Brasília é a capital do Brasil.')
    expect(apiClient.getRunAudit).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))
    expect(apiClient.getRunAudit).toHaveBeenCalledWith('run-1')
  })

  it('falha no audit não apaga o detail já carregado', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    vi.mocked(apiClient.getRunAudit).mockRejectedValue(
      new ApiError(500, 'internal_error', 'falhou', null),
    )
    renderDetail('run-1')

    await screen.findByText('Brasília é a capital do Brasil.')
    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))

    expect(await screen.findByText(/não foi possível carregar a inspeção/i)).toBeInTheDocument()
    // o detail continua visível, intacto
    expect(screen.getByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
  })

  it('mostra custo desconhecido corretamente (não como zero) no accounting parcial', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(quorumRun)
    renderDetail('run-2')

    await screen.findByText(/quórum insuficiente/i)
    expect(screen.getByText(/estimativa parcial/i)).toBeInTheDocument()
  })
})
