// Source disclosure UX fix -- o botão "Fonte (opcional)" precisa ser
// reversível (colapsado <-> expandido), preservando qualquer texto já
// digitado quando o painel é reaberto, e nunca só um `aria-expanded`
// fixo em `true`. O rótulo do botão é estável (não muda de texto ao
// expandir/colapsar) -- o estado de disclosure é comunicado por
// `aria-expanded` + chevron, não por trocar o texto visível.

import { describe, expect, it, vi } from 'vitest'
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

    const toggle = screen.getByRole('button', { name: 'Fonte (opcional)' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText(/fonte de texto \(opcional\)/i)).not.toBeInTheDocument()
  })

  it('expande ao clicar, mantendo o rótulo estável e aria-expanded pra true', async () => {
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: 'Fonte (opcional)' }))

    const toggle = screen.getByRole('button', { name: 'Fonte (opcional)' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByLabelText(/fonte de texto \(opcional\)/i)).toBeInTheDocument()
  })

  it('aria-controls do botão aponta pro id real do painel quando expandido', async () => {
    renderComposer()

    const toggle = screen.getByRole('button', { name: 'Fonte (opcional)' })
    await userEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const controlsId = toggle.getAttribute('aria-controls')
    expect(controlsId).toBeTruthy()
    expect(document.getElementById(controlsId!)).toBeInTheDocument()
  })

  it('é reversível: colapsar esconde o painel, mas o botão continua disponível pra reabrir', async () => {
    renderComposer()

    const toggle = screen.getByRole('button', { name: 'Fonte (opcional)' })
    await userEvent.click(toggle)
    await userEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText(/fonte de texto \(opcional\)/i)).not.toBeInTheDocument()
  })

  it('colapsar NUNCA limpa o texto já digitado -- reabrir mostra exatamente o que foi escrito', async () => {
    renderComposer()

    const toggle = screen.getByRole('button', { name: 'Fonte (opcional)' })
    await userEvent.click(toggle)
    const sourceInput = screen.getByLabelText(/fonte de texto \(opcional\)/i)
    await userEvent.type(sourceInput, 'Trecho de fonte já digitado.')

    // fecha
    await userEvent.click(toggle)
    expect(screen.queryByLabelText(/fonte de texto \(opcional\)/i)).not.toBeInTheDocument()

    // reabre
    await userEvent.click(toggle)
    expect(screen.getByLabelText(/fonte de texto \(opcional\)/i)).toHaveValue(
      'Trecho de fonte já digitado.',
    )
  })
})

describe('RunComposer -- o que a fonte analisa (semântica)', () => {
  it('diz que a fonte é comparada com as afirmações identificadas no DEBATE, à parte da avaliação', async () => {
    renderComposer()
    await userEvent.click(screen.getByRole('button', { name: 'Fonte (opcional)' }))

    const hint = document.getElementById('source-hint')
    expect(hint).toHaveTextContent(/afirmações identificadas durante o debate/)
    expect(hint).toHaveTextContent(/não altera a avaliação das afirmações/)
    expect(hint).toHaveTextContent(/não é verificada como verdadeira/)
    expect(hint).not.toHaveTextContent(/afirmações da resposta/)
    const source = screen.getByLabelText(/fonte de texto \(opcional\)/i)
    expect(source).toHaveAttribute('placeholder', expect.stringMatching(/afirmações do debate/))
    expect(source.getAttribute('placeholder')).not.toMatch(/com a resposta/)
    expect(source).toHaveAccessibleDescription(/afirmações identificadas durante o debate/)
  })
})

// Nova descoberta de modelos (ex.: "Recarregar modelos" depois de um
// invalid_provider): o conjunto mostrado, o que habilita o envio e o que é
// enviado são sempre o mesmo -- a seleção restrita às opções devolvidas.
describe('RunComposer -- seleção reconciliada a cada descoberta de modelos', () => {
  function renderWith(providers: string[], onSubmit = vi.fn()) {
    const props = {
      providersLoading: false,
      providersError: null as string | null,
      submitting: false,
      onSubmit,
    }
    const view = render(<RunComposer {...props} providers={providers} />)
    return {
      onSubmit,
      rediscover: (next: string[], overrides: Partial<typeof props> = {}) =>
        view.rerender(<RunComposer {...props} {...overrides} providers={next} />),
    }
  }

  async function ask() {
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
  }

  it('mantém as escolhas que continuam existindo e remove as que sumiram', async () => {
    const { onSubmit, rediscover } = renderWith(['openai', 'anthropic', 'gemini'])
    await ask()

    rediscover(['openai', 'gemini'])

    expect(screen.getByRole('button', { name: 'Modelos: GPT, Gemini' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(onSubmit).toHaveBeenCalledWith('pergunta', ['openai', 'gemini'], null)
  })

  it('se nenhuma escolha continua existindo: "Modelos: nenhum" e envio bloqueado (inclusive por Ctrl+Enter)', async () => {
    const { onSubmit, rediscover } = renderWith(['openai', 'anthropic'])
    await ask()

    rediscover(['gemini'])

    expect(screen.getByRole('button', { name: 'Modelos: nenhum' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), '{Control>}{Enter}{/Control}')
    expect(onSubmit).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Modelos: nenhum' }))
    expect(screen.getByLabelText('Gemini')).not.toBeChecked()
  })

  it('uma escolha removida não volta sozinha se o modelo reaparecer depois', async () => {
    const { rediscover } = renderWith(['openai', 'anthropic'])
    await ask()

    rediscover(['openai'])
    rediscover(['openai', 'anthropic'])

    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
  })

  it('enquanto a lista recarrega, ou se a recarga falha, não há escolha válida: envio bloqueado', async () => {
    const { onSubmit, rediscover } = renderWith(['openai'])
    await ask()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeEnabled()

    rediscover(['openai'], { providersLoading: true })
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()

    rediscover(['openai'], { providersError: 'Erro inesperado.' })
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), '{Control>}{Enter}{/Control}')
    expect(onSubmit).not.toHaveBeenCalled()

    // a escolha ainda válida volta a valer quando a lista volta
    rediscover(['openai'])
    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeEnabled()
  })
})
