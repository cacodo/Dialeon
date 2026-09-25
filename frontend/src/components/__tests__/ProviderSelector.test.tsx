// Seleção de modelos: o resumo mostra NOMES (não uma contagem abstrata) e
// continua curto com muitos modelos; o painel usa opções `{ id, label }`.

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ModelSelectionPanel, ModelSummaryButton, type ModelOption } from '../ProviderSelector'

const OPTIONS: ModelOption[] = [
  { id: 'openai', label: 'GPT' },
  { id: 'anthropic', label: 'Claude' },
  { id: 'gemini', label: 'Gemini' },
  { id: 'novo-a', label: 'Novo-a' },
  { id: 'novo-b', label: 'Novo-b' },
]

function renderSummary(selected: string[], expanded = false) {
  return render(
    <ModelSummaryButton
      options={OPTIONS}
      selected={selected}
      expanded={expanded}
      onToggle={() => {}}
      panelId="painel"
    />,
  )
}

describe('ModelSummaryButton', () => {
  it('nomeia os modelos escolhidos, na ordem das opções (não na ordem da seleção)', () => {
    renderSummary(['anthropic', 'openai'])
    expect(screen.getByRole('button', { name: 'Modelos: GPT, Claude' })).toBeInTheDocument()
  })

  it('com mais de 3 modelos, mostra os 3 primeiros e "+N" pros demais', () => {
    renderSummary(['openai', 'anthropic', 'gemini', 'novo-a', 'novo-b'])
    expect(screen.getByRole('button', { name: 'Modelos: GPT, Claude, Gemini +2' })).toBeInTheDocument()
  })

  it('nenhum modelo escolhido é dito explicitamente', () => {
    renderSummary([])
    expect(screen.getByRole('button', { name: 'Modelos: nenhum' })).toBeInTheDocument()
  })

  it('é um disclosure: aria-expanded + aria-controls apontando pro painel', () => {
    renderSummary(['openai'], true)
    const button = screen.getByRole('button', { name: 'Modelos: GPT' })
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(button).toHaveAttribute('aria-controls', 'painel')
  })

  it('nunca afirma prontidão/credencial de um modelo', () => {
    const { container } = renderSummary(['openai', 'anthropic'])
    expect(container.textContent).not.toMatch(/pronto|configurad|disponíve|funcionando|credencia/i)
  })
})

describe('ModelSelectionPanel', () => {
  it('um checkbox por opção, rotulado pelo nome; alternar devolve os ids', async () => {
    const onChange = vi.fn()
    render(
      <ModelSelectionPanel options={OPTIONS.slice(0, 3)} selected={['openai']} onChange={onChange} panelId="painel" />,
    )

    expect(screen.getByRole('group', { name: 'Modelos que vão responder' })).toHaveAttribute('id', 'painel')
    expect(screen.getByLabelText('GPT')).toBeChecked()
    expect(screen.getByLabelText('Claude')).not.toBeChecked()

    await userEvent.click(screen.getByLabelText('Claude'))
    expect(onChange).toHaveBeenLastCalledWith(['openai', 'anthropic'])
    await userEvent.click(screen.getByLabelText('GPT'))
    expect(onChange).toHaveBeenLastCalledWith([])
  })

  it('sem nenhum modelo escolhido, explica o que falta para perguntar', () => {
    render(<ModelSelectionPanel options={OPTIONS.slice(0, 3)} selected={[]} onChange={() => {}} panelId="painel" />)
    expect(screen.getByText('Escolha pelo menos um modelo para perguntar.')).toBeInTheDocument()
  })
})
