import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { Home } from '../Home'
import type { LocalPrerequisiteState, ProvidersResponse } from '../../api/types'
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

// Rotas reais do app que importam aqui: a Home navega pra /runs/:id ao
// receber um desfecho persistido. O probe expõe o caminho atual pra
// asserções de navegação.
function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location">{location.pathname}</span>
}

function renderHome(initialEntry: string | { pathname: string; state: unknown } = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/runs" element={<p>Página do Histórico</p>} />
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  )
}

const currentPath = () => screen.getByTestId('location').textContent

// Resposta de GET /providers: por padrão, todo provider com a configuração
// local presente ("met"); `states` sobrescreve por provider.
function listed(
  providers: string[],
  states: Record<string, LocalPrerequisiteState> = {},
): ProvidersResponse {
  return {
    providers,
    local_prerequisites: Object.fromEntries(providers.map((id) => [id, states[id] ?? 'met'])),
  }
}

const completedResult = {
  status: 'completed' as const,
  id: 'run-abc',
  started_at: '2026-09-06T00:00:00Z',
  completed_at: '2026-09-06T00:00:05Z',
  final_answer: {
    answer_text: 'Brasília é a capital do Brasil.',
    answer_blocks: null,
    limitations: ['Só uma rodada de debate.'],
    status: 'llm_composed' as const,
    editor_model: 'claude-sonnet-5',
    editor_model_identity_source: 'provider_reported' as const,
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
    max_output_tokens_grouping: 100,
    max_output_tokens_judge: 100,
    round_dispatch_timeout_seconds: 30,
    quorum: { min_for_debate: 1, min_to_return: 1 },
  },
  provider_execution_policy: null,
  // Snapshot concreto: `completedResult` representa uma execução NOVA
  // recém-aceita (retorno de POST /runs), nunca um registro histórico
  // pré-feature -- ver docstring de DefaultModelAuthoritySnapshot.
  default_model_authority_snapshot: {
    configured_default_models: { openai: 'gpt-5.5', anthropic: 'claude-sonnet-5' },
  },
}

beforeEach(() => {
  vi.mocked(apiClient.getProviders).mockReset()
  vi.mocked(apiClient.createRun).mockReset()
  vi.mocked(apiClient.getRun).mockReset()
  vi.mocked(apiClient.getRunAudit).mockReset()
})

describe('Home', () => {
  it('mostra erro quando a descoberta de providers falha', async () => {
    vi.mocked(apiClient.getProviders).mockRejectedValue(
      new ApiError(500, 'internal_error', 'falhou', null),
    )
    renderHome()

    expect(await screen.findByText(/não foi possível carregar a lista de modelos/i)).toBeInTheDocument()
  })

  it('pré-seleciona todos os providers retornados por GET /providers após discovery bem-sucedido', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic', 'gemini']))
    renderHome()

    const toggle = await screen.findByRole('button', { name: 'Modelos: GPT, Claude, Gemini' })

    await userEvent.click(toggle)
    expect(screen.getByLabelText('GPT')).toBeChecked()
    expect(screen.getByLabelText('Claude')).toBeChecked()
    expect(screen.getByLabelText('Gemini')).toBeChecked()
  })

  it('permite submit sem o usuário precisar abrir o seletor (seleção padrão já é suficiente)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic']))
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'Qual a capital do Brasil?')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
    expect(currentPath()).toBe('/runs/run-abc')
  })

  it('envia exatamente os IDs retornados por GET /providers, sem hardcode', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic', 'gemini']))
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT, Claude, Gemini' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'pergunta',
      enabled_providers: ['openai', 'anthropic', 'gemini'],
      source_text: null,
    })
  })

  it('envia a question VERBATIM, com espaço em branco significativo ao redor preservado (repair F1)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    // userEvent.type digita caractere a caractere -- inclui os espaços
    // literalmente, nunca colapsados/removidos pelo próprio evento de
    // digitação.
    await userEvent.type(question, '  pergunta válida  ')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: '  pergunta válida  ',
      enabled_providers: ['openai'],
      source_text: null,
    })
  })

  it('espaço-em-branco-só continua bloqueando submit mesmo com forwarding verbatim (repair F1)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, '   ')

    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    expect(apiClient.createRun).not.toHaveBeenCalled()
  })

  it('usuário ainda pode desmarcar um provider pré-selecionado', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic']))
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    const toggle = await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })
    await userEvent.click(toggle)
    await userEvent.click(screen.getByLabelText('Claude'))

    expect(await screen.findByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'pergunta',
      enabled_providers: ['openai'],
      source_text: null,
    })
  })

  it('zero providers selecionados continua bloqueando submit', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic']))
    renderHome()

    const toggle = await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })
    await userEvent.click(toggle)
    await userEvent.click(screen.getByLabelText('GPT'))
    await userEvent.click(screen.getByLabelText('Claude'))

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')

    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
  })

  it('resposta vazia de GET /providers mantém seleção vazia e submit bloqueado', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed([]))
    renderHome()

    const toggle = await screen.findByRole('button', { name: 'Modelos: nenhum' })
    expect(toggle).toBeInTheDocument()

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
  })

  it('mostra loading honesto (sem progresso falso) durante a execução', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Aguardando a resposta…')
    expect(screen.getByRole('button', { name: 'Perguntando…' })).toBeDisabled()
    expect(screen.getByText(/tempo decorrido/i)).toBeInTheDocument()
    expect(screen.getByText(/pode levar vários minutos/i)).toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/extraindo claims/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/debatendo/i)).not.toBeInTheDocument()
  })

  it('ao completar, abre a página estável da pergunta (/runs/:id) com o resultado em mãos -- sem novo GET', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'Qual a capital do Brasil?')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
    expect(currentPath()).toBe('/runs/run-abc')
    // a mesma superfície de uma pergunta reaberta: pergunta, resposta, limitações
    expect(screen.getByText('Qual a capital do Brasil?')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Resposta' })).toBeInTheDocument()
    expect(screen.getByText('Só uma rodada de debate.')).toBeInTheDocument()
    expect(screen.queryByText(/carregando pergunta/i)).not.toBeInTheDocument()
    expect(apiClient.getRun).not.toHaveBeenCalled()
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('quórum insuficiente com details.run_id abre o registro persistido da pergunta', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic']))
    vi.mocked(apiClient.getRun).mockImplementation(() => new Promise(() => {}))
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(409, 'insufficient_quorum', 'Quórum insuficiente.', {
        run_id: 'failed-run-42',
        successful_count: 1,
        total_providers: 2,
        min_to_return: 2,
      }),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    await waitFor(() => expect(currentPath()).toBe('/runs/failed-run-42'))
    // o registro persistido é buscado normalmente (não há resultado em mãos)
    expect(apiClient.getRun).toHaveBeenCalledWith('failed-run-42')
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('quórum insuficiente sem run_id: aviso neutro (não erro), sem navegação', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic']))
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(409, 'insufficient_quorum', 'Quórum insuficiente.', null),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/poucos modelos responderam para montar uma resposta/i)
    expect(notice).toHaveClass('notice--neutral')
    expect(currentPath()).toBe('/')
  })

  it('invalid_provider: aviso de validação com recarga (segura) da lista de modelos', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(422, 'invalid_provider', 'provider inválido', null),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/um ou mais modelos selecionados não são reconhecidos pelo servidor/i)
    expect(notice).toHaveClass('notice--validation')

    // recarregar refaz só GET /providers -- nunca reenvia a pergunta
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar lista de modelos' }))
    await waitFor(() => expect(apiClient.getProviders).toHaveBeenCalledTimes(2))
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('500: desfecho incerto -- aponta o Histórico, NUNCA oferece reenvio de um clique, sem vazar detalhes', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(500, 'internal_error', 'stack trace secreta aqui', null),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/não foi possível confirmar o resultado desta pergunta/i)
    expect(notice).toHaveTextContent(/pode ter sido processada pelos modelos mesmo assim/i)
    expect(notice).toHaveTextContent(/confira o histórico antes de perguntar de novo/i)
    expect(notice).toHaveClass('notice--warning')
    expect(screen.queryByText(/stack trace secreta/i)).not.toBeInTheDocument()
    // nenhuma ação de reenvio no aviso -- só o caminho pro Histórico
    expect(within(notice).queryByRole('button')).toBeNull()
    expect(within(notice).getByRole('link', { name: 'Abrir o Histórico' })).toHaveAttribute('href', '/runs')
    // a pergunta continua no composer pra um reenvio DELIBERADO
    expect(screen.getByLabelText(/faça uma pergunta/i)).toHaveValue('pergunta')
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('erro de rede depois do envio também é desfecho incerto (nunca "falhou, tente de novo")', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockRejectedValue(new TypeError('Failed to fetch'))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    const notice = await screen.findByRole('alert')
    expect(notice).toHaveTextContent(/não foi possível confirmar o resultado desta pergunta/i)
    expect(within(notice).queryByRole('button')).toBeNull()
    expect(screen.queryByText(/tente novamente|tentar novamente/i)).not.toBeInTheDocument()
  })

  it('falha ao carregar modelos: erro com "Tentar novamente", que refaz só GET /providers', async () => {
    vi.mocked(apiClient.getProviders)
      .mockRejectedValueOnce(new ApiError(500, 'internal_error', 'falhou', null))
      .mockResolvedValueOnce(listed(['openai', 'anthropic']))
    renderHome()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveClass('notice--error')
    await userEvent.click(within(alert).getByRole('button', { name: 'Tentar novamente' }))

    expect(await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })).toBeInTheDocument()
    expect(screen.queryByText(/não foi possível carregar a lista de modelos/i)).not.toBeInTheDocument()
    expect(apiClient.getProviders).toHaveBeenCalledTimes(2)
    expect(apiClient.createRun).not.toHaveBeenCalled()
  })

  it('Ctrl/⌘+Enter envia; Enter sozinho só insere nova linha', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    expect(question).toHaveAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter')
    await userEvent.type(question, 'linha 1{Enter}linha 2')
    expect(question).toHaveValue('linha 1\nlinha 2')
    expect(apiClient.createRun).not.toHaveBeenCalled()

    await userEvent.type(question, '{Control>}{Enter}{/Control}')
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
    expect(vi.mocked(apiClient.createRun).mock.calls[0][0].question).toBe('linha 1\nlinha 2')
  })

  it('Meta+Enter (⌘) também envia', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'q{Meta>}{Enter}{/Meta}')
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+Enter não envia quando o envio está bloqueado (pergunta em branco)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), '  {Control>}{Enter}{/Control}')
    expect(apiClient.createRun).not.toHaveBeenCalled()
  })

  it('invalid_provider → "Recarregar modelos" com lista menor: o reenvio deliberado leva só os modelos que ainda existem', async () => {
    vi.mocked(apiClient.getProviders)
      .mockResolvedValueOnce(listed(['openai', 'anthropic', 'gemini']))
      .mockResolvedValueOnce(listed(['openai', 'gemini']))
    vi.mocked(apiClient.createRun)
      .mockRejectedValueOnce(new ApiError(422, 'invalid_provider', 'provider inválido', null))
      .mockImplementationOnce(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT, Claude, Gemini' })
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Recarregar lista de modelos' }))

    expect(await screen.findByRole('button', { name: 'Modelos: GPT, Gemini' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(apiClient.createRun).toHaveBeenCalledTimes(2)
    expect(vi.mocked(apiClient.createRun).mock.calls[1][0].enabled_providers).toEqual(['openai', 'gemini'])
  })

  it('invalid_provider → recarga sem nenhum dos modelos escolhidos: envio bloqueado', async () => {
    vi.mocked(apiClient.getProviders)
      .mockResolvedValueOnce(listed(['openai']))
      .mockResolvedValueOnce(listed(['gemini']))
    vi.mocked(apiClient.createRun).mockRejectedValueOnce(
      new ApiError(422, 'invalid_provider', 'provider inválido', null),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Recarregar lista de modelos' }))

    expect(await screen.findByRole('button', { name: 'Modelos: nenhum' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    expect(apiClient.createRun).toHaveBeenCalledTimes(1)
  })

  it('estados de pré-requisito local vindos de GET /providers: pré-seleção só "met", envio nunca leva "missing"', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue(
      listed(['anthropic', 'gemini', 'openai'], { anthropic: 'missing', gemini: 'unknown' }),
    )
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await userEvent.click(await screen.findByRole('button', { name: 'Modelos: GPT' }))
    expect(screen.getByLabelText('Claude')).toBeDisabled()
    await userEvent.click(screen.getByLabelText('Gemini'))
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

    expect(vi.mocked(apiClient.createRun).mock.calls[0][0].enabled_providers).toEqual(['openai', 'gemini'])
  })

  it('primeira execução sem nenhum "met": orienta, e "Recarregar lista de modelos" só repete GET /providers', async () => {
    vi.mocked(apiClient.getProviders)
      .mockResolvedValueOnce(listed(['openai', 'anthropic'], { openai: 'missing', anthropic: 'missing' }))
      .mockResolvedValueOnce(listed(['openai', 'anthropic'], { anthropic: 'missing' }))
    renderHome()

    const notice = await screen.findByRole('region', { name: 'Falta a configuração local dos modelos' })
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    await userEvent.click(within(notice).getByRole('button', { name: 'Recarregar lista de modelos' }))

    expect(await screen.findByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
    expect(apiClient.getProviders).toHaveBeenCalledTimes(2)
    expect(apiClient.createRun).not.toHaveBeenCalled()
  })

  describe('Reutilizar pergunta (prefill de ENTRADA do usuário)', () => {
    function renderHomeWithState(state: unknown) {
      return renderHome({ pathname: '/', state })
    }

    it('preenche pergunta, fonte e participantes; o envio usa ids canônicos e nenhum campo extra', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic', 'gemini']))
      vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
      renderHomeWithState({
        reuseInput: {
          question: 'Pergunta reutilizada',
          sourceText: 'Fonte reutilizada',
          enabledProviders: ['anthropic', 'openai'],
        },
      })

      expect(await screen.findByLabelText(/faça uma pergunta/i)).toHaveValue('Pergunta reutilizada')
      expect(screen.getByLabelText(/fonte de texto/i)).toHaveValue('Fonte reutilizada')
      expect(await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })).toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

      expect(apiClient.createRun).toHaveBeenCalledWith({
        question: 'Pergunta reutilizada',
        enabled_providers: ['anthropic', 'openai'],
        source_text: 'Fonte reutilizada',
      })
    })

    it('ignora participantes que não existem mais; se nenhum restar, volta ao padrão (todos)', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai', 'anthropic']))
      renderHomeWithState({
        reuseInput: { question: 'q', sourceText: null, enabledProviders: ['provider-removido'] },
      })

      expect(await screen.findByRole('button', { name: 'Modelos: GPT, Claude' })).toBeInTheDocument()
    })

    it('state malformado é ignorado (composer vazio, comportamento normal)', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
      renderHomeWithState({ reuseInput: { question: 42, enabledProviders: 'x' } })

      expect(await screen.findByLabelText(/faça uma pergunta/i)).toHaveValue('')
    })

    it('sem fonte no reuso, o painel de fonte continua recolhido', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
      renderHomeWithState({ reuseInput: { question: 'q', sourceText: null, enabledProviders: ['openai'] } })

      await screen.findByLabelText(/faça uma pergunta/i)
      expect(screen.queryByLabelText(/fonte de texto/i)).not.toBeInTheDocument()
    })
  })

  describe('Limites de entrada e erros de validação', () => {
    async function ready() {
      vi.mocked(apiClient.getProviders).mockResolvedValue(listed(['openai']))
      renderHome()
      return await screen.findByLabelText(/faça uma pergunta/i)
    }

    it('o limite de 20.000 caracteres é visível na pergunta (e na fonte, ao abrir o painel)', async () => {
      await ready()

      expect(screen.getByText(/0 \/ 20\.000 caracteres/)).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i }))
      expect(screen.getAllByText(/\/ 20\.000 caracteres/)).toHaveLength(2)
    })

    it('pergunta acima do limite: feedback específico do campo e submit bloqueado', async () => {
      const input = await ready()

      fireEvent.change(input, { target: { value: 'a'.repeat(20_001) } })

      expect(screen.getByRole('alert')).toHaveTextContent(/a pergunta passa do limite de 20\.000/i)
      expect(input).toHaveAttribute('aria-invalid', 'true')
      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    })

    it('exatamente 20.000 caracteres continua válido', async () => {
      vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
      const input = await ready()

      fireEvent.change(input, { target: { value: 'a'.repeat(20_000) } })

      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeEnabled()
    })

    it('conta por code point como o servidor: 10.000 emojis (20.000 unidades UTF-16) são válidos', async () => {
      const input = await ready()

      fireEvent.change(input, { target: { value: '😀'.repeat(10_000) } })

      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeEnabled()
      expect(screen.getByText(/10\.000 \/ 20\.000 caracteres/)).toBeInTheDocument()
    })

    it('fonte acima do limite: erro visível mesmo com o painel recolhido, submit bloqueado', async () => {
      const input = await ready()
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i }))
      fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: 'b'.repeat(20_001) } })
      await userEvent.type(input, 'q')
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i })) // recolhe

      expect(screen.getByRole('alert')).toHaveTextContent(/a fonte passa do limite de 20\.000/i)
      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    })

    it('fonte só com espaços não conta pro limite (é enviada como ausente)', async () => {
      const input = await ready()
      await userEvent.type(input, 'q')
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i }))
      fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: ' '.repeat(25_000) } })

      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeEnabled()
    })

    describe('fonte enviada VERBATIM, como API/CLI (whitespace só decide se está vazia)', () => {
      async function submitWithSource(source: string) {
        vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
        const input = await ready()
        await userEvent.type(input, 'q')
        await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i }))
        fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: source } })
        const submit = screen.getByRole('button', { name: 'Perguntar' })
        return submit
      }

      it.each([
        [' source ', ' source '],
        ['    código\n', '    código\n'],
        ['\ntexto\n', '\ntexto\n'],
        // U+FEFF não é whitespace pro backend (str.isspace) -- fonte não vazia
        ['\ufeff', '\ufeff'],
      ])('%j é enviada exatamente como digitada', async (source, sent) => {
        const submit = await submitWithSource(source)
        await userEvent.click(submit)

        await waitFor(() => expect(apiClient.createRun).toHaveBeenCalled())
        expect(vi.mocked(apiClient.createRun).mock.calls[0][0].source_text).toBe(sent)
      })

      it.each([[''], ['   '], ['\n\t '], ['\u001c\u0085\u3000']])(
        '%j é vazia pelo critério do backend e vai como ausente',
        async (source) => {
          const submit = await submitWithSource(source)
          await userEvent.click(submit)

          await waitFor(() => expect(apiClient.createRun).toHaveBeenCalled())
          expect(vi.mocked(apiClient.createRun).mock.calls[0][0].source_text).toBeNull()
        },
      )

      it('fonte exatamente no limite (20.000) é aceita e enviada inteira', async () => {
        const source = 'b'.repeat(20_000)
        const submit = await submitWithSource(source)

        expect(submit).toBeEnabled()
        await userEvent.click(submit)
        await waitFor(() => expect(apiClient.createRun).toHaveBeenCalled())
        expect(vi.mocked(apiClient.createRun).mock.calls[0][0].source_text).toBe(source)
      })

      it('fonte que só caberia no limite se fosse trimada é bloqueada (o backend a rejeitaria)', async () => {
        const submit = await submitWithSource(' ' + 'b'.repeat(20_000))

        expect(screen.getByRole('alert')).toHaveTextContent(/a fonte passa do limite de 20\.000/i)
        expect(screen.getByText(/20\.001 \/ 20\.000 caracteres/)).toBeInTheDocument()
        expect(submit).toBeDisabled()
      })
    })

    it('invalid_request do servidor mostra feedback POR CAMPO, sem expor a estrutura crua', async () => {
      vi.mocked(apiClient.createRun).mockRejectedValue(
        new ApiError(422, 'invalid_request', 'Request inválido.', {
          errors: [{ loc: ['body', 'source_text'], msg: 'Value error, interno xyz', type: 'value_error' }],
        }),
      )
      const input = await ready()
      await userEvent.type(input, 'pergunta')
      await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent(/a fonte não é válida.*20\.000/i)
      expect(alert).not.toHaveTextContent(/value error|interno xyz|loc|body/i)
    })

    it('invalid_request sem detalhes estruturados cai no rótulo genérico seguro', async () => {
      vi.mocked(apiClient.createRun).mockRejectedValue(
        new ApiError(422, 'invalid_request', 'Request inválido.', null),
      )
      const input = await ready()
      await userEvent.type(input, 'pergunta')
      await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Verifique os dados informados.')
    })
  })
})
