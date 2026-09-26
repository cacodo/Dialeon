import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ParticipantModelsView } from '../ParticipantModelsView'

describe('ParticipantModelsView', () => {
  it('run anterior ao registro: "não registrados", nunca o padrão atual', () => {
    render(<ParticipantModelsView choices={null} />)
    expect(screen.getByText(/não registrados \(execução anterior a este registro\)/)).toBeInTheDocument()
  })

  it('mostra o modelo pedido e a origem de cada participante', () => {
    render(
      <ParticipantModelsView
        choices={[
          { provider: 'openai', requested_model: 'gpt-explicit', origin: 'run_override' },
          { provider: 'gemini', requested_model: 'gemini-configured', origin: 'configured_default' },
        ]}
      />,
    )
    expect(screen.getByText(/^GPT:/)).toHaveTextContent('GPT: gpt-explicit (escolhido nesta pergunta)')
    expect(screen.getByText(/^Gemini:/)).toHaveTextContent('Gemini: gemini-configured (padrão configurado)')
  })
})
