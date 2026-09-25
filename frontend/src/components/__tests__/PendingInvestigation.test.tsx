import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { PendingInvestigation } from '../PendingInvestigation'
import { formatElapsed } from '../../api/formatting'

afterEach(() => {
  vi.useRealTimers()
})

describe('PendingInvestigation', () => {
  it('o tempo decorrido avança de forma determinística (relógio local)', () => {
    vi.useFakeTimers()
    render(<PendingInvestigation />)
    expect(screen.getByText('0 s')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(5_000)
    })
    expect(screen.getByText('5 s')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(screen.getByText('1 min 05 s')).toBeInTheDocument()
  })

  it('a linha de status viva é estática (o contador não anuncia a cada segundo)', () => {
    vi.useFakeTimers()
    render(<PendingInvestigation />)

    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Aguardando a resposta…')
    expect(status).not.toHaveTextContent(/tempo decorrido|\d+ s/)
  })

  it('só honestidade: sem etapas, percentuais, previsão de término nem progresso por participante', () => {
    vi.useFakeTimers()
    const { container } = render(<PendingInvestigation />)
    const text = container.textContent ?? ''

    expect(text).toMatch(/pode levar vários minutos/i)
    expect(text).not.toMatch(/%|etapa|estimad|previs|restante|faltam|concluíd|claude|gpt|gemini/i)
    expect(container.querySelector('progress, [role="progressbar"]')).toBeNull()
  })

  it('a orientação sobre sair da página é precisa: nunca diz que a execução/resultado foi perdido ou cancelado', () => {
    vi.useFakeTimers()
    const { container } = render(<PendingInvestigation />)
    const text = container.textContent ?? ''

    expect(text).toMatch(/deixará de aparecer aqui/i)
    expect(text).toMatch(/Histórico/)
    expect(text).not.toMatch(/perd(er|ido|erá)|cancel|interromp|em andamento|continua(rá)? (rodando|executando)/i)
  })
})

describe('formatElapsed', () => {
  it.each([
    [0, '0 s'],
    [59.9, '59 s'],
    [60, '1 min 00 s'],
    [125, '2 min 05 s'],
    [-3, '0 s'],
  ])('%s s -> %s', (seconds, expected) => {
    expect(formatElapsed(seconds)).toBe(expected)
  })
})
