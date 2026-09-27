// Direct Accepted Effective Model Choice V1 -- escolha opcional/avançada do
// modelo da resposta direta no composer.
//
// O caminho normal não muda (sem controle à vista, envio de sempre); uma
// escolha só vale para o provider escolhido e não vazia; vai verbatim; não há
// catálogo nem promessa de disponibilidade; o estado é separado do Conselho e
// preso ao provider; o reuso só traz uma escolha explícita, nunca para outro
// provider.

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RunComposer } from '../RunComposer'
import type { LocalPrerequisiteState } from '../../api/types'
import type { ReuseInput } from '../../lib/reuseInput'

function setup(
  listed: Record<string, LocalPrerequisiteState> = { openai: 'met', anthropic: 'met' },
  initialInput: ReuseInput | null = null,
) {
  const onSubmit = vi.fn()
  render(
    <RunComposer
      providers={Object.keys(listed)}
      localPrerequisites={listed}
      providersLoading={false}
      providersError={null}
      submitting={false}
      onSubmit={onSubmit}
      initialInput={initialInput}
    />,
  )
  return { onSubmit }
}

const ask = () => userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'Qual a capital?')
const submitButton = () => screen.getByRole('button', { name: 'Perguntar' })
const chooseDirect = () => userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
const chooseCouncil = () => userEvent.click(screen.getByRole('radio', { name: 'Conselho de modelos' }))
const openModel = () => userEvent.click(screen.getByRole('button', { name: /^Modelo:/ }))
const openDirectModel = () => userEvent.click(screen.getByRole('button', { name: 'Modelo específico (avançado)' }))
const directModelInput = (name: string) => screen.getByRole('textbox', { name: `Modelo para ${name}` })

describe('RunComposer -- modelo específico da resposta direta (avançado)', () => {
  it('o caminho normal não mostra o controle e envia exatamente como antes', async () => {
    const { onSubmit } = setup()
    await chooseDirect()
    await ask()

    expect(screen.queryByRole('button', { name: /Modelo específico/ })).not.toBeInTheDocument()
    await userEvent.click(submitButton())

    expect(onSubmit.mock.calls[0]).toEqual(['Qual a capital?', ['openai'], null, 'direct'])
  })

  it('fica recolhido dentro do painel de modelo; sem catálogo e sem prometer disponibilidade', async () => {
    setup()
    await chooseDirect()
    await openModel()

    const toggle = screen.getByRole('button', { name: 'Modelo específico (avançado)' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('textbox', { name: /Modelo para/ })).not.toBeInTheDocument()

    await openDirectModel()
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText(/não confere se o modelo existe nem se está disponível/)).toBeInTheDocument()
    expect(directModelInput('GPT')).toHaveAttribute('placeholder', 'modelo padrão configurado')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('uma escolha é enviada verbatim, só para a resposta direta', async () => {
    const { onSubmit } = setup()
    await chooseDirect()
    await ask()
    await openModel()
    await openDirectModel()
    await userEvent.type(directModelInput('GPT'), 'modèle-é:v2')
    await userEvent.click(submitButton())

    expect(onSubmit.mock.calls[0]).toEqual([
      'Qual a capital?',
      ['openai'],
      null,
      'direct',
      undefined,
      undefined,
      'modèle-é:v2',
    ])
  })

  it('em branco (ou só espaços) é o padrão: nada é enviado', async () => {
    const { onSubmit } = setup()
    await chooseDirect()
    await ask()
    await openModel()
    await openDirectModel()
    await userEvent.type(directModelInput('GPT'), '   ')
    await userEvent.click(submitButton())

    expect(onSubmit.mock.calls[0]).toEqual(['Qual a capital?', ['openai'], null, 'direct'])
  })

  it('trocar de provider não leva a escolha junto; voltar recupera a do mesmo provider', async () => {
    const { onSubmit } = setup()
    await chooseDirect()
    await ask()
    await openModel()
    await openDirectModel()
    await userEvent.type(directModelInput('GPT'), 'gpt-explicit')

    await userEvent.click(screen.getByRole('radio', { name: 'Claude' }))
    expect(directModelInput('Claude')).toHaveValue('')
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls.at(-1)).toEqual(['Qual a capital?', ['anthropic'], null, 'direct'])

    await userEvent.click(screen.getByRole('radio', { name: 'GPT' }))
    expect(directModelInput('GPT')).toHaveValue('gpt-explicit')
  })

  it('o Conselho não recebe a escolha da resposta direta, e vice-versa', async () => {
    const { onSubmit } = setup()
    await chooseDirect()
    await ask()
    await openModel()
    await openDirectModel()
    await userEvent.type(directModelInput('GPT'), 'gpt-direct')

    await chooseCouncil()
    expect(screen.queryByRole('button', { name: /Modelo específico/ })).not.toBeInTheDocument()
    await userEvent.click(submitButton())
    const council = onSubmit.mock.calls.at(-1)!
    expect(council[3]).toBeUndefined()
    expect(council).toHaveLength(5) // sem escolha de participante e sem modelo direto
    expect(JSON.stringify(council)).not.toContain('gpt-direct')

    // uma escolha de participante do Conselho não aparece na resposta direta
    await userEvent.click(screen.getByRole('button', { name: 'Modelo de cada participante (avançado)' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Modelo para Claude' }), 'claude-council')
    await chooseDirect()
    await userEvent.click(screen.getByRole('radio', { name: 'Claude' }))
    expect(directModelInput('Claude')).toHaveValue('')
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls.at(-1)).toEqual(['Qual a capital?', ['anthropic'], null, 'direct'])
  })

  it('reuso de uma escolha explícita: fica à vista, preenchida, e é enviada', async () => {
    const { onSubmit } = setup(
      { openai: 'met', anthropic: 'met' },
      {
        question: 'Pergunta anterior',
        sourceText: null,
        enabledProviders: ['openai'],
        kind: 'direct',
        directRequestedModel: 'gpt-explicit',
      },
    )

    expect(screen.getByRole('button', { name: 'Modelo específico (avançado)' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(directModelInput('GPT')).toHaveValue('gpt-explicit')
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls[0]).toEqual([
      'Pergunta anterior',
      ['openai'],
      null,
      'direct',
      undefined,
      undefined,
      'gpt-explicit',
    ])
  })

  it('reuso sem escolha explícita: nada preenchido, o envio de sempre', async () => {
    const { onSubmit } = setup(
      { openai: 'met', anthropic: 'met' },
      { question: 'Pergunta anterior', sourceText: null, enabledProviders: ['openai'], kind: 'direct' },
    )

    expect(screen.queryByRole('button', { name: /Modelo específico/ })).not.toBeInTheDocument()
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls[0]).toEqual(['Pergunta anterior', ['openai'], null, 'direct'])
  })

  it('reuso cujo provider não pode ser marcado: a escolha nunca vai para outro provider', async () => {
    const { onSubmit } = setup(
      { openai: 'missing', anthropic: 'met' },
      {
        question: 'Pergunta anterior',
        sourceText: null,
        enabledProviders: ['openai'],
        kind: 'direct',
        directRequestedModel: 'gpt-explicit',
      },
    )

    expect(screen.getByRole('status')).toHaveTextContent(/não foi marcado/)
    expect(screen.queryByRole('textbox', { name: /Modelo para/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('radio', { name: 'Claude' }))
    expect(directModelInput('Claude')).toHaveValue('')
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls[0]).toEqual(['Pergunta anterior', ['anthropic'], null, 'direct'])
  })

  // Revisão independente (LOW) -- uma escolha ativa não pode ficar escondida:
  // com os painéis fechados, o resumo (visível e acessível) diz qual modelo
  // específico vai ser pedido.
  describe('resumo recolhido com modelo específico ativo', () => {
    const summary = () => screen.getByRole('button', { name: /^Modelos?:/ })

    it('sem escolha, o resumo não fala em modelo específico', async () => {
      setup()
      await chooseDirect()

      expect(summary()).toHaveAccessibleName('Modelo: GPT')
      expect(summary()).not.toHaveTextContent(/específico/)
    })

    it('recolher não apaga a escolha: o resumo a mostra, o envio a leva e reabrir a traz', async () => {
      const { onSubmit } = setup()
      await chooseDirect()
      await ask()
      await openModel()
      await openDirectModel()
      await userEvent.type(directModelInput('GPT'), 'gpt-explicit')
      expect(summary()).toHaveAccessibleName('Modelo: GPT · modelo específico: gpt-explicit')

      await openDirectModel() // recolhe o avançado
      await userEvent.click(summary()) // recolhe o painel de modelo
      expect(screen.queryByRole('textbox', { name: /Modelo para/ })).not.toBeInTheDocument()
      expect(summary()).toHaveAttribute('aria-expanded', 'false')
      expect(summary()).toHaveTextContent('Modelo: GPT · modelo específico: gpt-explicit')
      expect(summary()).toHaveAccessibleName('Modelo: GPT · modelo específico: gpt-explicit')

      await userEvent.click(submitButton())
      expect(onSubmit.mock.calls.at(-1)).toEqual([
        'Qual a capital?',
        ['openai'],
        null,
        'direct',
        undefined,
        undefined,
        'gpt-explicit',
      ])

      await userEvent.click(summary())
      await openDirectModel()
      expect(directModelInput('GPT')).toHaveValue('gpt-explicit')
    })

    it('apagar a escolha tira o indicador e o modelo do envio', async () => {
      const { onSubmit } = setup()
      await chooseDirect()
      await ask()
      await openModel()
      await openDirectModel()
      await userEvent.type(directModelInput('GPT'), 'gpt-explicit')
      await userEvent.clear(directModelInput('GPT'))
      await userEvent.type(directModelInput('GPT'), '  ')
      await userEvent.click(summary())

      expect(summary()).toHaveAccessibleName('Modelo: GPT')
      await userEvent.click(submitButton())
      expect(onSubmit.mock.calls.at(-1)).toEqual(['Qual a capital?', ['openai'], null, 'direct'])
    })

    it('o indicador é só do provider escolhido agora, e só da resposta direta', async () => {
      setup()
      await chooseDirect()
      await openModel()
      await openDirectModel()
      await userEvent.type(directModelInput('GPT'), 'gpt-explicit')

      await userEvent.click(screen.getByRole('radio', { name: 'Claude' }))
      expect(summary()).toHaveAccessibleName('Modelo: Claude')

      await userEvent.click(screen.getByRole('radio', { name: 'GPT' }))
      expect(summary()).toHaveAccessibleName('Modelo: GPT · modelo específico: gpt-explicit')

      await chooseCouncil()
      expect(summary()).not.toHaveTextContent(/específico|gpt-explicit/)
      expect(summary()).toHaveAccessibleName(/^Modelos:/)
    })

    it('reuso: a escolha restaurada fica à vista, e continua no resumo depois de recolher', async () => {
      const { onSubmit } = setup(
        { openai: 'met', anthropic: 'met' },
        {
          question: 'Pergunta anterior',
          sourceText: null,
          enabledProviders: ['openai'],
          kind: 'direct',
          directRequestedModel: 'gpt-explicit',
        },
      )
      expect(directModelInput('GPT')).toHaveValue('gpt-explicit')

      await openDirectModel()
      await userEvent.click(summary())
      expect(screen.queryByRole('textbox', { name: /Modelo para/ })).not.toBeInTheDocument()
      expect(summary()).toHaveAccessibleName('Modelo: GPT · modelo específico: gpt-explicit')
      await userEvent.click(submitButton())
      expect(onSubmit.mock.calls[0]?.[6]).toBe('gpt-explicit')
    })
  })
})
