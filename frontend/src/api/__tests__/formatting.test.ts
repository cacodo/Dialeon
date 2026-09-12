import { describe, expect, it } from 'vitest'
import { formatEstimatedCost, formatTokenCount, formatDebateOutcome, formatFinalAnswerStatus } from '../formatting'

describe('formatEstimatedCost', () => {
  it('formata custo desconhecido (null) sem virar zero', () => {
    expect(formatEstimatedCost(null, false)).toBe('Estimativa indisponível')
  })

  it('formata custo conhecido-zero distinto de desconhecido', () => {
    const result = formatEstimatedCost(0, false)
    expect(result).toContain('$0,00')
    expect(result).not.toBe('Estimativa indisponível')
  })

  it('formata custo positivo conhecido', () => {
    expect(formatEstimatedCost(0.0042, false)).toContain('0.0042')
  })

  it('sinaliza quando has_unknown_accounting_components é true mesmo com custo conhecido', () => {
    const result = formatEstimatedCost(0.5, true)
    expect(result).toContain('parcial')
  })
})

describe('formatTokenCount', () => {
  it('tokens desconhecidos nunca viram 0', () => {
    const result = formatTokenCount(null)
    expect(result).not.toBe('0')
  })

  it('tokens conhecidos são formatados', () => {
    expect(formatTokenCount(1000)).not.toBe('—')
  })
})

describe('formatFinalAnswerStatus', () => {
  it('exibe um rótulo conhecido para o novo status llm_planned (Etapa 17B)', () => {
    const result = formatFinalAnswerStatus('llm_planned')
    expect(result).not.toBe('llm_planned') // nunca mostra o valor bruto
    expect(result.length).toBeGreaterThan(0)
  })

  it('continua exibindo um rótulo conhecido para o status histórico llm_composed', () => {
    const result = formatFinalAnswerStatus('llm_composed')
    expect(result).not.toBe('llm_composed')
    expect(result.length).toBeGreaterThan(0)
  })

  it('llm_planned e llm_composed têm rótulos distintos', () => {
    expect(formatFinalAnswerStatus('llm_planned')).not.toBe(formatFinalAnswerStatus('llm_composed'))
  })
})

describe('formatDebateOutcome', () => {
  it('retorna null quando não houve skip (nada a explicar)', () => {
    expect(formatDebateOutcome({ skipped_reason: null, cumulative_budget_exceeded: false })).toBeNull()
  })

  it('formata motivo conhecido de forma legível', () => {
    const result = formatDebateOutcome({
      skipped_reason: 'insufficient_initial_quorum',
      cumulative_budget_exceeded: false,
    })
    expect(result).not.toBe('insufficient_initial_quorum')
    expect(result).toBeTruthy()
  })

  it('cai de volta pro valor cru se o motivo não tiver label conhecido (nunca inventa explicação)', () => {
    const result = formatDebateOutcome({
      skipped_reason: 'motivo_novo_desconhecido',
      cumulative_budget_exceeded: false,
    })
    expect(result).toBe('motivo_novo_desconhecido')
  })
})
