// Direct Answer Execution V1 -- páginas: envio pela Home, página da run direta
// (concluída, sem resposta, sem desfecho), reuso e Histórico.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { Home } from '../Home'
import { RunDetail } from '../RunDetail'
import { History } from '../History'
import { apiClient, ApiError } from '../../api/client'
import type {
  DirectCompletedRunResponse,
  DirectFailedRunResponse,
  DirectRunningRunResponse,
  ModelResponsePublic,
} from '../../api/types'

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
  for (const fn of Object.values(apiClient)) vi.mocked(fn).mockReset()
  vi.mocked(apiClient.getProviders).mockResolvedValue({
    providers: ['anthropic', 'openai'],
    local_prerequisites: { anthropic: 'met', openai: 'met' },
  })
})

const config = {
  question: 'Qual a capital do Brasil?',
  provider: 'openai',
  requested_model: 'gpt-conf',
  max_output_tokens: 4096,
}

const response: ModelResponsePublic = {
  id: 'mr-1',
  provider: 'openai',
  requested_model: 'gpt-conf',
  model: 'gpt-conf-2026-09-01',
  model_identity_source: 'provider_reported',
  round_number: 1,
  status: 'success',
  response_text: 'Brasília.\n\nÉ a capital desde 1960.',
  usage: { input_tokens: 12, output_tokens: 8 },
  cost_usd: 0.0001,
  pricing_provenance: {
    source_id: 'tabela-teste',
    tier: 'standard',
    input_rate_usd_per_million_tokens: 1,
    output_rate_usd_per_million_tokens: 2,
    canonical_model_id: null,
  },
  latency_ms: 420,
  attempts: 1,
  error: null,
  had_uncertain_prior_attempts: false,
  provider_finish_reason: 'stop',
  request_provenance: { contract_version: 'direct_answer_v1', request_digest: 'completion-request-sha256-v2:abc' },
  created_at: '2026-09-01T12:00:01Z',
} as ModelResponsePublic

const policy = { attempt_timeout_seconds: 60, max_transport_attempts_per_completion: 3, judge_override: null }

const completed: DirectCompletedRunResponse = {
  kind: 'direct',
  status: 'completed',
  id: 'direct-1',
  started_at: '2026-09-01T12:00:00Z',
  completed_at: '2026-09-01T12:00:02Z',
  config,
  answer: 'Brasília.\n\nÉ a capital desde 1960.',
  response,
  accounting: {
    total_input_tokens: 12,
    total_output_tokens: 8,
    estimated_cost_usd: 0.0001,
    has_unknown_accounting_components: false,
  },
  provider_execution_policy: policy,
}

const failed: DirectFailedRunResponse = {
  kind: 'direct',
  status: 'failed',
  id: 'direct-2',
  started_at: '2026-09-01T12:00:00Z',
  failed_at: '2026-09-01T12:01:00Z',
  config,
  failure_stage: 'provider',
  failure_reason: 'timeout',
  message: 'A chamada ao provider excedeu o tempo limite.',
  response: {
    ...response,
    status: 'error',
    response_text: null,
    usage: null,
    cost_usd: null,
    pricing_provenance: null,
    attempts: 2,
    had_uncertain_prior_attempts: true,
    error: { type: 'timeout', message: 'timeout', retryable: true },
  } as ModelResponsePublic,
  accounting: {
    total_input_tokens: 0,
    total_output_tokens: 0,
    estimated_cost_usd: 0,
    has_unknown_accounting_components: true,
  },
  provider_execution_policy: policy,
}

const running: DirectRunningRunResponse = {
  kind: 'direct',
  status: 'running',
  id: 'direct-3',
  started_at: '2026-09-01T12:00:00Z',
  config,
  provider_execution_policy: policy,
}

function LocationProbe({ onState }: { onState?: (state: unknown) => void }) {
  const location = useLocation()
  onState?.(location.state)
  return <span data-testid="location">{location.pathname}</span>
}

function renderApp(path: string, onState?: (state: unknown) => void) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/runs" element={<History />} />
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
      <LocationProbe onState={onState} />
    </MemoryRouter>,
  )
}

describe('Home -- envio de uma resposta direta', () => {
  it('envia exatamente {question, enabled_providers: [um], source_text: null, kind: "direct"} e abre a run', async () => {
    vi.mocked(apiClient.createRun).mockResolvedValue(completed)
    vi.mocked(apiClient.getRun).mockResolvedValue(completed)
    renderApp('/')

    await screen.findByRole('button', { name: 'Modelos: Claude, GPT' })
    await userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'Qual a capital do Brasil?')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'Qual a capital do Brasil?',
      enabled_providers: ['anthropic'],
      source_text: null,
      kind: 'direct',
    })
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/runs/direct-1'))
    expect(await screen.findByText('Brasília.')).toBeInTheDocument()
  })

  it('o envio do Conselho continua sem `kind`', async () => {
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderApp('/')

    await screen.findByRole('button', { name: 'Modelos: Claude, GPT' })
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'q',
      enabled_providers: ['anthropic', 'openai'],
      source_text: null,
    })
  })

  it('provider_prerequisites_missing: aviso de validação com recarga da lista, sem reenvio', async () => {
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(422, 'provider_prerequisites_missing', 'x', { provider: 'anthropic' }),
    )
    renderApp('/')
    await screen.findByRole('button', { name: 'Modelos: Claude, GPT' })
    await userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/não tem a configuração local necessária/)
    expect(within(notice).getByRole('button', { name: 'Recarregar lista de modelos' })).toBeInTheDocument()
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('desfecho incerto de uma direta: mesmo aviso do Conselho, sem reenvio de um clique', async () => {
    vi.mocked(apiClient.createRun).mockRejectedValue(new TypeError('Failed to fetch'))
    renderApp('/')
    await screen.findByRole('button', { name: 'Modelos: Claude, GPT' })
    await userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/não foi possível confirmar o resultado/i)
    expect(within(notice).queryByRole('button')).toBeNull()
    expect(screen.getByRole('radio', { name: 'Resposta direta' })).toBeChecked() // estado preservado
  })
})

describe('RunDetail -- run direta', () => {
  it('concluída: a resposta primeiro, dita como resposta de um único modelo, sem nada do Conselho', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completed)
    renderApp('/runs/direct-1')

    const answer = await screen.findByRole('region', { name: 'Resposta' })
    expect(within(answer).getByText('Brasília.').tagName).toBe('P')
    expect(within(answer).getByText('É a capital desde 1960.')).toBeInTheDocument()
    expect(within(answer).getByText(/Resposta direta de GPT: um único modelo, sem as etapas do conselho/)).toBeInTheDocument()
    expect(within(answer).getByText(/Não é consenso nem verificação/)).toBeInTheDocument()

    const meta = screen.getByRole('list', { name: 'Resumo da resposta' })
    expect(meta).toHaveTextContent('Modelo: GPT')
    expect(meta).toHaveTextContent('Custo estimado: ~US$ 0,0001')

    // nada do Conselho, nem vazio
    expect(screen.queryByText(/afirma(ç|c)ões avaliadas|avaliação completa|juiz avaliou|Limitações registradas/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /como esta resposta foi produzida/i })).toBeNull()
    expect(apiClient.getRunAudit).not.toHaveBeenCalled()
  })

  it('detalhes da chamada: modelo solicitado x reportado, proveniência, custo e tentativas', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completed)
    renderApp('/runs/direct-1')

    const details = await screen.findByRole('region', { name: 'Como esta resposta foi produzida' })
    await userEvent.click(within(details).getByText('Ver detalhes da chamada'))

    expect(details).toHaveTextContent('gpt-conf (padrão configurado quando a pergunta foi aceita)')
    expect(details).toHaveTextContent('gpt-conf-2026-09-01')
    expect(details).toHaveTextContent('direct_answer_v1')
    expect(details).toHaveTextContent('completion-request-sha256-v2:abc')
    expect(details).toHaveTextContent('tabela-teste')
    expect(details).toHaveTextContent('420 ms')
  })

  it('sem resposta: o desfecho é o conteúdo principal; nenhum outro modelo; incerteza dita', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(failed)
    renderApp('/runs/direct-2')

    const outcome = await screen.findByRole('region', { name: 'Sem resposta' })
    expect(outcome).toHaveTextContent('A chamada ao provider excedeu o tempo limite.')
    expect(outcome).toHaveTextContent(/nenhum outro modelo foi usado no lugar de GPT/)
    expect(outcome).toHaveTextContent(/uma tentativa anterior pode ter chegado ao provider/i)
    expect(screen.queryByRole('region', { name: 'Resposta' })).toBeNull()
    // custo da chamada desconhecido: nunca "0,00"
    expect(screen.getByRole('list', { name: 'Resumo da tentativa' })).toHaveTextContent(
      'Custo estimado: Estimativa indisponível',
    )
  })

  it('sem desfecho registrado: honesto, com atualização manual', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValueOnce(running).mockResolvedValueOnce(completed)
    renderApp('/runs/direct-3')

    const outcome = await screen.findByRole('region', { name: 'Sem desfecho registrado' })
    expect(outcome).toHaveTextContent(/indistinguíveis/)
    await userEvent.click(within(outcome).getByRole('button', { name: 'Atualizar registro' }))
    expect(await screen.findByText('Brasília.')).toBeInTheDocument()
  })

  it('"Perguntar de novo" leva só a pergunta e o provider, marcados como direta', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completed)
    const states: unknown[] = []
    renderApp('/runs/direct-1', (state) => states.push(state))

    await userEvent.click(await screen.findByRole('link', { name: 'Perguntar de novo' }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/))
    expect(states.at(-1)).toEqual({
      reuseInput: {
        question: 'Qual a capital do Brasil?',
        sourceText: null,
        enabledProviders: ['openai'],
        kind: 'direct',
      },
    })
    expect(JSON.stringify(states.at(-1))).not.toMatch(/gpt-conf|Brasília|direct-1/)
    expect(await screen.findByRole('radio', { name: 'Resposta direta' })).toBeChecked()
    expect(await screen.findByRole('button', { name: 'Modelo: GPT' })).toBeInTheDocument()
  })
})

describe('Histórico -- tipo de run', () => {
  it('marca só as runs diretas', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [
        { id: 'd', status: 'completed', started_at: '2026-09-01T12:00:00Z', ended_at: null, question: 'Direta?', kind: 'direct' },
        { id: 'c', status: 'completed', started_at: '2026-09-01T11:00:00Z', ended_at: null, question: 'Conselho?', kind: 'council' },
        { id: 'h', status: 'completed', started_at: '2026-09-01T10:00:00Z', ended_at: null, question: 'Histórica?' },
      ],
      limit: 20,
      offset: 0,
    })
    renderApp('/runs')

    const direct = (await screen.findByText('Direta?')).closest('li') as HTMLElement
    expect(within(direct).getByText('Resposta direta')).toBeInTheDocument()
    for (const question of ['Conselho?', 'Histórica?']) {
      const item = screen.getByText(question).closest('li') as HTMLElement
      expect(within(item).queryByText('Resposta direta')).toBeNull()
    }
  })
})
