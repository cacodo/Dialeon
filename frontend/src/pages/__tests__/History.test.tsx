import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { History } from '../History'
import { apiClient, ApiError } from '../../api/client'
import type { RunSummaryResponse } from '../../api/types'

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
  vi.mocked(apiClient.listRuns).mockReset()
})

function makeRun(overrides: Partial<RunSummaryResponse> = {}): RunSummaryResponse {
  return {
    id: 'r1',
    status: 'completed',
    started_at: '2026-09-06T00:00:00Z',
    ended_at: '2026-09-06T00:00:05Z',
    question: 'Qual a capital do Brasil?',
    ...overrides,
  }
}

function renderHistory(initialPath = '/runs') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/runs" element={<History />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('History — estado de carregamento e vazio', () => {
  it('mostra role="status" enquanto carrega', () => {
    vi.mocked(apiClient.listRuns).mockReturnValue(new Promise(() => {}))
    renderHistory()

    expect(screen.getByRole('status')).toHaveTextContent(/carregando histórico/i)
  })

  it('página 1 vazia: mensagem de histórico GENUINAMENTE vazio', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 21, offset: 0 })
    renderHistory()

    expect(await screen.findByText(/nenhuma pergunta ainda/i)).toBeInTheDocument()
  })
})

describe('History — pergunta como identidade primária', () => {
  it('a pergunta literal é o texto visível do link, sem título sintetizado', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [makeRun({ id: 'r1', question: 'Qual a capital do Brasil?' })],
      limit: 21,
      offset: 0,
    })
    renderHistory()

    const link = await screen.findByRole('link', { name: /qual a capital do brasil/i })
    expect(link).toHaveAttribute('href', '/runs/r1')
  })

  it('pergunta longa/com caracteres especiais permanece EXATA no DOM, nunca truncada/mutada', async () => {
    const longQuestion =
      'Esta é uma pergunta muito longa que deveria, em telas estreitas, quebrar em várias linhas ' +
      'visualmente — mas o texto completo, incluindo "aspas", <symbols> & pontuação, precisa continuar ' +
      'presente no DOM exatamente como foi persistido, sem nenhuma truncagem real da string.'
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [makeRun({ id: 'r1', question: longQuestion })],
      limit: 21,
      offset: 0,
    })
    const { container } = renderHistory()

    await screen.findByText('Concluída')
    // O nome acessível do link inclui data/status junto (contexto útil
    // pra leitor de tela) -- a prova de fidelidade exata é o elemento
    // que carrega SÓ a pergunta.
    const questionElement = container.querySelector('.history__question')!
    expect(questionElement.textContent).toBe(longQuestion)
    expect(questionElement.closest('a')).toHaveAttribute('href', '/runs/r1')
  })

  it('duas execuções com a MESMA pergunta permanecem entradas distintas (dois links separados)', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [
        makeRun({ id: 'r1', question: 'Pergunta repetida?', started_at: '2026-09-06T00:00:00Z' }),
        makeRun({ id: 'r2', question: 'Pergunta repetida?', started_at: '2026-09-05T00:00:00Z' }),
      ],
      limit: 21,
      offset: 0,
    })
    const { container } = renderHistory()

    await screen.findAllByText('Concluída')
    const questionElements = container.querySelectorAll('.history__question')
    expect(questionElements).toHaveLength(2)
    const hrefs = Array.from(questionElements)
      .map((el) => el.closest('a')?.getAttribute('href'))
      .sort()
    expect(hrefs).toEqual(['/runs/r1', '/runs/r2'])
  })

  it('question ausente/malformada (payload histórico) usa fallback neutro, NUNCA o UUID como título', async () => {
    const malformed = { ...makeRun({ id: 'r1' }), question: undefined } as unknown as RunSummaryResponse
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [malformed], limit: 21, offset: 0 })
    renderHistory()

    expect(await screen.findByText('Pergunta indisponível')).toBeInTheDocument()
    expect(screen.queryByText('r1')).not.toBeInTheDocument()
  })
})

describe('History — usa só os dados do summary (sem N+1)', () => {
  it('nunca busca detail/audit pra montar a listagem', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [
        makeRun({ id: 'r1', status: 'completed' }),
        makeRun({ id: 'r2', status: 'insufficient_quorum' }),
      ],
      limit: 21,
      offset: 0,
    })
    renderHistory()

    expect(await screen.findByText('Concluída')).toBeInTheDocument()
    expect(await screen.findByText('Sem respostas suficientes')).toBeInTheDocument()
    expect(vi.mocked(apiClient.getRun)).not.toHaveBeenCalled()
    expect(vi.mocked(apiClient.getRunAudit)).not.toHaveBeenCalled()
  })

  it('rótulos de lifecycle pros 4 status -- "running" NUNCA diz "Em andamento"', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [
        makeRun({ id: 'r1', status: 'completed' }),
        makeRun({ id: 'r2', status: 'insufficient_quorum' }),
        makeRun({ id: 'r3', status: 'running', ended_at: null }),
        makeRun({ id: 'r4', status: 'failed' }),
      ],
      limit: 21,
      offset: 0,
    })
    renderHistory()

    expect(await screen.findByText('Concluída')).toBeInTheDocument()
    expect(screen.getByText('Sem respostas suficientes')).toBeInTheDocument()
    expect(screen.getByText('Sem desfecho registrado')).toBeInTheDocument()
    expect(screen.getByText('Falhou durante a execução')).toBeInTheDocument()
    expect(screen.queryByText(/em andamento/i)).not.toBeInTheDocument()
  })

  it('não expõe visualmente UUID/provider/custo/tokens/veredito', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [makeRun({ id: 'uuid-nao-deveria-aparecer-visivel' })],
      limit: 21,
      offset: 0,
    })
    renderHistory()

    await screen.findByText('Concluída')
    expect(screen.queryByText('uuid-nao-deveria-aparecer-visivel')).not.toBeInTheDocument()
  })
})

describe('History — acessibilidade', () => {
  it('cada investigação é um único link nativo dentro de um <li> de uma <ul>', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [makeRun({})], limit: 21, offset: 0 })
    const { container } = renderHistory()

    await screen.findByRole('link')
    const list = container.querySelector<HTMLElement>('ul.history__list')!
    expect(list).toBeInTheDocument()
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getAllByRole('link')).toHaveLength(1)
  })

  it('started_at usa <time dateTime> com o valor ISO exato', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [makeRun({ started_at: '2026-09-06T13:45:00Z' })],
      limit: 21,
      offset: 0,
    })
    const { container } = renderHistory()

    await screen.findByText('Concluída')
    const time = container.querySelector('time')
    expect(time).not.toBeNull()
    expect(time).toHaveAttribute('dateTime', '2026-09-06T13:45:00Z')
  })
})

describe('History — paginação e estado de URL', () => {
  it('pede PAGE_SIZE+1 registros (21) na página 1 (offset 0)', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 21, offset: 0 })
    renderHistory('/runs')

    await screen.findByText(/nenhuma pergunta ainda/i)
    expect(apiClient.listRuns).toHaveBeenCalledWith(21, 0)
  })

  it('?page=2 na URL calcula o offset correto (20) e mostra "Página 2"', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [makeRun({ id: 'r21' })],
      limit: 21,
      offset: 20,
    })
    renderHistory('/runs?page=2')

    await screen.findByText('Concluída')
    expect(apiClient.listRuns).toHaveBeenCalledWith(21, 20)
    expect(screen.getByText('Página 2')).toBeInTheDocument()
  })

  it.each([['0'], ['-1'], ['abc'], ['1.5'], [''], ['Infinity'], ['1e308'], ['9007199254740993']])(
    'page=%s inválido/inseguro normaliza pra página 1 (offset 0), nunca erro visível',
    async (invalid) => {
      vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 21, offset: 0 })
      renderHistory(`/runs?page=${invalid}`)

      await screen.findByText(/nenhuma pergunta ainda/i)
      expect(apiClient.listRuns).toHaveBeenCalledWith(21, 0)
    },
  )

  it.each([['1'], ['2'], ['42']])(
    'page=%s válido continua sendo respeitado normalmente',
    async (valid) => {
      const page = Number(valid)
      vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 21, offset: (page - 1) * 20 })
      renderHistory(`/runs?page=${valid}`)

      await screen.findByText(page === 1 ? /nenhuma pergunta ainda/i : /esta página não tem perguntas/i)
      expect(apiClient.listRuns).toHaveBeenCalledWith(21, (page - 1) * 20)
    },
  )

  it('exatamente PAGE_SIZE (20) registros retornados: "Próxima" NUNCA aparece habilitada (sem falso positivo)', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: Array.from({ length: 20 }, (_, i) => makeRun({ id: `r${i}` })),
      limit: 21,
      offset: 0,
    })
    renderHistory()

    await screen.findAllByText('Concluída')
    expect(screen.queryByRole('link', { name: /próxima/i })).not.toBeInTheDocument()
    expect(screen.getByText('Próxima')).toBeInTheDocument() // presente como texto desabilitado
  })

  it('PAGE_SIZE+1 (21) registros retornados: "Próxima" aparece como link real', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: Array.from({ length: 21 }, (_, i) => makeRun({ id: `r${i}` })),
      limit: 21,
      offset: 0,
    })
    renderHistory()

    await screen.findAllByText('Concluída')
    // Só 20 exibidos, nunca o 21º (usado só pra decidir hasNext).
    expect(screen.getAllByRole('listitem')).toHaveLength(20)
    const nextLink = screen.getByRole('link', { name: /próxima/i })
    expect(nextLink).toHaveAttribute('href', '/runs?page=2')
  })

  it('"Anterior" ausente como link na página 1', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [makeRun({})], limit: 21, offset: 0 })
    renderHistory()

    await screen.findByText('Concluída')
    expect(screen.queryByRole('link', { name: /anterior/i })).not.toBeInTheDocument()
  })

  it('página > 1 com resultados: "Anterior" é um link real pra page-1', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [makeRun({})],
      limit: 21,
      offset: 20,
    })
    renderHistory('/runs?page=2')

    await screen.findByText('Concluída')
    expect(screen.getByRole('link', { name: /anterior/i })).toHaveAttribute('href', '/runs?page=1')
  })
})

describe('History — página além do fim (out-of-range) é distinta de histórico vazio', () => {
  it('page=1 vazio diz "Nenhuma pergunta ainda"; page=5 vazio NUNCA diz isso', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 21, offset: 80 })
    renderHistory('/runs?page=5')

    await screen.findByText(/esta página não tem perguntas/i)
    expect(screen.queryByText(/nenhuma pergunta ainda/i)).not.toBeInTheDocument()
  })

  it('página vazia além do fim oferece rota de volta pra uma página existente', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 21, offset: 80 })
    renderHistory('/runs?page=5')

    const back = await screen.findByRole('link', { name: /página anterior/i })
    expect(back).toHaveAttribute('href', '/runs?page=4')
  })
})

describe('History — erro e retry', () => {
  it('erro de requisição usa role="alert" e permite tentar de novo', async () => {
    vi.mocked(apiClient.listRuns).mockRejectedValueOnce(
      new ApiError(500, 'internal_error', 'falhou', null),
    )
    renderHistory()

    expect(await screen.findByRole('alert')).toBeInTheDocument()

    vi.mocked(apiClient.listRuns).mockResolvedValueOnce({
      runs: [makeRun({})],
      limit: 21,
      offset: 0,
    })
    await userEvent.click(screen.getByRole('button', { name: /tentar novamente/i }))

    expect(await screen.findByText('Concluída')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('retry refaz a busca pra MESMA página (nunca reseta pra página 1)', async () => {
    vi.mocked(apiClient.listRuns).mockRejectedValueOnce(
      new ApiError(500, 'internal_error', 'falhou', null),
    )
    renderHistory('/runs?page=3')

    await screen.findByRole('alert')
    expect(apiClient.listRuns).toHaveBeenLastCalledWith(21, 40)

    vi.mocked(apiClient.listRuns).mockResolvedValueOnce({ runs: [], limit: 21, offset: 40 })
    await userEvent.click(screen.getByRole('button', { name: /tentar novamente/i }))

    await screen.findByText(/esta página não tem perguntas/i)
    expect(apiClient.listRuns).toHaveBeenLastCalledWith(21, 40)
  })
})

// Helper de promise controlada -- permite ao teste decidir EXATAMENTE
// quando (e em que ordem) cada chamada a `listRuns` resolve/rejeita,
// pra provar o comportamento de tentativa-em-curso sem depender de
// timing real.
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('History — estado de tentativa de requisição (request-attempt), não só de página', () => {
  it('retry: some com o erro antigo, mostra loading, e o controle de retry não fica clicável enquanto a tentativa de substituição está pendente', async () => {
    const first = deferred<{ runs: RunSummaryResponse[]; limit: number; offset: number }>()
    vi.mocked(apiClient.listRuns).mockReturnValueOnce(first.promise)
    renderHistory()

    first.reject(new ApiError(500, 'internal_error', 'falhou', null))
    await screen.findByRole('alert')
    expect(apiClient.listRuns).toHaveBeenCalledTimes(1)

    const replacement = deferred<{ runs: RunSummaryResponse[]; limit: number; offset: number }>()
    vi.mocked(apiClient.listRuns).mockReturnValueOnce(replacement.promise)

    await userEvent.click(screen.getByRole('button', { name: /tentar novamente/i }))

    // O erro antigo já não é "atual" -- some IMEDIATAMENTE, antes da
    // tentativa de substituição resolver.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/carregando histórico/i)
    // O botão de retry só existe dentro do bloco de erro -- some junto,
    // então não há como clicá-lo de novo enquanto a substituição pende.
    expect(screen.queryByRole('button', { name: /tentar novamente/i })).not.toBeInTheDocument()
    expect(apiClient.listRuns).toHaveBeenCalledTimes(2)

    replacement.resolve({ runs: [makeRun({ id: 'r1' })], limit: 21, offset: 0 })

    expect(await screen.findByText('Concluída')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    // Exatamente uma requisição de substituição resultou do clique --
    // nenhuma chamada extra por causa do clique não poder repetir.
    expect(apiClient.listRuns).toHaveBeenCalledTimes(2)
  })

  it('página 1 → 2 → 1 com promises controladas: uma tentativa antiga nunca vence uma mais nova, mesmo resolvendo fora de ordem', async () => {
    // Navegação real via <Link> (URL como autoridade de navegação),
    // não um remount de MemoryRouter -- links de navegação de teste
    // ficam FORA de <History>, já que a própria paginação de History só
    // renderiza depois que a página atual resolve (e aqui a página 1
    // fica deliberadamente pendente).
    function Harness() {
      return (
        <>
          <nav>
            <Link to="/runs?page=1">ir pra página 1</Link>
            <Link to="/runs?page=2">ir pra página 2</Link>
          </nav>
          <Routes>
            <Route path="/runs" element={<History />} />
          </Routes>
        </>
      )
    }

    const page1First = deferred<{ runs: RunSummaryResponse[]; limit: number; offset: number }>()
    vi.mocked(apiClient.listRuns).mockReturnValueOnce(page1First.promise)

    const { container } = render(
      <MemoryRouter initialEntries={['/runs?page=1']}>
        <Harness />
      </MemoryRouter>,
    )
    expect(apiClient.listRuns).toHaveBeenLastCalledWith(21, 0)

    const page2 = deferred<{ runs: RunSummaryResponse[]; limit: number; offset: number }>()
    vi.mocked(apiClient.listRuns).mockReturnValueOnce(page2.promise)
    await userEvent.click(screen.getByRole('link', { name: 'ir pra página 2' }))
    expect(apiClient.listRuns).toHaveBeenLastCalledWith(21, 20)

    const page1Second = deferred<{ runs: RunSummaryResponse[]; limit: number; offset: number }>()
    vi.mocked(apiClient.listRuns).mockReturnValueOnce(page1Second.promise)
    await userEvent.click(screen.getByRole('link', { name: 'ir pra página 1' }))
    expect(apiClient.listRuns).toHaveBeenLastCalledWith(21, 0)
    expect(apiClient.listRuns).toHaveBeenCalledTimes(3)

    // Resolve fora de ordem: a tentativa da página 2 (já superada)
    // resolve primeiro, depois a PRIMEIRA tentativa da página 1
    // (também já superada), e só por último a tentativa ATUAL (segunda
    // ida à página 1).
    page2.resolve({ runs: [makeRun({ id: 'stale-page-2' })], limit: 21, offset: 20 })
    page1First.resolve({ runs: [makeRun({ id: 'stale-page-1-first' })], limit: 21, offset: 0 })

    // Ainda carregando -- nenhuma das duas escritas obsoletas virou
    // "atual".
    expect(screen.getByRole('status')).toHaveTextContent(/carregando histórico/i)
    expect(container.querySelector('.history__question')).not.toBeInTheDocument()

    page1Second.resolve({ runs: [makeRun({ id: 'current-page-1' })], limit: 21, offset: 0 })

    const questionEl = await screen.findByText('Qual a capital do Brasil?')
    expect(questionEl.closest('a')).toHaveAttribute('href', '/runs/current-page-1')
    expect(container.querySelectorAll('.history__question')).toHaveLength(1)
  })
})
