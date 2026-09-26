// Council Accepted Effective Participant Model Choice V1 -- escolha
// opcional/avançada do modelo de cada participante no composer do Conselho.
//
// O caminho normal não muda; uma escolha só vale para participante
// selecionado e não vazio; ela faz parte da prévia de prontidão (chave) e do
// escopo do reconhecimento; respostas antigas nunca passam por cima da
// entrada atual; um reconhecimento que caiu não revive; o reuso só traz
// escolhas explícitas; a resposta direta não usa nada disso.

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
import type { ReuseInput } from '../../lib/reuseInput'

type State = LocalPrerequisiteState

const DEFAULTS: Record<string, string> = {
  openai: 'gpt-configured',
  gemini: 'gemini-configured',
  anthropic: 'claude-configured',
}

// Mesma forma que o servidor devolveria (v2), com papéis internos em anthropic.
function readinessFor(request: CouncilReadinessRequest, states: Record<string, State>): CouncilReadiness {
  const overrides = request.participant_model_overrides ?? {}
  const dep = (
    role: CouncilDependencyReadiness['role'],
    provider: string,
    applicability: CouncilDependencyReadiness['applicability'],
  ): CouncilDependencyReadiness => ({
    role,
    provider,
    configured_default_model: DEFAULTS[provider],
    local_prerequisite: states[provider] ?? 'met',
    applicability,
    ...(role === 'participant'
      ? {
          planned_model: overrides[provider] ?? DEFAULTS[provider],
          planned_model_origin: provider in overrides ? 'run_override' : 'configured_default',
        }
      : {}),
  })
  const dependencies = [
    ...request.enabled_providers.map((p) => dep('participant', p, 'selected')),
    dep('claim_extraction', 'anthropic', 'potential'),
    dep('source_analysis', 'anthropic', 'not_applicable'),
    dep('judge', 'anthropic', 'potential'),
    dep('editor', 'anthropic', 'potential'),
    dep('semantic_review', 'anthropic', 'potential'),
  ]
  const missing = dependencies.filter((d) => d.applicability !== 'not_applicable' && d.local_prerequisite === 'missing')
  return {
    contract_version: 'council_local_readiness_v2',
    summary: missing.length > 0 ? 'some_missing' : 'all_met',
    strict_admission: missing.length > 0 ? 'blocked' : 'admissible',
    // identidade "do servidor": aqui, só determinística pela entrada
    known_degradation_fingerprint:
      missing.length > 0 ? `sha256:${'0'.repeat(63)}${Object.keys(overrides).length}` : null,
    dependencies,
  }
}

function setup(
  listed: Record<string, State>,
  {
    internalStates = {},
    initialInput = null,
    previewReadiness,
  }: {
    internalStates?: Record<string, State>
    initialInput?: ReuseInput | null
    previewReadiness?: (r: CouncilReadinessRequest) => Promise<CouncilReadiness>
  } = {},
) {
  const onSubmit = vi.fn()
  const preview =
    previewReadiness ??
    vi.fn((request: CouncilReadinessRequest) => Promise.resolve(readinessFor(request, { ...listed, ...internalStates })))
  render(
    <RunComposer
      providers={Object.keys(listed)}
      localPrerequisites={listed}
      providersLoading={false}
      providersError={null}
      submitting={false}
      onSubmit={onSubmit}
      previewReadiness={preview}
      initialInput={initialInput}
    />,
  )
  return { onSubmit, preview }
}

const ask = () => userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'Qual a capital?')
const submitButton = () => screen.getByRole('button', { name: 'Perguntar' })
const openModels = () => userEvent.click(screen.getByRole('button', { name: /^Modelos?:/ }))
const openOverrides = () => userEvent.click(screen.getByRole('button', { name: 'Modelo de cada participante (avançado)' }))
const overrideInput = (name: string) => screen.getByRole('textbox', { name: `Modelo para ${name}` })
const STRICT = { readiness_admission: 'strict' }

describe('RunComposer -- modelo de cada participante (avançado)', () => {
  it('o caminho normal não mostra o controle nem envia escolha', async () => {
    const { onSubmit } = setup({ openai: 'met', gemini: 'met' })
    await ask()

    expect(screen.queryByRole('button', { name: /Modelo de cada participante/ })).not.toBeInTheDocument()
    await userEvent.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai', 'gemini'], null, undefined, STRICT)
    expect(onSubmit.mock.calls[0]).toHaveLength(5)
  })

  it('uma escolha vale para a prévia e para o envio, sem catálogo e sem prometer disponibilidade', async () => {
    const { onSubmit, preview } = setup({ openai: 'met', gemini: 'met' })
    await ask()
    await openModels()
    await openOverrides()

    expect(screen.getByText(/não confere se o modelo existe nem se está disponível/)).toBeInTheDocument()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await waitFor(() => expect(overrideInput('Gemini')).toHaveAttribute('placeholder', 'gemini-configured'))
    await userEvent.type(overrideInput('Gemini'), 'gemini-explicit')

    await waitFor(() =>
      expect(preview).toHaveBeenLastCalledWith({
        enabled_providers: ['openai', 'gemini'],
        source_supplied: false,
        participant_model_overrides: { gemini: 'gemini-explicit' },
      }),
    )
    await userEvent.click(submitButton())
    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai', 'gemini'], null, undefined, STRICT, {
      gemini: 'gemini-explicit',
    })
  })

  it('escolha em branco é o padrão; escolha de participante desmarcado não é enviada', async () => {
    const { onSubmit } = setup({ openai: 'met', gemini: 'met' })
    await ask()
    await openModels()
    await openOverrides()
    await userEvent.type(overrideInput('GPT'), '   ')
    await userEvent.type(overrideInput('Gemini'), 'gemini-explicit')

    await userEvent.click(screen.getByRole('checkbox', { name: 'Gemini' })) // desmarca o Gemini
    await userEvent.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith('Qual a capital?', ['openai'], null, undefined, STRICT)
    expect(onSubmit.mock.calls[0]).toHaveLength(5)
  })

  it('trocar o modelo durante uma prévia: a resposta antiga nunca passa por cima da atual', async () => {
    const calls: { request: CouncilReadinessRequest; resolve: (r: CouncilReadiness) => void }[] = []
    const previewReadiness = vi.fn(
      (request: CouncilReadinessRequest) => new Promise<CouncilReadiness>((resolve) => calls.push({ request, resolve })),
    )
    setup({ openai: 'met', gemini: 'met' }, { previewReadiness })
    await ask()
    await openModels()
    await openOverrides()
    await userEvent.type(overrideInput('Gemini'), 'x')
    await waitFor(() => expect(calls.at(-1)?.request.participant_model_overrides).toEqual({ gemini: 'x' }))
    const stale = calls.at(-1)!

    await userEvent.type(overrideInput('Gemini'), 'y') // agora "xy"
    await waitFor(() => expect(calls.at(-1)?.request.participant_model_overrides).toEqual({ gemini: 'xy' }))
    const current = calls.at(-1)!

    // a resposta antiga ("x") chega depois e diria que falta configuração
    await act(async () => stale.resolve(readinessFor(stale.request, { gemini: 'missing' })))
    expect(screen.queryByRole('region', { name: /Falta configuração local/ })).not.toBeInTheDocument()

    await act(async () => current.resolve(readinessFor(current.request, {})))
    expect(screen.queryByRole('region', { name: /Falta configuração local/ })).not.toBeInTheDocument()
    expect(submitButton()).toBeEnabled()
  })

  it('o reconhecimento cai ao trocar o modelo e não revive ao voltar ao mesmo modelo', async () => {
    const { onSubmit } = setup({ openai: 'met', gemini: 'met' }, { internalStates: { anthropic: 'missing' } })
    await ask()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    expect(submitButton()).toBeEnabled()

    await openModels()
    await openOverrides()
    await userEvent.type(overrideInput('GPT'), 'g')
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Perguntar mesmo assim/ })).not.toBeChecked())
    await userEvent.clear(overrideInput('GPT')) // volta exatamente à entrada reconhecida antes

    const checkbox = await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ })
    expect(checkbox).not.toBeChecked()
    expect(submitButton()).toBeDisabled()
    await userEvent.click(checkbox)
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls.at(-1)?.[4]).toMatchObject({ acknowledge_known_degradation: true })
  })

  it('reuso: só escolhas explícitas de participantes ainda selecionáveis', async () => {
    const { onSubmit, preview } = setup(
      { openai: 'met', gemini: 'met' },
      {
        initialInput: {
          question: 'Pergunta anterior',
          sourceText: null,
          // "mistral" não existe mais nesta instalação
          enabledProviders: ['openai', 'gemini', 'mistral'],
          participantModelOverrides: { gemini: 'gemini-explicit', mistral: 'mistral-explicit' },
        },
      },
    )

    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith({
        enabled_providers: ['openai', 'gemini'],
        source_supplied: false,
        participant_model_overrides: { gemini: 'gemini-explicit' },
      }),
    )
    await waitFor(() => expect(submitButton()).toBeEnabled())
    await userEvent.click(submitButton())

    // "mistral" não volta à seleção, e a escolha dele não viaja
    expect(onSubmit).toHaveBeenCalledWith('Pergunta anterior', ['openai', 'gemini'], null, undefined, STRICT, {
      gemini: 'gemini-explicit',
    })
  })

  it('resposta direta: sem controle de modelo e sem escolha no envio', async () => {
    const { onSubmit } = setup({ openai: 'met', gemini: 'met' })
    await userEvent.click(screen.getByRole('radio', { name: 'Resposta direta' }))
    await ask()
    await openModels()

    expect(screen.queryByRole('button', { name: /Modelo de cada participante/ })).not.toBeInTheDocument()
    await userEvent.click(submitButton())
    expect(onSubmit.mock.calls[0]).toEqual(['Qual a capital?', ['openai'], null, 'direct'])
  })
})
