// Source disclosure UX fix -- o botão "Adicionar fonte de texto" precisa
// ser reversível (colapsado <-> expandido), preservando qualquer texto já
// digitado quando o painel é reaberto, e nunca só um `aria-expanded`
// fixo em `true`.

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RunComposer } from '../RunComposer'

function renderComposer() {
  return render(
    <RunComposer
      providers={['openai', 'anthropic']}
      providersLoading={false}
      providersError={null}
      submitting={false}
      onSubmit={() => {}}
    />,
  )
}

describe('RunComposer -- divulgação da fonte de texto', () => {
  it('começa colapsado, com aria-expanded=false e o painel fora do documento', () => {
    renderComposer()

    const toggle = screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText(/fonte de texto \(opcional\)/i)).not.toBeInTheDocument()
  })

  it('expande ao clicar, trocando o rótulo e aria-expanded pra true', async () => {
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' }))

    const toggle = screen.getByRole('button', { name: 'Ocultar fonte de texto' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByLabelText(/fonte de texto \(opcional\)/i)).toBeInTheDocument()
  })

  it('aria-controls do botão aponta pro id real do painel quando expandido', async () => {
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' }))

    const toggle = screen.getByRole('button', { name: 'Ocultar fonte de texto' })
    const controlsId = toggle.getAttribute('aria-controls')
    expect(controlsId).toBeTruthy()
    expect(document.getElementById(controlsId!)).toBeInTheDocument()
  })

  it('é reversível: colapsar esconde o painel, mas o botão continua disponível pra reabrir', async () => {
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' }))
    await userEvent.click(screen.getByRole('button', { name: 'Ocultar fonte de texto' }))

    expect(
      screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' }),
    ).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText(/fonte de texto \(opcional\)/i)).not.toBeInTheDocument()
  })

  it('colapsar NUNCA limpa o texto já digitado -- reabrir mostra exatamente o que foi escrito', async () => {
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' }))
    const sourceInput = screen.getByLabelText(/fonte de texto \(opcional\)/i)
    await userEvent.type(sourceInput, 'Trecho de fonte já digitado.')

    // fecha
    await userEvent.click(screen.getByRole('button', { name: 'Ocultar fonte de texto' }))
    expect(screen.queryByLabelText(/fonte de texto \(opcional\)/i)).not.toBeInTheDocument()

    // reabre
    await userEvent.click(screen.getByRole('button', { name: '+ Adicionar fonte de texto (opcional)' }))
    expect(screen.getByLabelText(/fonte de texto \(opcional\)/i)).toHaveValue(
      'Trecho de fonte já digitado.',
    )
  })
})
