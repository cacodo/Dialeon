// Direct Accepted Effective Model Choice V1 -- reuso de uma run direta: só
// uma escolha EXPLÍCITA viaja; o padrão configurado da run anterior nunca.

import { describe, expect, it } from 'vitest'
import { buildDirectReuseState, parseReuseInput, REUSE_STATE_KEY } from '../reuseInput'

const base = { question: 'q', provider: 'openai', requested_model: 'gpt-x' }

describe('reuso da resposta direta -- modelo', () => {
  it('run_override: o modelo viaja', () => {
    const state = buildDirectReuseState({ ...base, requested_model_origin: 'run_override' })
    expect(state[REUSE_STATE_KEY].directRequestedModel).toBe('gpt-x')
    expect(parseReuseInput(state)).toEqual(state[REUSE_STATE_KEY])
  })

  it('configured_default (inclusive run anterior a este registro): nada viaja', () => {
    const state = buildDirectReuseState({ ...base, requested_model_origin: 'configured_default' })
    expect(state[REUSE_STATE_KEY]).not.toHaveProperty('directRequestedModel')
  })

  it('estado manufaturado: só string não vazia, só na resposta direta', () => {
    const direct = { question: 'q', sourceText: null, enabledProviders: ['openai'], kind: 'direct' }
    expect(parseReuseInput({ [REUSE_STATE_KEY]: { ...direct, directRequestedModel: 5 } })).not.toHaveProperty(
      'directRequestedModel',
    )
    expect(parseReuseInput({ [REUSE_STATE_KEY]: { ...direct, directRequestedModel: '  ' } })).not.toHaveProperty(
      'directRequestedModel',
    )
    const council = { question: 'q', sourceText: null, enabledProviders: ['openai'], directRequestedModel: 'gpt-x' }
    expect(parseReuseInput({ [REUSE_STATE_KEY]: council })).not.toHaveProperty('directRequestedModel')
  })
})
