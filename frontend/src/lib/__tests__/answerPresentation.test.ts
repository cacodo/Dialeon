// A regra da forma apresentada da resposta final (lib/answerPresentation.ts)
// conferida contra a MESMA tabela de casos que o backend usa
// (app/presentation/answer_presentation.py) -- as duas implementações não
// podem divergir em silêncio.

import { describe, expect, it } from 'vitest'
import casesRaw from './answer_presentation_cases.json?raw'
import { selectAnswerPresentationKind, type AnswerPresentationInput } from '../answerPresentation'

interface Case {
  has_linguistic_realization: boolean
  linguistic_realization_presentation_eligible: boolean
  has_natural_answer: boolean
  natural_answer_presentation_eligible: boolean
  has_primary_answer: boolean
  expected: string
}

const { cases } = JSON.parse(casesRaw) as { cases: Case[] }

// Só a PRESENÇA importa pra regra; o conteúdo é irrelevante.
const present = {} as never

function input(c: Case): AnswerPresentationInput {
  return {
    linguistic_realization: c.has_linguistic_realization ? present : null,
    linguistic_realization_presentation_eligible: c.linguistic_realization_presentation_eligible,
    natural_answer: c.has_natural_answer ? present : null,
    natural_answer_presentation_eligible: c.natural_answer_presentation_eligible,
    primary_answer: c.has_primary_answer ? present : null,
  }
}

describe('forma apresentada da resposta final -- tabela compartilhada com o backend', () => {
  it('cobre todas as 32 combinações', () => {
    expect(cases).toHaveLength(32)
  })

  it.each(cases.map((c) => [JSON.stringify(c), c] as const))('%s', (_name, c) => {
    expect(selectAnswerPresentationKind(input(c))).toBe(c.expected)
  })
})
