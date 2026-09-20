import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { Home } from '../Home'
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

function renderHome() {
  return render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  )
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
})

describe('Home', () => {
  it('mostra erro quando a descoberta de providers falha', async () => {
    vi.mocked(apiClient.getProviders).mockRejectedValue(
      new ApiError(500, 'internal_error', 'falhou', null),
    )
    renderHome()

    expect(await screen.findByText(/não foi possível carregar os participantes/i)).toBeInTheDocument()
  })

  it('pré-seleciona todos os providers retornados por GET /providers após discovery bem-sucedido', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({
      providers: ['openai', 'anthropic', 'gemini'],
    })
    renderHome()

    const toggle = await screen.findByRole('button', { name: /3 selecionados/i })

    await userEvent.click(toggle)
    expect(screen.getByLabelText('GPT')).toBeChecked()
    expect(screen.getByLabelText('Claude')).toBeChecked()
    expect(screen.getByLabelText('Gemini')).toBeChecked()
  })

  it('permite submit sem o usuário precisar abrir o seletor (seleção padrão já é suficiente)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai', 'anthropic'] })
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: /2 selecionados/i })

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'Qual a capital do Brasil?')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
  })

  it('envia exatamente os IDs retornados por GET /providers, sem hardcode', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({
      providers: ['openai', 'anthropic', 'gemini'],
    })
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: /3 selecionados/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'pergunta',
      enabled_providers: ['openai', 'anthropic', 'gemini'],
      source_text: null,
    })
  })

  it('envia a question VERBATIM, com espaço em branco significativo ao redor preservado (repair F1)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: /1 selecionado\b/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    // userEvent.type digita caractere a caractere -- inclui os espaços
    // literalmente, nunca colapsados/removidos pelo próprio evento de
    // digitação.
    await userEvent.type(question, '  pergunta válida  ')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: '  pergunta válida  ',
      enabled_providers: ['openai'],
      source_text: null,
    })
  })

  it('espaço-em-branco-só continua bloqueando submit mesmo com forwarding verbatim (repair F1)', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
    renderHome()

    await screen.findByRole('button', { name: /1 selecionado\b/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, '   ')

    expect(screen.getByRole('button', { name: /investigar/i })).toBeDisabled()
    expect(apiClient.createRun).not.toHaveBeenCalled()
  })

  it('usuário ainda pode desmarcar um provider pré-selecionado', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai', 'anthropic'] })
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    const toggle = await screen.findByRole('button', { name: /2 selecionados/i })
    await userEvent.click(toggle)
    await userEvent.click(screen.getByLabelText('Claude'))

    expect(await screen.findByRole('button', { name: /1 selecionado\b/i })).toBeInTheDocument()

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'pergunta',
      enabled_providers: ['openai'],
      source_text: null,
    })
  })

  it('zero providers selecionados continua bloqueando submit', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai', 'anthropic'] })
    renderHome()

    const toggle = await screen.findByRole('button', { name: /2 selecionados/i })
    await userEvent.click(toggle)
    await userEvent.click(screen.getByLabelText('GPT'))
    await userEvent.click(screen.getByLabelText('Claude'))

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')

    expect(screen.getByRole('button', { name: /investigar/i })).toBeDisabled()
  })

  it('resposta vazia de GET /providers mantém seleção vazia e submit bloqueado', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: [] })
    renderHome()

    const toggle = await screen.findByRole('button', { name: /0 selecionados/i })
    expect(toggle).toBeInTheDocument()

    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    expect(screen.getByRole('button', { name: /investigar/i })).toBeDisabled()
  })

  it('mostra loading honesto (sem progresso falso) durante a execução', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: /1 selecionado\b/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(await screen.findByRole('status')).toHaveTextContent('Investigando…')
    expect(screen.getByText(/tempo decorrido/i)).toBeInTheDocument()
    expect(screen.getByText(/pode levar vários minutos/i)).toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/extraindo claims/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/debatendo/i)).not.toBeInTheDocument()
  })

  it('mostra a resposta final ao completar com sucesso', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
    vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
    renderHome()

    await screen.findByRole('button', { name: /1 selecionado\b/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'Qual a capital do Brasil?')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
    expect(screen.getByText('Só uma rodada de debate.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /ver detalhes desta execução/i })).toHaveAttribute(
      'href',
      '/runs/run-abc',
    )
  })

  it('mostra quorum insuficiente com link usando details.run_id', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai', 'anthropic'] })
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(409, 'insufficient_quorum', 'Quórum insuficiente.', {
        run_id: 'failed-run-42',
        successful_count: 1,
        total_providers: 2,
        min_to_return: 2,
      }),
    )
    renderHome()

    await screen.findByRole('button', { name: /2 selecionados/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(await screen.findByText(/não houve participantes suficientes/i)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /ver detalhes/i })
    expect(link).toHaveAttribute('href', '/runs/failed-run-42')
  })

  it('mostra mensagem de validação em 422', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(422, 'invalid_provider', 'provider inválido', null),
    )
    renderHome()

    await screen.findByRole('button', { name: /1 selecionado\b/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    expect(await screen.findByText(/participantes selecionados não existem/i)).toBeInTheDocument()
  })

  it('mostra mensagem genérica segura em 500', async () => {
    vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
    vi.mocked(apiClient.createRun).mockRejectedValue(
      new ApiError(500, 'internal_error', 'stack trace secreta aqui', null),
    )
    renderHome()

    await screen.findByRole('button', { name: /1 selecionado\b/i })
    const question = screen.getByLabelText(/faça uma pergunta/i)
    await userEvent.type(question, 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

    const errorText = await screen.findByText(/algo deu errado do nosso lado/i)
    expect(errorText).toBeInTheDocument()
    expect(screen.queryByText(/stack trace secreta/i)).not.toBeInTheDocument()
  })

  describe('Reutilizar pergunta (prefill de ENTRADA do usuário)', () => {
    function renderHomeWithState(state: unknown) {
      return render(
        <MemoryRouter initialEntries={[{ pathname: '/', state }]}>
          <Home />
        </MemoryRouter>,
      )
    }

    it('preenche pergunta, fonte e participantes; o envio usa ids canônicos e nenhum campo extra', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai', 'anthropic', 'gemini'] })
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
      expect(await screen.findByRole('button', { name: /2 selecionados/i })).toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

      expect(apiClient.createRun).toHaveBeenCalledWith({
        question: 'Pergunta reutilizada',
        enabled_providers: ['anthropic', 'openai'],
        source_text: 'Fonte reutilizada',
      })
    })

    it('ignora participantes que não existem mais; se nenhum restar, volta ao padrão (todos)', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai', 'anthropic'] })
      renderHomeWithState({
        reuseInput: { question: 'q', sourceText: null, enabledProviders: ['provider-removido'] },
      })

      expect(await screen.findByRole('button', { name: /2 selecionados/i })).toBeInTheDocument()
    })

    it('state malformado é ignorado (composer vazio, comportamento normal)', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
      renderHomeWithState({ reuseInput: { question: 42, enabledProviders: 'x' } })

      expect(await screen.findByLabelText(/faça uma pergunta/i)).toHaveValue('')
    })

    it('sem fonte no reuso, o painel de fonte continua recolhido', async () => {
      vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
      renderHomeWithState({ reuseInput: { question: 'q', sourceText: null, enabledProviders: ['openai'] } })

      await screen.findByLabelText(/faça uma pergunta/i)
      expect(screen.queryByLabelText(/fonte de texto/i)).not.toBeInTheDocument()
    })
  })

  describe('Limites de entrada e erros de validação', () => {
    async function ready() {
      vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
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
      expect(screen.getByRole('button', { name: /investigar/i })).toBeDisabled()
    })

    it('exatamente 20.000 caracteres continua válido', async () => {
      vi.mocked(apiClient.createRun).mockResolvedValue(completedResult)
      const input = await ready()

      fireEvent.change(input, { target: { value: 'a'.repeat(20_000) } })

      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /investigar/i })).toBeEnabled()
    })

    it('conta por code point como o servidor: 10.000 emojis (20.000 unidades UTF-16) são válidos', async () => {
      const input = await ready()

      fireEvent.change(input, { target: { value: '😀'.repeat(10_000) } })

      expect(screen.getByRole('button', { name: /investigar/i })).toBeEnabled()
      expect(screen.getByText(/10\.000 \/ 20\.000 caracteres/)).toBeInTheDocument()
    })

    it('fonte acima do limite: erro visível mesmo com o painel recolhido, submit bloqueado', async () => {
      const input = await ready()
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i }))
      fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: 'b'.repeat(20_001) } })
      await userEvent.type(input, 'q')
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i })) // recolhe

      expect(screen.getByRole('alert')).toHaveTextContent(/a fonte passa do limite de 20\.000/i)
      expect(screen.getByRole('button', { name: /investigar/i })).toBeDisabled()
    })

    it('fonte só com espaços não conta pro limite (é enviada como ausente)', async () => {
      const input = await ready()
      await userEvent.type(input, 'q')
      await userEvent.click(screen.getByRole('button', { name: /fonte \(opcional\)/i }))
      fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: ' '.repeat(25_000) } })

      expect(screen.getByRole('button', { name: /investigar/i })).toBeEnabled()
    })

    it('invalid_request do servidor mostra feedback POR CAMPO, sem expor a estrutura crua', async () => {
      vi.mocked(apiClient.createRun).mockRejectedValue(
        new ApiError(422, 'invalid_request', 'Request inválido.', {
          errors: [{ loc: ['body', 'source_text'], msg: 'Value error, interno xyz', type: 'value_error' }],
        }),
      )
      const input = await ready()
      await userEvent.type(input, 'pergunta')
      await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

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
      await userEvent.click(screen.getByRole('button', { name: /investigar/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Verifique os dados informados.')
    })
  })
})
