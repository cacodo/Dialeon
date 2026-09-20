import { describe, expect, it } from 'vitest'
import { characterCount, MAX_QUESTION_CHARACTERS, MAX_SOURCE_TEXT_CHARACTERS } from '../inputLimits'
import { buildReuseState, parseReuseInput } from '../reuseInput'
import { formatInvalidRequest } from '../../api/formatting'

describe('inputLimits', () => {
  it('espelha os limites canônicos de 20.000 caracteres', () => {
    expect(MAX_QUESTION_CHARACTERS).toBe(20_000)
    expect(MAX_SOURCE_TEXT_CHARACTERS).toBe(20_000)
  })

  it('conta code points (como o backend), não unidades UTF-16', () => {
    expect(characterCount('abc')).toBe(3)
    expect(characterCount('😀😀')).toBe(2)
    expect(characterCount('')).toBe(0)
  })
})

describe('reuseInput', () => {
  const config = {
    question: 'Q',
    source_text: null,
    enabled_providers: ['openai', 'anthropic'],
    // campos extras (config completa) NUNCA viajam
    judge_provider: 'anthropic',
    max_total_tokens: 1,
  }

  it('só carrega pergunta, fonte e participantes', () => {
    const state = buildReuseState(config)

    expect(state).toEqual({
      reuseInput: { question: 'Q', sourceText: null, enabledProviders: ['openai', 'anthropic'] },
    })
    expect(Object.keys(state.reuseInput).sort()).toEqual(['enabledProviders', 'question', 'sourceText'])
  })

  it('parse aceita o formato válido e rejeita qualquer forma inesperada', () => {
    expect(parseReuseInput(buildReuseState(config))).toEqual({
      question: 'Q',
      sourceText: null,
      enabledProviders: ['openai', 'anthropic'],
    })
    for (const bad of [
      undefined,
      null,
      'x',
      {},
      { reuseInput: null },
      { reuseInput: { question: 1, sourceText: null, enabledProviders: [] } },
      { reuseInput: { question: 'q', sourceText: 5, enabledProviders: [] } },
      { reuseInput: { question: 'q', sourceText: null, enabledProviders: [1] } },
      { reuseInput: { question: 'q', sourceText: null, enabledProviders: 'openai' } },
    ]) {
      expect(parseReuseInput(bad)).toBeNull()
    }
  })

  it('descarta chaves extras (ex.: artefatos de modelo manufaturados)', () => {
    const parsed = parseReuseInput({
      reuseInput: { question: 'q', sourceText: null, enabledProviders: ['openai'], final_answer: 'x', parent_run_id: 'r' },
    })

    expect(parsed).toEqual({ question: 'q', sourceText: null, enabledProviders: ['openai'] })
  })
})

describe('formatInvalidRequest', () => {
  it('mapeia o NOME do campo para uma mensagem útil, sem expor estrutura crua', () => {
    const message = formatInvalidRequest({
      errors: [
        { loc: ['body', 'question'], msg: 'Value error, x', type: 'value_error' },
        { loc: ['body', 'enabled_providers'], msg: 'y', type: 'value_error' },
      ],
    })

    expect(message).toMatch(/pergunta não é válida.*20\.000/)
    expect(message).toMatch(/ao menos um participante/)
    expect(message).not.toMatch(/Value error|value_error|body/)
  })

  it.each([[null], [{}], [{ errors: 'x' }], [{ errors: [{ loc: 'nope' }] }], [{ errors: [null] }]])(
    'formato inesperado (%j) cai no rótulo genérico',
    (details) => {
      expect(formatInvalidRequest(details as Record<string, unknown> | null)).toBe('Verifique os dados informados.')
    },
  )
})
