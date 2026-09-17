// UI Slice 2 -- cobertura do app shell: navegação persistente entre as
// rotas primárias (Perguntar/Histórico), com estado de rota atual
// exposto via `aria-current="page"` (nunca só por cor).

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { App } from '../App'
import { apiClient } from '../api/client'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
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
  vi.mocked(apiClient.getProviders).mockReset()
  vi.mocked(apiClient.listRuns).mockReset()
  vi.mocked(apiClient.getProviders).mockResolvedValue({ providers: ['openai'] })
  vi.mocked(apiClient.listRuns).mockResolvedValue({ runs: [], limit: 20, offset: 0 })
})

function renderApp(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App shell', () => {
  it('mostra a marca (texto-só) e os dois itens de navegação primária', async () => {
    renderApp()

    expect(screen.getByRole('link', { name: 'Dialeon' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Perguntar' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Histórico' })).toBeInTheDocument()
  })

  it('marca "Perguntar" como página atual (aria-current) em "/", nunca "Histórico"', async () => {
    renderApp('/')

    expect(screen.getByRole('link', { name: 'Perguntar' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Histórico' })).not.toHaveAttribute('aria-current')
  })

  it('marca "Histórico" como página atual em "/runs", nunca "Perguntar"', async () => {
    renderApp('/runs')

    await screen.findByText(/nenhuma execução ainda/i)

    expect(screen.getByRole('link', { name: 'Histórico' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Perguntar' })).not.toHaveAttribute('aria-current')
  })

  it('navega para o histórico ao clicar no item de navegação, preservando a experiência de pergunta', async () => {
    renderApp('/')
    await screen.findByLabelText(/faça uma pergunta/i)

    await userEvent.click(screen.getByRole('link', { name: 'Histórico' }))

    await screen.findByText(/nenhuma execução ainda/i)
    expect(screen.getByRole('link', { name: 'Histórico' })).toHaveAttribute('aria-current', 'page')
  })

  it('a marca sempre leva de volta pra experiência de pergunta', async () => {
    renderApp('/runs')
    await screen.findByText(/nenhuma execução ainda/i)

    await userEvent.click(screen.getByRole('link', { name: 'Dialeon' }))

    await screen.findByLabelText(/faça uma pergunta/i)
    expect(screen.getByRole('link', { name: 'Perguntar' })).toHaveAttribute('aria-current', 'page')
  })

  it('o shell persiste (mesmo header) através da navegação entre rotas', async () => {
    renderApp('/')
    await screen.findByLabelText(/faça uma pergunta/i)
    const headerBefore = screen.getByRole('link', { name: 'Dialeon' })

    await userEvent.click(screen.getByRole('link', { name: 'Histórico' }))
    await screen.findByText(/nenhuma execução ainda/i)

    expect(screen.getByRole('link', { name: 'Dialeon' })).toBe(headerBefore)
  })
})
