// Council Local Execution Readiness & Admission V1 -- a Home liga a prévia de
// prontidão ao composer e trata a recusa de admissão estrita: nada foi
// enviado aos modelos, o aviso mostra a avaliação do aceite e seguir mesmo
// assim é uma escolha deliberada (envio padrão com reconhecimento).

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { Home } from '../Home'
import { apiClient, ApiError } from '../../api/client'
import type { CouncilReadiness } from '../../api/types'

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...actual,
    apiClient: {
      getProviders: vi.fn(),
      previewCouncilReadiness: vi.fn(),
      createRun: vi.fn(),
      listRuns: vi.fn(),
      getRun: vi.fn(),
      getRunAudit: vi.fn(),
    },
  }
})

const FINGERPRINT_ANTHROPIC = `sha256:${'a'.repeat(64)}`
const FINGERPRINT_OPENAI = `sha256:${'b'.repeat(64)}`

function readiness(
  internalState: 'met' | 'missing' | 'unknown',
  participantState: 'met' | 'missing' = 'met',
): CouncilReadiness {
  const internal = (role: CouncilReadiness['dependencies'][number]['role'], applicability: 'potential' | 'not_applicable') => ({
    role,
    provider: 'anthropic',
    configured_default_model: 'claude-configured',
    local_prerequisite: internalState,
    applicability,
  })
  // Forma ATUAL do servidor (v2): os participantes carregam o modelo planejado.
  return {
    contract_version: 'council_local_readiness_v2',
    summary:
      internalState === 'missing' || participantState === 'missing'
        ? 'some_missing'
        : internalState === 'unknown'
          ? 'some_unknown'
          : 'all_met',
    strict_admission: internalState === 'missing' || participantState === 'missing' ? 'blocked' : 'admissible',
    // identidade derivada pelo "servidor" (aqui, fixa por situação)
    known_degradation_fingerprint:
      internalState === 'missing' ? FINGERPRINT_ANTHROPIC : participantState === 'missing' ? FINGERPRINT_OPENAI : null,
    dependencies: [
      {
        role: 'participant',
        provider: 'openai',
        configured_default_model: 'gpt-configured',
        local_prerequisite: participantState,
        applicability: 'selected',
        planned_model: 'gpt-configured',
        planned_model_origin: 'configured_default',
      },
      internal('claim_extraction', 'potential'),
      internal('source_analysis', 'not_applicable'),
      internal('judge', 'potential'),
      internal('editor', 'potential'),
      internal('semantic_review', 'potential'),
    ],
  }
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/runs/:runId" element={<p>Página da pergunta</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const ask = () => userEvent.type(screen.getByLabelText(/faça uma pergunta/i), 'Qual a capital?')
const submit = () => userEvent.click(screen.getByRole('button', { name: 'Perguntar' }))

beforeEach(() => {
  vi.mocked(apiClient.getProviders).mockReset()
  vi.mocked(apiClient.previewCouncilReadiness).mockReset()
  vi.mocked(apiClient.createRun).mockReset()
  vi.mocked(apiClient.getProviders).mockResolvedValue({
    providers: ['openai', 'anthropic'],
    local_prerequisites: { openai: 'met', anthropic: 'missing' },
  })
})

describe('Home -- prontidão local do Conselho', () => {
  it('degradação conhecida na prévia: só envia depois da escolha deliberada, com reconhecimento', async () => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('missing'))
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    expect(await screen.findByRole('region', { name: 'Falta configuração local para parte do Conselho' })).toBeInTheDocument()
    expect(apiClient.previewCouncilReadiness).toHaveBeenCalledWith({
      enabled_providers: ['openai'],
      source_supplied: false,
    })
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()

    await userEvent.click(screen.getByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()

    expect(apiClient.createRun).toHaveBeenCalledWith({
      question: 'Qual a capital?',
      enabled_providers: ['openai'],
      source_text: null,
      readiness_admission: 'standard',
      acknowledge_known_degradation: true,
      acknowledged_degradation_fingerprint: FINGERPRINT_ANTHROPIC,
    })
  })

  it('recusa da admissão estrita: nada foi enviado, o aviso aparece e seguir é deliberado', async () => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('met'))
    vi.mocked(apiClient.createRun)
      .mockRejectedValueOnce(
        new ApiError(422, 'council_prerequisites_missing', 'recusada', { readiness: readiness('missing') }),
      )
      .mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await submit()

    expect(apiClient.createRun).toHaveBeenLastCalledWith({
      question: 'Qual a capital?',
      enabled_providers: ['openai'],
      source_text: null,
      readiness_admission: 'strict',
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Nada foi enviado aos modelos.')
    expect(screen.getByRole('region', { name: 'Falta configuração local para parte do Conselho' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()

    await userEvent.click(screen.getByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()
    expect(apiClient.createRun).toHaveBeenLastCalledWith({
      question: 'Qual a capital?',
      enabled_providers: ['openai'],
      source_text: null,
      readiness_admission: 'standard',
      acknowledge_known_degradation: true,
      acknowledged_degradation_fingerprint: FINGERPRINT_ANTHROPIC,
    })
  })

  it('degradação mudou entre o aviso e o envio (409): nada criado, aviso novo, escolha de novo', async () => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('missing'))
    vi.mocked(apiClient.createRun)
      .mockRejectedValueOnce(
        new ApiError(409, 'council_readiness_changed', 'mudou', {
          readiness: readiness('met', 'missing'),
          acknowledged_degradation_fingerprint: FINGERPRINT_ANTHROPIC,
        }),
      )
      .mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A configuração local das etapas do Conselho mudou desde o aviso. Nada foi enviado aos modelos. ' +
        'Veja o aviso acima para decidir se quer perguntar mesmo assim.',
    )
    const notice = screen.getByRole('region', { name: 'Falta configuração local para parte do Conselho' })
    expect(notice).toHaveTextContent('Participante: GPT')
    expect(screen.getByRole('checkbox', { name: /Perguntar mesmo assim/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Perguntar' })).toBeDisabled()

    await userEvent.click(screen.getByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()
    expect(apiClient.createRun).toHaveBeenLastCalledWith({
      question: 'Qual a capital?',
      enabled_providers: ['openai'],
      source_text: null,
      readiness_admission: 'standard',
      acknowledge_known_degradation: true,
      acknowledged_degradation_fingerprint: FINGERPRINT_OPENAI,
    })
  })

  it.each([
    ['tudo presente', readiness('met')],
    ['só não verificável', readiness('unknown')],
  ])('409 com a degradação sumida (%s): sem aviso e sem pedir reconhecimento', async (_, fresh) => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('missing'))
    vi.mocked(apiClient.createRun)
      .mockRejectedValueOnce(
        new ApiError(409, 'council_readiness_changed', 'mudou', {
          readiness: fresh,
          acknowledged_degradation_fingerprint: FINGERPRINT_ANTHROPIC,
        }),
      )
      .mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'Agora não há configuração local ausente conhecida nas etapas do Conselho: você pode perguntar de novo.',
    )
    expect(alert).not.toHaveTextContent(/aviso acima|mesmo assim/)
    expect(screen.queryByRole('region', { name: /Falta configuração local/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /Perguntar mesmo assim/ })).not.toBeInTheDocument()

    // perguntar de novo segue estrito, sem reconhecimento
    await submit()
    expect(apiClient.createRun).toHaveBeenLastCalledWith({
      question: 'Qual a capital?',
      enabled_providers: ['openai'],
      source_text: null,
      readiness_admission: 'strict',
    })
  })

  it('409 sem avaliação reconhecível: nada é afirmado sobre a configuração', async () => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('missing'))
    vi.mocked(apiClient.createRun).mockRejectedValueOnce(
      new ApiError(409, 'council_readiness_changed', 'mudou', { readiness: { unexpected: true } }),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Você pode perguntar de novo; o servidor avalia a configuração local outra vez.')
    expect(alert).not.toHaveTextContent(/não há configuração local ausente|aviso acima/)
  })

  it('409 com resumo fora do contrato: texto neutro, sem afirmar ausência nem pedir reconhecimento', async () => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('missing'))
    vi.mocked(apiClient.createRun).mockRejectedValueOnce(
      new ApiError(409, 'council_readiness_changed', 'mudou', {
        readiness: { ...readiness('met'), summary: 'mostly_fine' },
      }),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await userEvent.click(await screen.findByRole('checkbox', { name: /Perguntar mesmo assim/ }))
    await submit()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Você pode perguntar de novo; o servidor avalia a configuração local outra vez.')
    expect(alert).not.toHaveTextContent(/não há configuração local ausente|aviso acima|mesmo assim/)
  })

  it('escolha de modelo de participante vai no envio do Conselho (mesmo mapa da API)', async () => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('met'))
    vi.mocked(apiClient.createRun).mockImplementation(() => new Promise(() => {}))
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await userEvent.click(screen.getByRole('button', { name: 'Modelos: GPT' }))
    await userEvent.click(screen.getByRole('button', { name: 'Modelo de cada participante (avançado)' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Modelo para GPT' }), 'gpt-explicit')
    await submit()

    expect(apiClient.createRun).toHaveBeenLastCalledWith({
      question: 'Qual a capital?',
      enabled_providers: ['openai'],
      source_text: null,
      readiness_admission: 'strict',
      participant_model_overrides: { openai: 'gpt-explicit' },
    })
  })

  it.each([
    // v1 só existe em registros históricos de aceite; uma recusa nova sempre traz v2
    ['avaliação v1 numa recusa', { ...readiness('met', 'missing'), contract_version: 'council_local_readiness_v1' }],
    ['identidade de degradação malformada', { ...readiness('met', 'missing'), known_degradation_fingerprint: 'sha256:xyz' }],
  ])('recusa com %s: não adotada, texto neutro, nada afirmado', async (_, details) => {
    vi.mocked(apiClient.previewCouncilReadiness).mockResolvedValue(readiness('met'))
    vi.mocked(apiClient.createRun).mockRejectedValueOnce(
      new ApiError(422, 'council_prerequisites_missing', 'recusada', { readiness: details }),
    )
    renderHome()

    await screen.findByRole('button', { name: 'Modelos: GPT' })
    await ask()
    await submit()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Você pode perguntar de novo; o servidor avalia a configuração local outra vez.')
    expect(alert).not.toHaveTextContent(/aviso acima|não há configuração local ausente/)
    // a avaliação não reconhecida nunca vira aviso/reconhecimento
    expect(screen.queryByRole('region', { name: /Falta configuração local/ })).not.toBeInTheDocument()
  })
})
