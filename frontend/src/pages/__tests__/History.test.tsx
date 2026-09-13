import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { History } from '../History'
import { apiClient } from '../../api/client'

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

function renderHistory() {
  return render(
    <MemoryRouter>
      <History />
    </MemoryRouter>,
  )
}

describe('History', () => {
  it('mostra estado vazio quando não há execuções', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 20, offset: 0 })
    renderHistory()

    expect(await screen.findByText(/nenhuma execução ainda/i)).toBeInTheDocument()
  })

  it('lista execuções usando só os dados do summary (sem N+1)', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [
        { id: 'r1', status: 'completed', started_at: '2026-09-06T00:00:00Z', ended_at: '2026-09-06T00:00:05Z' },
        {
          id: 'r2',
          status: 'insufficient_quorum',
          started_at: '2026-09-05T00:00:00Z',
          ended_at: '2026-09-05T00:00:03Z',
        },
      ],
      limit: 20,
      offset: 0,
    })
    renderHistory()

    expect(await screen.findByText('Concluída')).toBeInTheDocument()
    expect(await screen.findByText('Quórum insuficiente')).toBeInTheDocument()
    expect(vi.mocked(apiClient.getRun)).not.toHaveBeenCalled()
    expect(vi.mocked(apiClient.getRunAudit)).not.toHaveBeenCalled()

    const links = screen.getAllByRole('link')
    const runLink = links.find((l) => l.getAttribute('href') === '/runs/r1')
    expect(runLink).toBeDefined()
  })

  it('T02.4: mostra rótulos pros estados running/failed, com ended_at nulo pro running', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: [
        { id: 'r3', status: 'running', started_at: '2026-09-06T00:00:00Z', ended_at: null },
        { id: 'r4', status: 'failed', started_at: '2026-09-05T00:00:00Z', ended_at: '2026-09-05T00:00:02Z' },
      ],
      limit: 20,
      offset: 0,
    })
    renderHistory()

    expect(await screen.findByText('Em andamento')).toBeInTheDocument()
    expect(await screen.findByText('Falhou')).toBeInTheDocument()
  })

  it('paginação avança o offset', async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      runs: Array.from({ length: 20 }, (_, i) => ({
        id: `r${i}`,
        status: 'completed' as const,
        started_at: '2026-09-06T00:00:00Z',
        ended_at: '2026-09-06T00:00:05Z',
      })),
      limit: 20,
      offset: 0,
    })
    renderHistory()

    await screen.findAllByText('Concluída')
    await userEvent.click(screen.getByRole('button', { name: /próxima/i }))

    expect(apiClient.listRuns).toHaveBeenLastCalledWith(20, 20)
  })
})
