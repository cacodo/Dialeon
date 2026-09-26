// Council Local Execution Readiness & Admission V1 -- prévia de prontidão
// local no composer do Conselho.
//
// Caminho feliz sem mudança visível (e envio estrito); ausência local
// CONHECIDA numa etapa aparece antes do envio e só se pergunta com a escolha
// deliberada de seguir (envio padrão com reconhecimento); "não verificável" é
// incerteza, nunca falha; a resposta direta não usa nada disso.

import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RunComposer } from '../RunComposer'
import type {
  CouncilDependencyReadiness,
  CouncilReadiness,
  CouncilReadinessRequest,
  LocalPrerequisiteState,
} from '../../api/types'

type State = LocalPrerequisiteState

const MODELS: Record<string, string> = {
  openai: 'gpt-configured',
  anthropic: 'claude-configured',
  gemini: 'gemini-configured',
}

// Identidade FALSA mas determinística da degradação (o servidor usa SHA-256;
// aqui só importa ser estável e diferente pra conjuntos diferentes).
function fakeFingerprint(missing: string[]): string {
  let hex = ''
  for (let seed = 1; hex.length < 64; seed++) {
    let h = 2166136261 ^ seed
    for (const c of missing.join('|')) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0
    hex += h.toString(16).padStart(8, '0')
  }
  return `sha256:${hex.slice(0, 64)}`
}

// Monta a mesma forma que o servidor devolveria: papéis internos todos em
// "anthropic" (o default do deployment).
function readinessFor(
  request: CouncilReadinessRequest,
  states: Record<string, State>,
  internal = 'anthropic',
): CouncilReadiness {
  const dep = (
    role: CouncilDependencyReadiness['role'],
    provider: string,
    applicability: CouncilDependencyReadiness['applicability'],
  ): CouncilDependencyReadiness => ({
    role,
    provider,
    configured_default_model: MODELS[provider],
    local_prerequisite: states[provider] ?? 'met',
    applicability,
  })
  const dependencies = [
    ...request.enabled_providers.map((p) => dep('participant', p, 'selected')),
    dep('claim_extraction', internal, 'potential'),
    dep('source_analysis', internal, request.source_supplied ? 'potential' : 'not_applicable'),
    dep('judge', internal, 'potential'),
    dep('editor', internal, 'potential'),
    dep('semantic_review', internal, 'potential'),
  ]
  const applicable = dependencies.filter((d) => d.applicability !== 'not_applicable')
  const summary = applicable.some((d) => d.local_prerequisite === 'missing')
    ? 'some_missing'
    : applicable.some((d) => d.local_prerequisite === 'unknown')
      ? 'some_unknown'
      : 'all_met'
  const missing = applicable
    .filter((d) => d.local_prerequisite === 'missing')
    .map((d) => `${d.role}:${d.provider}:${d.configured_default_model}`)
    .sort()
  return {
    contract_version: 'council_local_readiness_v1',
    summary,
    strict_admission: summary === 'some_missing' ? 'blocked' : 'admissible',
    known_degradation_fingerprint: missing.length > 0 ? fakeFingerprint(missing) : null,
    dependencies,
  }
}

function setup(
  listed: Record<string, State>,
  internalStates: Record<string, State> = {},
  { rejectedReadiness = null }: { rejectedReadiness?: CouncilReadiness | null } = {},
) {
  const onSubmit = vi.fn()
  const previewReadiness = vi.fn((request: CouncilReadinessRequest) =>
    Promise.resolve(readinessFor(request, { ...listed, ...internalStates })),
  )
  const props = {
    providersLoading: false,
    providersError: null as string | null,
    submitting: false,
    onSubmit,
    previewReadiness,
    providers: Object.keys(listed),
    localPrerequisites: listed,
  }
  const view = render(<RunComposer {...props} rejectedReadiness={rejectedReadiness} />)
  return {
    onSubmit,
    previewReadiness,
    rerenderWithRejection: (readiness: CouncilReadiness) =>
      view.rerender(<RunComposer {...props} rejectedReadiness={readiness} />),
  }
}

const INTERNAL_MISSING = readinessFor(
  { enabled_providers: ['openai', 'gemini'], source_supplied: false },
  { anthropic: 'missing' },
)
const ACK_INTERNAL_MISSING = {
  readiness_admission: 'standard',
  acknowledge_known_degradation: true,
  acknowledged_degradation_fingerprint: INTERNAL_MISSING.known_degradation_fingerprint,
}

const ask = () => userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'Qual a capital?')
const submitButton = () => screen.getByRole('button', { name: 'Perguntar' })
const ackCheckbox = () =>
  screen.getByRole('checkbox', { name: /Perguntar mesmo assim, sabendo que a resposta pode sair incompleta/ })

describe('RunComposer -- prontidão local do Conselho', () => {
  it('caminho feliz: nada a mais na tela e o envio pede admissão estrita', async () => {
    const { onSubmit, previewReadiness } = setup({ openai: 'met', anthropic: 'met' })

    await waitFor(() =>
      expect(previewReadiness).toHaveBeenCalledWith({
        enabled_providers: ['openai', 'anthropic'],
        source_supplied: false,
      }),
    )
    await ask()
    expect(screen.queryByRole('heading', { name: /Falta configuração local/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Não dá para verificar daqui/)).not.toBeInTheDocument()
    await userEvent.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai', 'anthropic'], null, undefined, {
      readiness_admission: 'strict',
    })
  })

  it('ausência conhecida numa etapa interna: aviso compacto antes do envio, que fica bloqueado', async () => {
    const { onSubmit } = setup({ openai: 'met', gemini: 'met', anthropic: 'missing' })
    await ask()

    const notice = await screen.findByRole('region', { name: 'Falta configuração local para parte do Conselho' })
    expect(notice).toHaveTextContent(
      'Extração de afirmações, juiz, editor e revisão da redação: Claude (modelo configurado: claude-configured)',
    )
    expect(notice).toHaveTextContent('O Dialeon não troca de modelo por conta própria.')
    // a análise da fonte não se aplica sem fonte: não é listada
    expect(notice).not.toHaveTextContent('análise da fonte')
    expect(submitButton()).toBeDisabled()

    await userEvent.keyboard('{Control>}{Enter}{/Control}')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('seguir mesmo assim exige a escolha deliberada, e o envio registra o reconhecimento', async () => {
    const { onSubmit } = setup({ openai: 'met', gemini: 'met', anthropic: 'missing' })
    await ask()

    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    expect(submitButton()).toBeEnabled()
    await userEvent.click(submitButton())

    // o envio leva a identidade da degradação MOSTRADA, derivada pelo servidor
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai', 'gemini'], null, undefined, ACK_INTERNAL_MISSING)
  })

  it('o reconhecimento vale só pra degradação mostrada: mudar a fonte o desfaz', async () => {
    setup({ openai: 'met', gemini: 'met', anthropic: 'missing' })
    await ask()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))

    await userEvent.click(screen.getByRole('button', { name: /Fonte \(opcional\)/ }))
    await userEvent.type(screen.getByLabelText(/fonte de texto/i), 'um texto')

    const notice = await screen.findByRole('region', { name: 'Falta configuração local para parte do Conselho' })
    await waitFor(() => expect(notice).toHaveTextContent('análise da fonte'))
    expect(ackCheckbox()).not.toBeChecked()
    expect(submitButton()).toBeDisabled()
  })

  it('"não verificável" numa etapa interna é incerteza: nota neutra, envio livre e estrito', async () => {
    const { onSubmit } = setup({ openai: 'met', anthropic: 'unknown' }, {})
    await ask()

    expect(
      await screen.findByText(/Não dá para verificar daqui a configuração local de extração de afirmações/),
    ).toHaveTextContent('Isso não quer dizer que falte')
    expect(screen.queryByRole('checkbox', { name: /Perguntar mesmo assim/ })).not.toBeInTheDocument()
    expect(submitButton()).toBeEnabled()
    await userEvent.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai'], null, undefined, {
      readiness_admission: 'strict',
    })
  })

  it('sem prévia disponível, nada é afirmado e o envio segue estrito (o servidor decide)', async () => {
    const onSubmit = vi.fn()
    render(
      <RunComposer
        providers={['openai']}
        localPrerequisites={{ openai: 'met' }}
        providersLoading={false}
        providersError={null}
        submitting={false}
        onSubmit={onSubmit}
        previewReadiness={() => Promise.reject(new Error('offline'))}
      />,
    )
    await ask()
    await userEvent.click(submitButton())

    expect(screen.queryByRole('region', { name: /Falta configuração local/ })).not.toBeInTheDocument()
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai'], null, undefined, {
      readiness_admission: 'strict',
    })
  })

  it('uma recusa de admissão estrita mostra a avaliação do aceite e permite seguir deliberadamente', async () => {
    const listed = { openai: 'met' as State, gemini: 'met' as State, anthropic: 'met' as State }
    const { onSubmit, rerenderWithRejection } = setup(listed)
    await ask()
    await waitFor(() => expect(submitButton()).toBeEnabled())

    // a configuração mudou entre a prévia e o envio: o servidor recusou
    rerenderWithRejection(
      readinessFor({ enabled_providers: ['openai', 'gemini', 'anthropic'], source_supplied: false }, {
        anthropic: 'missing',
      }),
    )

    expect(await screen.findByRole('region', { name: 'Falta configuração local para parte do Conselho' })).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    await userEvent.click(ackCheckbox())
    await userEvent.click(submitButton())
    const rejected = readinessFor(
      { enabled_providers: ['openai', 'gemini', 'anthropic'], source_supplied: false },
      { anthropic: 'missing' },
    )
    expect(onSubmit).toHaveBeenLastCalledWith(
      'Qual a capital?',
      ['openai', 'gemini', 'anthropic'],
      null,
      undefined,
      {
        readiness_admission: 'standard',
        acknowledge_known_degradation: true,
        acknowledged_degradation_fingerprint: rejected.known_degradation_fingerprint,
      },
    )
  })

  it('resposta direta: nenhuma prévia do Conselho, nenhum aviso, envio de sempre', async () => {
    const { onSubmit, previewReadiness } = setup({ openai: 'met', anthropic: 'missing' })
    await userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
    previewReadiness.mockClear()
    await ask()
    await userEvent.click(submitButton())

    expect(previewReadiness).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: /Falta configuração local/ })).not.toBeInTheDocument()
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai'], null, 'direct')
    expect(onSubmit.mock.calls[0]).toHaveLength(4)
  })

  it('prévia antiga que chega DEPOIS de uma recusa do servidor não substitui a avaliação autoritativa', async () => {
    const listed = { openai: 'met' as State, gemini: 'met' as State, anthropic: 'met' as State }
    let resolveOld: (value: CouncilReadiness) => void = () => {}
    const onSubmit = vi.fn()
    const previewReadiness = vi.fn(
      () => new Promise<CouncilReadiness>((resolve) => (resolveOld = resolve)), // P1 fica pendente
    )
    const props = {
      providers: Object.keys(listed),
      localPrerequisites: listed,
      providersLoading: false,
      providersError: null,
      submitting: false,
      onSubmit,
      previewReadiness,
    }
    const view = render(<RunComposer {...props} rejectedReadiness={null} />)
    await ask()
    await waitFor(() => expect(previewReadiness).toHaveBeenCalledTimes(1))
    await userEvent.click(submitButton()) // envio (estrito) com a prévia P1 ainda pendente

    // o servidor recusa com a avaliação autoritativa P2: falta configuração local
    const request = { enabled_providers: ['openai', 'gemini', 'anthropic'], source_supplied: false }
    const authoritative = readinessFor(request, { anthropic: 'missing' })
    view.rerender(<RunComposer {...props} rejectedReadiness={authoritative} />)
    expect(await screen.findByRole('region', { name: /Falta configuração local/ })).toBeInTheDocument()

    // P1 (consultiva, pedida ANTES) chega depois dizendo que está tudo presente
    await act(async () => resolveOld(readinessFor(request, {})))

    expect(screen.getByRole('region', { name: /Falta configuração local/ })).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
  })

  describe('o reconhecimento vale só para a entrada e a degradação em que foi dado', () => {
    async function acknowledged() {
      const view = setup({ openai: 'met', gemini: 'met', anthropic: 'missing' })
      await ask()
      await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
      expect(submitButton()).toBeEnabled()
      return view
    }

    async function expectAcknowledgementRequiredAgain() {
      await waitFor(() => expect(ackCheckbox()).not.toBeChecked())
      expect(submitButton()).toBeDisabled()
    }

    it('editar a pergunta pede a escolha de novo', async () => {
      await acknowledged()
      await userEvent.type(screen.getByLabelText(/faça uma pergunta/i), ' E a de Portugal?')
      await expectAcknowledgementRequiredAgain()
    })

    it('trocar uma fonte não vazia por outra não vazia pede a escolha de novo', async () => {
      setup({ openai: 'met', gemini: 'met', anthropic: 'missing' })
      await ask()
      await userEvent.click(screen.getByRole('button', { name: /Fonte \(opcional\)/ }))
      const source = screen.getByLabelText(/fonte de texto/i)
      await userEvent.type(source, 'primeira fonte')
      await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
      expect(submitButton()).toBeEnabled()

      await userEvent.clear(source)
      await userEvent.type(source, 'outra fonte')
      await expectAcknowledgementRequiredAgain()
    })

    it('mudar a seleção de modelos pede a escolha de novo', async () => {
      await acknowledged()
      await userEvent.click(screen.getByRole('button', { name: /^Modelos:/ }))
      await userEvent.click(screen.getByLabelText('Gemini'))
      await expectAcknowledgementRequiredAgain()
    })

    it('uma degradação diferente vinda do servidor pede a escolha de novo, e a nova escolha reenvia com a nova identidade', async () => {
      const { onSubmit, rerenderWithRejection } = await acknowledged()

      // 409: a degradação avaliada no aceite é outra (agora falta o participante gemini)
      const changed = readinessFor(
        { enabled_providers: ['openai', 'gemini'], source_supplied: false },
        { gemini: 'missing' },
      )
      expect(changed.known_degradation_fingerprint).not.toBe(INTERNAL_MISSING.known_degradation_fingerprint)
      rerenderWithRejection(changed)

      expect(await screen.findByRole('region', { name: /Falta configuração local/ })).toHaveTextContent(
        'Participante: Gemini',
      )
      await expectAcknowledgementRequiredAgain()

      await userEvent.click(ackCheckbox())
      await userEvent.click(submitButton())
      expect(onSubmit).toHaveBeenLastCalledWith('Qual a capital?', ['openai', 'gemini'], null, undefined, {
        readiness_admission: 'standard',
        acknowledge_known_degradation: true,
        acknowledged_degradation_fingerprint: changed.known_degradation_fingerprint,
      })
    })
  })
})
