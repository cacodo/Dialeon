// Direct Answer Execution V1 -- modo de resposta no composer.
//
// Conselho continua sendo o padrão e o envio do Conselho não muda. A resposta
// direta usa exatamente um modelo (escolha única), nunca envia fonte, e segue
// as mesmas regras de pré-requisito local: "met" pode ser escolhido
// automaticamente; "unknown" só por escolha explícita; "missing" nunca.

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RunComposer } from '../RunComposer'
import type { ReuseInput } from '../../lib/reuseInput'

type State = 'met' | 'missing' | 'unknown'

function setup(states: Record<string, State>, initialInput: ReuseInput | null = null) {
  const onSubmit = vi.fn()
  const props = {
    providersLoading: false,
    providersError: null as string | null,
    submitting: false,
    onSubmit,
    initialInput,
  }
  const view = render(
    <RunComposer {...props} providers={Object.keys(states)} localPrerequisites={states} />,
  )
  return {
    onSubmit,
    reload: (next: Record<string, State>) =>
      view.rerender(<RunComposer {...props} providers={Object.keys(next)} localPrerequisites={next} />),
  }
}

const ask = (text = 'Qual a capital?') => userEvent.type(screen.getByLabelText(/faça uma pergunta/i), text)
const submit = () => userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
const summary = (name: string) => screen.getByRole('button', { name })
const chooseDirect = () => userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
const chooseCouncil = () => userEvent.click(screen.getByRole('radio', { name: 'Conselho de modelos' }))

describe('RunComposer -- modo de resposta', () => {
  it('o Conselho é o padrão e o envio do Conselho continua exatamente o de sempre', async () => {
    const { onSubmit } = setup({ openai: 'met', anthropic: 'met' })

    expect(screen.getByRole('group', { name: 'Como responder' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Conselho de modelos' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Resposta direta' })).not.toBeChecked()

    await ask()
    await submit()
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai', 'anthropic'], null)
    expect(onSubmit.mock.calls[0]).toHaveLength(3) // nenhum `kind` no envio do Conselho
  })

  it('resposta direta: um modelo "met" (o primeiro escolhido no Conselho), sem fonte, enviada como direta', async () => {
    const { onSubmit } = setup({ anthropic: 'met', openai: 'met' })

    await chooseDirect()

    expect(summary('Modelo: Claude')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Fonte (opcional)' })).toBeNull()
    expect(screen.getByText(/um modelo responde sozinho, sem as etapas do conselho e sem fonte/i)).toBeVisible()
    await ask()
    await submit()
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['anthropic'], null, 'direct')
  })

  it('o painel da resposta direta é de escolha única; "missing" desabilitado; "unknown" só por escolha explícita', async () => {
    const { onSubmit } = setup({ openai: 'met', anthropic: 'missing', gemini: 'unknown' })
    await chooseDirect()
    await userEvent.click(summary('Modelo: GPT'))

    expect(screen.getByRole('group', { name: 'Modelo que vai responder' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'GPT' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Claude' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: 'Gemini' })).not.toBeChecked()

    await userEvent.click(screen.getByRole('radio', { name: 'Gemini' }))
    expect(summary('Modelo: Gemini')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'GPT' })).not.toBeChecked()

    await ask()
    await submit()
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['gemini'], null, 'direct')
  })

  it('sem nenhum "met", a resposta direta não escolhe nada sozinha e não envia', async () => {
    const { onSubmit } = setup({ openai: 'unknown', anthropic: 'missing' })
    await chooseDirect()

    expect(summary('Modelo: nenhum')).toBeInTheDocument()
    await ask()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), '{Control>}{Enter}{/Control}')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('Conselho → direta → Conselho: a fonte digitada nunca vai na direta e volta intacta no Conselho', async () => {
    const { onSubmit } = setup({ openai: 'met', anthropic: 'met' })
    await userEvent.click(screen.getByRole('button', { name: 'Fonte (opcional)' }))
    fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: 'um texto de referência' } })
    await ask()

    await chooseDirect()
    expect(screen.queryByLabelText(/fonte de texto/i)).toBeNull()
    expect(screen.getByText(/o texto de fonte que você colou não é enviado neste modo/i)).toBeVisible()
    await submit()
    expect(onSubmit).toHaveBeenLastCalledWith('Qual a capital?', ['openai'], null, 'direct')

    await chooseCouncil()
    expect(summary('Modelos: GPT, Claude')).toBeInTheDocument() // seleção do Conselho intacta
    expect(screen.getByLabelText(/fonte de texto/i)).toHaveValue('um texto de referência')
    await submit()
    expect(onSubmit).toHaveBeenLastCalledWith('Qual a capital?', ['openai', 'anthropic'], 'um texto de referência')
  })

  it('a escolha da direta não altera a seleção do Conselho, e vice-versa', async () => {
    setup({ openai: 'met', anthropic: 'met', gemini: 'met' })
    await chooseDirect()
    await userEvent.click(summary('Modelo: GPT'))
    await userEvent.click(screen.getByRole('radio', { name: 'Gemini' }))

    await chooseCouncil()
    expect(summary('Modelos: GPT, Claude, Gemini')).toBeInTheDocument()
    await chooseDirect()
    expect(summary('Modelo: Gemini')).toBeInTheDocument()
  })

  it('fonte acima do limite não bloqueia a resposta direta (ela não é enviada)', async () => {
    setup({ openai: 'met' })
    await userEvent.click(screen.getByRole('button', { name: 'Fonte (opcional)' }))
    fireEvent.change(screen.getByLabelText(/fonte de texto/i), { target: { value: 'b'.repeat(20_001) } })
    await ask()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()

    await chooseDirect()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeEnabled()
  })

  describe('recarga da lista no modo direto', () => {
    it('o modelo escolhido que passa a "missing" sai; nenhum outro entra no lugar', async () => {
      const { reload, onSubmit } = setup({ openai: 'met', anthropic: 'met' })
      await chooseDirect()
      expect(summary('Modelo: GPT')).toBeInTheDocument()

      reload({ openai: 'missing', anthropic: 'met' })

      expect(summary('Modelo: nenhum')).toBeInTheDocument()
      await ask()
      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('escolhido automaticamente como "met" e passando a "unknown": sai até ser escolhido de novo', async () => {
      const { reload, onSubmit } = setup({ openai: 'met' })
      await chooseDirect()

      reload({ openai: 'unknown' })
      expect(summary('Modelo: nenhum')).toBeInTheDocument()

      await userEvent.click(summary('Modelo: nenhum'))
      await userEvent.click(screen.getByRole('radio', { name: 'GPT' }))
      await ask()
      await submit()
      expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai'], null, 'direct')
    })

    it('"unknown" escolhido explicitamente continua escolhido numa recarga que o mantém "unknown"', async () => {
      const { reload } = setup({ openai: 'unknown' })
      await chooseDirect()
      await userEvent.click(summary('Modelo: nenhum'))
      await userEvent.click(screen.getByRole('radio', { name: 'GPT' }))

      reload({ openai: 'unknown' })

      expect(summary('Modelo: GPT')).toBeInTheDocument()
    })
  })

  describe('reuso de uma resposta direta', () => {
    const reuse = (provider: string): ReuseInput => ({
      question: 'Pergunta anterior',
      sourceText: null,
      enabledProviders: [provider],
      kind: 'direct',
    })

    it('volta no modo direto, com o mesmo modelo quando ele ainda está "met"', async () => {
      const { onSubmit } = setup({ openai: 'met', anthropic: 'met' }, reuse('anthropic'))

      expect(screen.getByRole('radio', { name: 'Resposta direta' })).toBeChecked()
      expect(summary('Modelo: Claude')).toBeInTheDocument()
      await submit()
      expect(onSubmit).toHaveBeenCalledWith('Pergunta anterior', ['anthropic'], null, 'direct')
    })

    it.each([
      ['missing', { openai: 'met', anthropic: 'missing' }, /falta a configuração local dele/],
      ['unknown', { openai: 'met', anthropic: 'unknown' }, /não foi possível verificar a configuração local dele/],
      ['removido', { openai: 'met' }, /não está mais na lista de modelos/],
    ] as const)('modelo anterior %s: não é trocado por outro, e a tela diz por quê', (_label, states, reason) => {
      setup(states as Record<string, State>, reuse('anthropic'))

      expect(summary('Modelo: nenhum')).toBeInTheDocument()
      expect(screen.getByRole('status')).toHaveTextContent(/O modelo usado antes \(Claude\) não foi marcado/)
      expect(screen.getByRole('status')).toHaveTextContent(reason)
      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()
    })

    it('o reuso de uma direta não pré-seleciona nada estranho no Conselho', async () => {
      setup({ openai: 'met', anthropic: 'met' }, reuse('anthropic'))
      await chooseCouncil()
      expect(summary('Modelos: GPT, Claude')).toBeInTheDocument()
    })
  })

  // M3 (revisão do Direct) -- a introdução sempre visível do composer
  // descreve o modo escolhido: comparação/concordância/divergência só no
  // Conselho.
  it('a introdução descreve o Conselho no Conselho e nunca promete comparação na resposta direta', async () => {
    setup({ openai: 'met', anthropic: 'met' })
    const councilClaims = /concordam|divergem|compara|consenso|juiz|verifica/i
    const visibleCouncilClaims = () =>
      screen
        .queryAllByText(councilClaims)
        .filter((el) => el.closest('[hidden]') === null && !/não é consenso nem verificação/i.test(el.textContent ?? ''))

    expect(
      screen.getByText(/os modelos escolhidos respondem de forma independente.*onde eles concordam, onde divergem/i),
    ).toBeVisible()

    await chooseDirect()

    expect(screen.getByText('O modelo escolhido responde sozinho, e o Dialeon mostra essa resposta.')).toBeVisible()
    expect(screen.queryByText(/respondem de forma independente/i)).toBeNull()
    expect(visibleCouncilClaims()).toEqual([])

    await chooseCouncil()
    expect(screen.getByText(/onde eles concordam, onde divergem/i)).toBeVisible()
  })
})
