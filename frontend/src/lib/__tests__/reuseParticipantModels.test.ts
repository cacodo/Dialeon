// Council Accepted Effective Participant Model Choice V1 -- "Perguntar de
// novo" só carrega escolhas EXPLÍCITAS do mapa aceito; um participante que
// usou o padrão usa o padrão ATUAL; nada vem das tentativas.
import { describe, expect, it } from 'vitest'
import { buildReuseState, parseReuseInput } from '../reuseInput'

const base = { question: 'q', source_text: null, enabled_providers: ['openai', 'gemini'] }

describe('reuso das escolhas de modelo', () => {
  it('só as escolhas explícitas viajam', () => {
    const state = buildReuseState({
      ...base,
      participant_models: [
        { provider: 'openai', requested_model: 'gpt-configured-then', origin: 'configured_default' },
        { provider: 'gemini', requested_model: 'gemini-explicit', origin: 'run_override' },
      ],
    })

    expect(parseReuseInput(state)).toEqual({
      question: 'q',
      sourceText: null,
      enabledProviders: ['openai', 'gemini'],
      participantModelOverrides: { gemini: 'gemini-explicit' },
    })
  })

  it('run sem escolha explícita, ou anterior ao registro (null/ausente): nenhuma escolha', () => {
    for (const participant_models of [
      undefined,
      null,
      [{ provider: 'openai', requested_model: 'gpt-configured-then', origin: 'configured_default' }],
    ]) {
      const parsed = parseReuseInput(buildReuseState({ ...base, participant_models }))
      expect(parsed).not.toHaveProperty('participantModelOverrides')
    }
  })

  it('estado malformado não vira escolha', () => {
    const parsed = parseReuseInput({
      reuseInput: { question: 'q', sourceText: null, enabledProviders: ['openai'], participantModelOverrides: { openai: 5 } },
    })
    expect(parsed).toEqual({ question: 'q', sourceText: null, enabledProviders: ['openai'] })
  })
})
