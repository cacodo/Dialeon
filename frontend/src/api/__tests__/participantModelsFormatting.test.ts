import { describe, expect, it } from 'vitest'
import { formatInvalidRequest, formatReadinessGroupModel } from '../formatting'

describe('Council Accepted Effective Participant Model Choice V1 -- textos', () => {
  it('erro de escolha de modelo vira mensagem específica, sem a estrutura crua', () => {
    const text = formatInvalidRequest({
      errors: [{ loc: ['body', 'participant_model_overrides'], msg: 'Value error, ...', type: 'value_error' }],
    })
    expect(text).toBe(
      'Um modelo escolhido não é válido: use o identificador do fornecedor, sem espaços, e só para modelos selecionados.',
    )
  })

  it('modelo escolhido x configurado: rótulos distintos, sem prometer disponibilidade', () => {
    expect(
      formatReadinessGroupModel({ provider: 'openai', configuredModel: 'gpt-x', modelChosenForThisQuestion: true, roles: [] }),
    ).toBe('modelo escolhido nesta pergunta: gpt-x')
    expect(
      formatReadinessGroupModel({ provider: 'openai', configuredModel: 'gpt-d', modelChosenForThisQuestion: false, roles: [] }),
    ).toBe('modelo configurado: gpt-d')
  })
})
