// UI Slice 2 -- a resposta final ganhou quebra tipográfica em parágrafos
// (splitAnswerParagraphs), preservando exatamente o texto/limitations/
// status já existentes -- nunca reinterpretando o conteúdo.

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FinalAnswerView } from '../FinalAnswerView'
import type { FinalAnswerPublic } from '../../api/types'

function makeFinalAnswer(overrides: Partial<FinalAnswerPublic> = {}): FinalAnswerPublic {
  return {
    answer_text: 'Brasília é a capital do Brasil.',
    limitations: [],
    status: 'llm_planned',
    editor_model: 'claude-sonnet-5',
    editor_model_identity_source: 'provider_reported',
    judge_confidence: 0.8,
    ...overrides,
  }
}

describe('FinalAnswerView', () => {
  it('renderiza uma resposta de bloco único como um único parágrafo', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer()} />)

    const paragraph = screen.getByText('Brasília é a capital do Brasil.')
    expect(paragraph.tagName).toBe('P')
  })

  it('quebra blocos separados por linha em branco em parágrafos distintos, sem alterar o texto', () => {
    const answerText =
      'Resultado da avaliação do debate:\n\n' +
      'Conclusões sustentadas pelo debate:\n- A receita cresceu 12% em 2025.\n\n' +
      'Limitações do debate:\n- Só uma rodada de debate.'
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_text: answerText })} />)

    expect(screen.getByText('Resultado da avaliação do debate:').tagName).toBe('P')
    expect(
      screen.getByText((_, node) => node?.textContent === 'Conclusões sustentadas pelo debate:\n- A receita cresceu 12% em 2025.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText((_, node) => node?.textContent === 'Limitações do debate:\n- Só uma rodada de debate.'),
    ).toBeInTheDocument()
  })

  it('nunca inventa parágrafo quando a resposta é uma única linha (nenhum \\n\\n presente)', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ answer_text: 'Uma linha só.' })} />)

    expect(screen.getAllByText('Uma linha só.')).toHaveLength(1)
  })

  it('continua mostrando limitações quando presentes', () => {
    render(
      <FinalAnswerView
        finalAnswer={makeFinalAnswer({ limitations: ['Só uma rodada de debate.'] })}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Limitações' })).toBeInTheDocument()
    expect(screen.getByText('Só uma rodada de debate.')).toBeInTheDocument()
  })

  it('nunca mostra a seção de limitações quando a lista está vazia', () => {
    render(<FinalAnswerView finalAnswer={makeFinalAnswer({ limitations: [] })} />)

    expect(screen.queryByRole('heading', { name: 'Limitações' })).not.toBeInTheDocument()
  })
})
