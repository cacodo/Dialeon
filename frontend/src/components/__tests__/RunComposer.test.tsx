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
      localPrerequisites={{ openai: 'met', anthropic: 'met' }}
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
    const met = (ids: string[]) => Object.fromEntries(ids.map((id) => [id, 'met' as const]))
    const view = render(<RunComposer {...props} providers={providers} localPrerequisites={met(providers)} />)
    return {
      onSubmit,
      rediscover: (next: string[], overrides: Partial<typeof props> = {}) =>
        view.rerender(
          <RunComposer {...props} {...overrides} providers={next} localPrerequisites={met(next)} />,
        ),
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

// Pré-requisitos LOCAIS por modelo (GET /providers): "met" normal,
// "missing" visível mas desabilitado e explicado, "unknown" escolhível só
// por escolha explícita. Nada afirma que um serviço está disponível.
describe('RunComposer -- pré-requisitos locais dos modelos', () => {
  const STATES = { openai: 'met', anthropic: 'missing', gemini: 'unknown' } as const

  function renderStates(
    states: Record<string, 'met' | 'missing' | 'unknown'> = STATES,
    extra: { initialInput?: { question: string; sourceText: string | null; enabledProviders: string[] } } = {},
  ) {
    const onSubmit = vi.fn()
    const onRetryProviders = vi.fn()
    const props = {
      providersLoading: false,
      providersError: null as string | null,
      submitting: false,
      onSubmit,
      onRetryProviders,
      ...extra,
    }
    const providers = Object.keys(states)
    const view = render(<RunComposer {...props} providers={providers} localPrerequisites={states} />)
    return {
      onSubmit,
      onRetryProviders,
      reload: (next: Record<string, 'met' | 'missing' | 'unknown'>) =>
        view.rerender(<RunComposer {...props} providers={Object.keys(next)} localPrerequisites={next} />),
    }
  }

  const openPanel = (name: string | RegExp) => userEvent.click(screen.getByRole('button', { name }))

  it('pré-seleciona só os modelos com configuração local presente', () => {
    renderStates()
    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
  })

  it('"met": visível e escolhível, sem selo de prontidão', async () => {
    renderStates()
    await openPanel('Modelos: GPT')

    const gpt = screen.getByLabelText('GPT')
    expect(gpt).toBeEnabled()
    expect(gpt).toBeChecked()
    expect(gpt).not.toHaveAccessibleDescription()
  })

  it('"missing": visível, desabilitado e explicado como falta de configuração local', async () => {
    renderStates()
    await openPanel('Modelos: GPT')

    const claude = screen.getByLabelText('Claude')
    expect(claude).toBeDisabled()
    expect(claude).not.toBeChecked()
    expect(claude).toHaveAccessibleDescription('Falta configuração local nesta instalação')
  })

  it('"unknown": visível, não pré-selecionado, escolhível manualmente, sem selo positivo', async () => {
    const { onSubmit } = renderStates()
    await openPanel('Modelos: GPT')

    const gemini = screen.getByLabelText('Gemini')
    expect(gemini).toBeEnabled()
    expect(gemini).not.toBeChecked()
    expect(gemini).toHaveAccessibleDescription('Não foi possível verificar a configuração local')

    await userEvent.click(gemini)
    expect(screen.getByRole('button', { name: 'Modelos: GPT, Gemini' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'pergunta')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(onSubmit).toHaveBeenCalledWith('pergunta', ['openai', 'gemini'], null)
  })

  it('o reuso restaura só modelos "met": nunca um "missing", nem um "unknown" sem escolha explícita agora', async () => {
    const { onSubmit } = renderStates(STATES, {
      initialInput: { question: 'q', sourceText: null, enabledProviders: ['anthropic', 'gemini', 'openai'] },
    })

    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
    expect(onSubmit).toHaveBeenCalledWith('q', ['openai'], null)
  })

  it('reuso só com modelos sem configuração local cai pra pré-seleção padrão ("met")', () => {
    renderStates(STATES, { initialInput: { question: 'q', sourceText: null, enabledProviders: ['anthropic'] } })
    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
  })

  it('recarregar: um modelo que passa a "missing" sai da seleção e não volta sozinho', async () => {
    const { reload } = renderStates({ openai: 'met', anthropic: 'met' })
    expect(screen.getByRole('button', { name: 'Modelos: GPT, Claude' })).toBeInTheDocument()

    reload({ openai: 'met', anthropic: 'missing' })
    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()

    reload({ openai: 'met', anthropic: 'met' })
    expect(screen.getByRole('button', { name: 'Modelos: GPT' })).toBeInTheDocument()
  })

  describe('primeira execução sem nenhum modelo com configuração local', () => {
    const NONE_MET = { openai: 'missing', anthropic: 'missing', gemini: 'missing' } as const

    it('explica com calma o que falta, que é preciso reiniciar o servidor e recarregar', async () => {
      const { onRetryProviders } = renderStates(NONE_MET)

      const notice = screen.getByRole('region', { name: 'Falta a configuração local dos modelos' })
      expect(notice).toHaveTextContent(/oferece suporte a GPT, Claude e Gemini/)
      expect(notice).toHaveTextContent(/ainda não tem a configuração local necessária/)
      expect(notice).toHaveTextContent(/reinicie o servidor e recarregue a lista/)
      expect(notice).toHaveTextContent(/não garante que o serviço de cada modelo aceite as credenciais/)
      expect(notice).not.toHaveTextContent(/API_KEY|\.env|OPENAI|ANTHROPIC|GOOGLE/)
      expect(notice).not.toHaveAttribute('role', 'alert')

      expect(screen.getByRole('button', { name: 'Modelos: nenhum' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()

      await userEvent.click(screen.getByRole('button', { name: 'Recarregar lista de modelos' }))
      expect(onRetryProviders).toHaveBeenCalledTimes(1)
    })

    it('só "missing": o aviso pode afirmar que a configuração local falta', () => {
      renderStates(NONE_MET)

      const notice = screen.getByRole('region', { name: 'Falta a configuração local dos modelos' })
      expect(notice).toHaveTextContent(/não tem a configuração local necessária/)
      expect(notice).toHaveTextContent(/Recarregar só consulta o servidor de novo/)
    })

    it('só "unknown": aviso neutro -- nunca afirma que a configuração falta', () => {
      renderStates({ openai: 'unknown', gemini: 'unknown' })

      expect(screen.queryByRole('region', { name: 'Falta a configuração local dos modelos' })).toBeNull()
      const notice = screen.getByRole('region', { name: 'Nenhum modelo com configuração local confirmada' })
      expect(notice).toHaveTextContent(/oferece suporte a GPT e Gemini/)
      expect(notice).toHaveTextContent(/não confirmou a configuração local de nenhum deles/)
      expect(notice).toHaveTextContent(/GPT e Gemini: não dá para verificar daqui se a configuração local está presente/)
      expect(notice).toHaveTextContent(/podem ser escolhidos mesmo assim/)
      expect(notice).not.toHaveTextContent(/falta|ausente|não tem a configuração/i)
      expect(notice).toHaveTextContent(/reinicie o servidor e recarregue a lista/)
      expect(notice).toHaveTextContent(/não garante que o serviço de cada modelo aceite as credenciais/)
      expect(screen.getByRole('button', { name: 'Modelos: nenhum' })).toBeInTheDocument()
    })

    it('"missing" + "unknown": neutro no geral, "falta" só para os "missing"', () => {
      renderStates({ openai: 'missing', anthropic: 'missing', gemini: 'unknown' })

      const notice = screen.getByRole('region', { name: 'Nenhum modelo com configuração local confirmada' })
      expect(notice).toHaveTextContent(/oferece suporte a GPT, Claude e Gemini/)
      expect(notice).toHaveTextContent(/GPT e Claude: falta a configuração local necessária/)
      expect(notice).toHaveTextContent(/Gemini: não dá para verificar daqui/)
      expect(notice).toHaveTextContent(/Ele pode ser escolhido mesmo assim/)
      expect(notice).not.toHaveTextContent(/Gemini: falta/)
    })

    it('depois de reiniciar e recarregar, os modelos "met" são pré-selecionados e o aviso some', () => {
      const { reload } = renderStates(NONE_MET)

      reload({ openai: 'met', anthropic: 'met', gemini: 'missing' })

      expect(screen.getByRole('button', { name: 'Modelos: GPT, Claude' })).toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'Falta a configuração local dos modelos' })).toBeNull()
    })

    it('uma escolha manual feita antes do recarregamento não é substituída pela pré-seleção', async () => {
      const { reload } = renderStates({ openai: 'missing', gemini: 'unknown' })
      await openPanel('Modelos: nenhum')
      await userEvent.click(screen.getByLabelText('Gemini'))

      reload({ openai: 'met', gemini: 'unknown' })

      expect(screen.getByRole('button', { name: 'Modelos: Gemini' })).toBeInTheDocument()
    })
  })

  it('sem estado informado pelo servidor, nada é tratado como "met" (nada pré-selecionado)', () => {
    render(
      <RunComposer
        providers={['openai']}
        providersLoading={false}
        providersError={null}
        submitting={false}
        onSubmit={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: 'Modelos: nenhum' })).toBeInTheDocument()
  })

  it('nenhum texto sugere disponibilidade, saúde ou validação remota', async () => {
    const { reload } = renderStates()
    await openPanel('Modelos: GPT')
    const banned = /pront[oa]s?\b|dispon[ií]ve|indispon|online|offline|funcionando|saud[aá]ve|quebrad|v[aá]lid[ao]|configurad[oa]/i
    expect(document.body.textContent).not.toMatch(banned)

    reload({ openai: 'missing', anthropic: 'missing', gemini: 'unknown' })
    expect(document.body.textContent).not.toMatch(banned)
  })
})

// Transições de estado ao recarregar a lista: "met" pode ser escolhido
// automaticamente; "unknown" só entra por uma escolha explícita feita no
// estado "unknown" atual; "missing" nunca.
describe('RunComposer -- transições de pré-requisito ao recarregar', () => {
  type State = 'met' | 'missing' | 'unknown'
  const IDS = ['openai', 'anthropic'] as const

  function setup(initial: Record<string, State>) {
    const onSubmit = vi.fn()
    const props = { providersLoading: false, providersError: null, submitting: false, onSubmit }
    const view = render(<RunComposer {...props} providers={[...IDS]} localPrerequisites={initial} />)
    return {
      onSubmit,
      reload: (next: Record<string, State>) =>
        view.rerender(<RunComposer {...props} providers={[...IDS]} localPrerequisites={next} />),
    }
  }

  const summary = (name: string) => screen.getByRole('button', { name })
  async function openPanel(name: string) {
    await userEvent.click(summary(name))
  }
  async function submit() {
    await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))
  }

  it('"met" no início é selecionado automaticamente', () => {
    setup({ openai: 'met', anthropic: 'met' })
    expect(summary('Modelos: GPT, Claude')).toBeInTheDocument()
  })

  it('"met" → "unknown": sai da seleção e não vai no envio sem nova escolha', async () => {
    const { onSubmit, reload } = setup({ openai: 'met', anthropic: 'met' })

    reload({ openai: 'met', anthropic: 'unknown' })
    expect(summary('Modelos: GPT')).toBeInTheDocument()
    await openPanel('Modelos: GPT')
    expect(screen.getByLabelText('Claude')).toBeEnabled()
    expect(screen.getByLabelText('Claude')).not.toBeChecked()

    await submit()
    expect(onSubmit).toHaveBeenCalledWith('q', ['openai'], null)
  })

  it('"met" → "unknown" e então escolha explícita: entra na seleção e no envio', async () => {
    const { onSubmit, reload } = setup({ openai: 'met', anthropic: 'met' })
    reload({ openai: 'met', anthropic: 'unknown' })

    await openPanel('Modelos: GPT')
    await userEvent.click(screen.getByLabelText('Claude'))
    expect(summary('Modelos: GPT, Claude')).toBeInTheDocument()

    await submit()
    expect(onSubmit).toHaveBeenCalledWith('q', ['openai', 'anthropic'], null)
  })

  it('"unknown" no início não é selecionado automaticamente', () => {
    setup({ openai: 'met', anthropic: 'unknown' })
    expect(summary('Modelos: GPT')).toBeInTheDocument()
  })

  it('"unknown" escolhido explicitamente continua escolhido ao recarregar como "unknown"', async () => {
    const { onSubmit, reload } = setup({ openai: 'met', anthropic: 'unknown' })
    await openPanel('Modelos: GPT')
    await userEvent.click(screen.getByLabelText('Claude'))

    reload({ openai: 'met', anthropic: 'unknown' })
    reload({ openai: 'met', anthropic: 'unknown' })

    expect(summary('Modelos: GPT, Claude')).toBeInTheDocument()
    await submit()
    expect(onSubmit).toHaveBeenCalledWith('q', ['openai', 'anthropic'], null)
  })

  it('"unknown" escolhido → "missing": sai da seleção e fica desabilitado', async () => {
    const { onSubmit, reload } = setup({ openai: 'met', anthropic: 'unknown' })
    await openPanel('Modelos: GPT')
    await userEvent.click(screen.getByLabelText('Claude'))

    reload({ openai: 'met', anthropic: 'missing' })

    expect(summary('Modelos: GPT')).toBeInTheDocument()
    expect(screen.getByLabelText('Claude')).toBeDisabled()
    expect(screen.getByLabelText('Claude')).not.toBeChecked()
    await submit()
    expect(onSubmit).toHaveBeenCalledWith('q', ['openai'], null)
  })

  it('"missing" → "unknown": fica escolhível, mas não é selecionado automaticamente', async () => {
    const { reload } = setup({ openai: 'met', anthropic: 'missing' })

    reload({ openai: 'met', anthropic: 'unknown' })

    expect(summary('Modelos: GPT')).toBeInTheDocument()
    await openPanel('Modelos: GPT')
    expect(screen.getByLabelText('Claude')).toBeEnabled()
    expect(screen.getByLabelText('Claude')).not.toBeChecked()
  })

  it('"unknown" escolhido → "met": continua escolhido; se voltar a "unknown", sai (exige nova escolha)', async () => {
    const { reload } = setup({ openai: 'met', anthropic: 'unknown' })
    await openPanel('Modelos: GPT')
    await userEvent.click(screen.getByLabelText('Claude'))

    reload({ openai: 'met', anthropic: 'met' })
    expect(summary('Modelos: GPT, Claude')).toBeInTheDocument()

    reload({ openai: 'met', anthropic: 'unknown' })
    expect(summary('Modelos: GPT')).toBeInTheDocument()
  })

  it('"unknown" não escolhido → "met" depois da pré-seleção: não entra sozinho (lista nova só remove)', () => {
    const { reload } = setup({ openai: 'met', anthropic: 'unknown' })

    reload({ openai: 'met', anthropic: 'met' })

    expect(summary('Modelos: GPT')).toBeInTheDocument()
  })

  it('sem nenhum "met" e sem escolha: "unknown" → "met" dispara a pré-seleção', () => {
    const { reload } = setup({ openai: 'unknown', anthropic: 'missing' })
    expect(summary('Modelos: nenhum')).toBeInTheDocument()

    reload({ openai: 'met', anthropic: 'missing' })

    expect(summary('Modelos: GPT')).toBeInTheDocument()
  })
})
