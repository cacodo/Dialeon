import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
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

// Simula chegar em RunDetail vindo de uma linha do Histórico -- History.tsx
// anexa `state={{ fromHistoryPage: N }}` ao `<Link>` de cada linha (ver
// History.tsx). Sem este state (acesso direto/refresh/deep link), o
// comportamento precisa continuar honesto e válido -- nunca depender dele.
function renderDetailFromHistoryPage(runId: string, fromHistoryPage: number) {
  return render(
    <MemoryRouter
      initialEntries={[{ pathname: `/runs/${runId}`, state: { fromHistoryPage } }]}
    >
      <Routes>
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

// Mesmo que `renderDetailFromHistoryPage`, mas aceita QUALQUER valor de
// `location.state` -- pra exercitar `fromHistoryPage` malformado/inseguro
// (nunca só o `number` válido que a própria navegação de History.tsx
// produz), já que `location.state` não é confiável por construção
// (pode ser manufaturado por fora do fluxo normal).
function renderDetailWithRawState(runId: string, state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: `/runs/${runId}`, state }]}>
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
    answer_blocks: null,
    limitations: [],
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
  provider_execution_policy: { attempt_timeout_seconds: 45, max_transport_attempts_per_completion: 3 },
  // Snapshot concreto -- `completedRun` representa uma execução real
  // com provenance de deployment conhecida (sibling de
  // provider_execution_policy acima, mesma disciplina).
  default_model_authority_snapshot: {
    configured_default_models: { openai: 'gpt-5.5', anthropic: 'claude-sonnet-5' },
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
    max_output_tokens_grouping: 100,
    max_output_tokens_judge: 100,
    round_dispatch_timeout_seconds: 30,
    quorum: { min_for_debate: 2, min_to_return: 2 },
  },
  // null junto de provider_execution_policy: null acima -- mesma
  // disciplina de sibling (T02.2/Provider Default-Model Snapshot
  // Provenance V1): registro tratado aqui como histórico/pré-feature,
  // nunca uma execução nova sem provenance.
  provider_execution_policy: null,
  default_model_authority_snapshot: null,
}

// T02.4 -- run aceito sem desfecho terminal ainda. T02.2 -- policy=null
// (histórico/desconhecido) pra exercitar a renderização honesta.
// default_model_authority_snapshot=null pelo mesmo motivo -- sibling de
// provider_execution_policy, mesma disciplina de registro histórico.
const runningRun = {
  status: 'running' as const,
  id: 'run-3',
  started_at: '2026-09-06T00:00:00Z',
  config: completedRun.config,
  provider_execution_policy: null,
  default_model_authority_snapshot: null,
}

// T02.4 -- exceção inesperada durante a execução. default_model_authority_snapshot=null
// -- mesma disciplina de provider_execution_policy=null acima (registro histórico).
const failedRun = {
  status: 'failed' as const,
  id: 'run-4',
  started_at: '2026-09-06T00:00:00Z',
  failed_at: '2026-09-06T00:00:02Z',
  failure_reason: 'WeirdBug',
  failure_stage: 'execution' as const,
  message: 'Erro interno inesperado durante a execução.',
  config: completedRun.config,
  provider_execution_policy: null,
  default_model_authority_snapshot: null,
}

describe('RunDetail', () => {
  it('mostra detail de uma run completed', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    renderDetail('run-1')

    expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
    expect(screen.getByText('Qual a capital do Brasil?')).toBeInTheDocument()
  })

  it('resumo da execução é quieto: sem identificadores brutos de provider nem política de execução -- só o que ajuda a interpretar a resposta', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    renderDetail('run-1')

    await screen.findByText('Brasília é a capital do Brasil.')

    const summary = screen.getByRole('heading', { name: /resumo da execução/i }).closest('section')
    expect(summary).not.toBeNull()
    // Contagem, não lista bruta de IDs de provider.
    expect(within(summary as HTMLElement).getByText(/1 participante no debate/i)).toBeInTheDocument()
    // Nome de exibição (GPT) para o usuário; o id canônico não aparece aqui.
    expect(within(summary as HTMLElement).getByText(/\(GPT\)/)).toBeInTheDocument()
    expect(within(summary as HTMLElement).queryByText('openai')).not.toBeInTheDocument()
    // Política de execução (detalhe técnico) não aparece no resumo
    // imediato -- só dentro da auditoria técnica, atrás de "Inspecionar
    // execução" + "Ver auditoria técnica".
    expect(screen.queryByText('45s')).not.toBeInTheDocument()
    expect(screen.queryByText(/timeout por tentativa/i)).not.toBeInTheDocument()
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

  const completedAudit = {
    status: 'completed' as const,
    id: 'run-1',
    started_at: '2026-09-06T00:00:00Z',
    completed_at: '2026-09-06T00:00:05Z',
    config: completedRun.config,
    debate_outcome: {
      skipped_reason: null,
      cumulative_budget_exceeded: false,
      claim_extraction_eligible_response_count: 0,
      claim_extraction_missing_response_count: 0,
    },
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
    numeric_verification_attempts: [],
    judge_verdict: null,
    judge_attempts: [],
    editor_attempts: [],
    final_answer: completedRun.final_answer,
    accounting: completedRun.accounting,
    provider_execution_policy: completedRun.provider_execution_policy,
    default_model_authority_snapshot: completedRun.default_model_authority_snapshot,
    reconciliation: {
      contract_version: 'source_judge_reconciliation_v1' as const,
      status: 'complete' as const,
      claim_outcomes: [],
    },
  }

  it('carrega audit somente sob ação explícita (lazy)', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    vi.mocked(apiClient.getRunAudit).mockResolvedValue(completedAudit)
    renderDetail('run-1')

    await screen.findByText('Brasília é a capital do Brasil.')
    expect(apiClient.getRunAudit).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))
    expect(apiClient.getRunAudit).toHaveBeenCalledWith('run-1')
  })

  it('política de execução (detalhe técnico) continua alcançável via auditoria técnica, mas não domina a inspeção inicial', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
    vi.mocked(apiClient.getRunAudit).mockResolvedValue(completedAudit)
    renderDetail('run-1')

    await screen.findByText('Brasília é a capital do Brasil.')
    await userEvent.click(screen.getByRole('button', { name: /inspecionar execução/i }))

    const technicalHeading = await screen.findByRole('heading', { name: /auditoria técnica/i })
    // Ainda não expandida -- o detalhe técnico não aparece só por ter
    // inspecionado a execução.
    expect(screen.queryByText('45s')).not.toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('button', { name: /ver auditoria técnica/i }),
    )

    expect(technicalHeading).toBeInTheDocument()
    expect(await screen.findByText('45s')).toBeInTheDocument()
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

  it('T02.4: mostra detail de uma run running, sem inventar desfecho nem oferecer inspeção', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(runningRun)
    renderDetail('run-3')

    expect(
      await screen.findByRole('heading', { name: 'Sem desfecho registrado' }),
    ).toBeInTheDocument()
    // nunca afirma progresso observável; explica as duas possibilidades
    expect(screen.queryByText(/em andamento/i)).not.toBeInTheDocument()
    expect(screen.getByText(/pode ainda estar ativa ou ter sido interrompida/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /inspecionar execução/i }),
    ).not.toBeInTheDocument()
    expect(apiClient.getRunAudit).not.toHaveBeenCalled()
    // T02.2 -- policy=null (histórico/desconhecido) renderiza honestamente.
    expect(screen.getByText(/não registrada/i)).toBeInTheDocument()
  })

  it('T02.4: mostra detail de uma run failed com mensagem sanitizada, sem oferecer inspeção', async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(failedRun)
    renderDetail('run-4')

    expect(await screen.findByRole('heading', { name: /falhou/i })).toBeInTheDocument()
    expect(screen.getByText(failedRun.message)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /inspecionar execução/i }),
    ).not.toBeInTheDocument()
  })

  it('repair M2: falha de persistência terminal é identificada como tal, nunca como falha de provider/modelo', async () => {
    const persistenceFailure = {
      ...failedRun,
      failure_reason: 'OperationalError',
      failure_stage: 'terminal_persistence' as const,
      message:
        'Falha ao persistir o resultado terminal da execução; o histórico detalhado da execução não foi preservado.',
    }
    vi.mocked(apiClient.getRun).mockResolvedValue(persistenceFailure)
    renderDetail('run-4')

    expect(await screen.findByRole('heading', { name: /falhou/i })).toBeInTheDocument()
    expect(screen.getByText(/persistência do resultado falhou/i)).toBeInTheDocument()
    expect(screen.getByText(persistenceFailure.message)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /inspecionar execução/i }),
    ).not.toBeInTheDocument()
  })

  describe('retorno ao Histórico preserva a página de origem', () => {
    it('acesso direto a /runs/:runId (sem state de origem) volta pra primeira página do histórico', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      renderDetail('run-1')

      await screen.findByText('Brasília é a capital do Brasil.')
      expect(screen.getByRole('link', { name: /histórico/i })).toHaveAttribute('href', '/runs')
    })

    it('navegação vinda da página 1 do Histórico volta pra /runs (sem ?page)', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      renderDetailFromHistoryPage('run-1', 1)

      await screen.findByText('Brasília é a capital do Brasil.')
      expect(screen.getByRole('link', { name: /histórico/i })).toHaveAttribute('href', '/runs')
    })

    it('navegação vinda da página 3 do Histórico preserva a página exata no link de volta', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      renderDetailFromHistoryPage('run-1', 3)

      await screen.findByText('Brasília é a capital do Brasil.')
      expect(screen.getByRole('link', { name: /histórico/i })).toHaveAttribute(
        'href',
        '/runs?page=3',
      )
    })

    it('estado not_found também preserva a página de origem do Histórico no link de volta', async () => {
      vi.mocked(apiClient.getRun).mockRejectedValue(
        new ApiError(404, 'run_not_found', 'não encontrada', null),
      )
      renderDetailFromHistoryPage('id-inexistente', 2)

      await screen.findByText(/execução não encontrada/i)
      expect(screen.getByRole('link', { name: /voltar ao histórico/i })).toHaveAttribute(
        'href',
        '/runs?page=2',
      )
    })

    it('estado de erro também preserva a página de origem do Histórico no link de volta', async () => {
      vi.mocked(apiClient.getRun).mockRejectedValue(
        new ApiError(500, 'internal_error', 'falhou', null),
      )
      renderDetailFromHistoryPage('run-1', 4)

      await screen.findByRole('alert')
      expect(screen.getByRole('link', { name: /voltar ao histórico/i })).toHaveAttribute(
        'href',
        '/runs?page=4',
      )
    })

    it.each([
      ['0', 0],
      ['negativo', -1],
      ['fracionário', 2.5],
      ['Infinity', Infinity],
      ['-Infinity', -Infinity],
      ['NaN', NaN],
      ['inteiro inseguro (> MAX_SAFE_INTEGER)', Number.MAX_SAFE_INTEGER + 2],
      ['1e308', 1e308],
    ])(
      '%s como fromHistoryPage cai pro /runs simples, nunca propaga um valor inseguro na URL',
      async (_label, unsafePage) => {
        vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
        renderDetailWithRawState('run-1', { fromHistoryPage: unsafePage })

        await screen.findByText('Brasília é a capital do Brasil.')
        expect(screen.getByRole('link', { name: /histórico/i })).toHaveAttribute('href', '/runs')
      },
    )

    it.each([
      ['string', '3'],
      ['objeto', { page: 3 }],
      ['null', null],
      ['array', [3]],
    ])(
      'fromHistoryPage do tipo %s (não-number) cai pro /runs simples',
      async (_label, malformedValue) => {
        vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
        renderDetailWithRawState('run-1', { fromHistoryPage: malformedValue })

        await screen.findByText('Brasília é a capital do Brasil.')
        expect(screen.getByRole('link', { name: /histórico/i })).toHaveAttribute('href', '/runs')
      },
    )

    it('location.state completamente malformado (não é um objeto com fromHistoryPage) cai pro /runs simples', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      renderDetailWithRawState('run-1', 'estado-nao-objeto')

      await screen.findByText('Brasília é a capital do Brasil.')
      expect(screen.getByRole('link', { name: /histórico/i })).toHaveAttribute('href', '/runs')
    })
  })

  describe('Atualização manual de Run sem desfecho registrado', () => {
    it('oferece "Atualizar registro" só quando a Run está sem desfecho, e nunca afirma que está viva', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(runningRun)
      renderDetail('run-3')

      expect(await screen.findByRole('heading', { name: 'Sem desfecho registrado' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Atualizar registro' })).toBeInTheDocument()
      expect(screen.queryByText(/em andamento|progresso|executando agora/i)).not.toBeInTheDocument()
    })

    it('não oferece atualização em Runs terminais', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      renderDetail('run-1')

      await screen.findByText('Brasília é a capital do Brasil.')
      expect(screen.queryByRole('button', { name: /atualizar registro/i })).not.toBeInTheDocument()
    })

    it('atualizar faz um NOVO GET e, se a Run ficou terminal, renderiza o detalhe terminal normal', async () => {
      vi.mocked(apiClient.getRun)
        .mockResolvedValueOnce(runningRun)
        .mockResolvedValueOnce({ ...completedRun, id: 'run-3' })
      renderDetail('run-3')
      await screen.findByRole('heading', { name: 'Sem desfecho registrado' })
      expect(apiClient.getRun).toHaveBeenCalledTimes(1)

      await userEvent.click(screen.getByRole('button', { name: 'Atualizar registro' }))

      expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeInTheDocument()
      expect(apiClient.getRun).toHaveBeenCalledTimes(2)
      expect(apiClient.getRun).toHaveBeenLastCalledWith('run-3')
      expect(screen.queryByRole('heading', { name: 'Sem desfecho registrado' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /atualizar registro/i })).not.toBeInTheDocument()
    })

    it('se continua sem desfecho, diz isso de forma neutra (sem alegar atividade)', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(runningRun)
      renderDetail('run-3')
      await screen.findByRole('heading', { name: 'Sem desfecho registrado' })

      await userEvent.click(screen.getByRole('button', { name: 'Atualizar registro' }))

      expect(await screen.findByText('Continua sem desfecho registrado.')).toBeInTheDocument()
      expect(screen.queryByText(/em andamento|ativa agora|progress/i)).not.toBeInTheDocument()
    })

    it('falha ao atualizar mostra um erro seguro e mantém a visão sem desfecho', async () => {
      vi.mocked(apiClient.getRun)
        .mockResolvedValueOnce(runningRun)
        .mockRejectedValueOnce(new ApiError(500, 'internal_error', 'boom', null))
      renderDetail('run-3')
      await screen.findByRole('heading', { name: 'Sem desfecho registrado' })

      await userEvent.click(screen.getByRole('button', { name: 'Atualizar registro' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(/algo deu errado/i)
      expect(screen.getByRole('heading', { name: 'Sem desfecho registrado' })).toBeInTheDocument()
    })

    it('nunca faz polling: sem clique, só o GET inicial acontece', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        vi.mocked(apiClient.getRun).mockResolvedValue(runningRun)
        renderDetail('run-3')
        await screen.findByRole('heading', { name: 'Sem desfecho registrado' })

        await vi.advanceTimersByTimeAsync(120_000)

        expect(apiClient.getRun).toHaveBeenCalledTimes(1)
      } finally {
        vi.useRealTimers()
      }
    })
  })

  describe('Reutilizar pergunta (reuso de ENTRADA, nunca continuidade)', () => {
    it('o link leva à home com SÓ pergunta/fonte/participantes -- nenhum artefato de modelo, id ou lineage', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue({
        ...completedRun,
        config: { ...completedRun.config, source_text: 'Texto de fonte do usuário.' },
      })
      const onHomeState = vi.fn()
      function Probe() {
        onHomeState(useLocation().state)
        return <p>home</p>
      }
      render(
        <MemoryRouter initialEntries={['/runs/run-1']}>
          <Routes>
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/" element={<Probe />} />
          </Routes>
        </MemoryRouter>,
      )

      await userEvent.click(await screen.findByRole('link', { name: 'Reutilizar pergunta' }))

      expect(onHomeState).toHaveBeenLastCalledWith({
        reuseInput: {
          question: completedRun.config.question,
          sourceText: 'Texto de fonte do usuário.',
          enabledProviders: completedRun.config.enabled_providers,
        },
      })
      expect(JSON.stringify(onHomeState.mock.calls.at(-1)?.[0])).not.toMatch(/run-1|Brasília|claim|verdict|parent/i)
    })

    it('está disponível também em Runs sem desfecho e falhas, e explica que nada do resultado anterior é enviado', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(failedRun)
      renderDetail('run-4')

      expect(await screen.findByRole('link', { name: 'Reutilizar pergunta' })).toBeInTheDocument()
      expect(screen.getByText(/nada do resultado anterior é enviado/i)).toBeInTheDocument()
      expect(screen.queryByText(/continuar (a )?conversa/i)).not.toBeInTheDocument()
    })
  })

  describe('Fonte fornecida pelo usuário', () => {
    const SOURCE = 'Linha 1 da fonte.\n\n  Linha 3 com espaços e <b>tags</b> literais.'

    it('fica recolhida por padrão e o usuário pode revelar o texto EXATO', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue({
        ...completedRun,
        config: { ...completedRun.config, source_text: SOURCE },
      })
      const { container } = renderDetail('run-1')
      const details = await waitFor(() => {
        const el = container.querySelector('details.run-detail__source')
        expect(el).not.toBeNull()
        return el as HTMLDetailsElement
      })

      expect(details.open).toBe(false)
      expect(screen.getByText(/fonte fornecida/i)).toBeInTheDocument()
      expect(screen.getByText(/não é verificado como verdadeiro/i)).toBeInTheDocument()

      await userEvent.click(screen.getByText(/fonte fornecida/i))

      expect(details.open).toBe(true)
      expect(container.querySelector('pre.run-detail__source-text')?.textContent).toBe(SOURCE)
    })

    it('sem source_text não existe nenhuma seção de fonte', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      const { container } = renderDetail('run-1')

      await screen.findByText('Brasília é a capital do Brasil.')
      expect(container.querySelector('.run-detail__source')).toBeNull()
      expect(screen.queryByText(/fonte fornecida/i)).not.toBeInTheDocument()
    })
  })

  describe('Resposta principal', () => {
    const primary = {
      contract_version: 'primary_answer_plan_v1',
      based_on_verdict_id: 'v-1',
      lead_in: 'Resposta principal, restrita ao que o debate e o Judge avaliaram (não é verificação externa):',
      sections: [
        {
          role: 'central_conclusion' as const,
          heading: 'Conclusão central:',
          items: [
            { claim_id: 'c1', claim_text: 'Brasília é a capital.', verdict_label: 'sustentada pelo debate' as const },
          ],
        },
      ],
      limitations: [],
      assessed_claim_count: 1,
      selected_claim_count: 1,
      omitted_not_established_count: 0,
      scope_note: 'Seleção apresentacional: 1 de 1 afirmações avaliadas pelo Judge. A avaliação completa lista todas.',
      rendered_text: 'TEXTO CANÔNICO',
    }

    it('domina a página, mantém a avaliação completa e o caminho de inspeção', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue({
        ...completedRun,
        final_answer: { ...completedRun.final_answer, primary_answer: primary },
      })
      renderDetail('run-1')

      expect(await screen.findByRole('heading', { level: 3, name: 'Conclusão central:' })).toBeVisible()
      expect(screen.getByText(/Ver avaliação completa/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /inspecionar execução/i })).toBeInTheDocument()
    })

    it('sem resposta principal (registro histórico) mostra a avaliação completa de sempre', async () => {
      vi.mocked(apiClient.getRun).mockResolvedValue(completedRun)
      renderDetail('run-1')

      expect(await screen.findByText('Brasília é a capital do Brasil.')).toBeVisible()
      expect(screen.queryByText(/Ver avaliação completa/)).not.toBeInTheDocument()
    })
  })
})
