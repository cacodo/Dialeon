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

function readiness(internalState: 'met' | 'missing'): CouncilReadiness {
  const internal = (role: CouncilReadiness['dependencies'][number]['role'], applicability: 'potential' | 'not_applicable') => ({
    role,
    provider: 'anthropic',
    configured_default_model: 'claude-configured',
    local_prerequisite: internalState,
    applicability,
  })
  return {
    contract_version: 'council_local_readiness_v1',
    summary: internalState === 'missing' ? 'some_missing' : 'all_met',
    strict_admission: internalState === 'missing' ? 'blocked' : 'admissible',
    dependencies: [
      { role: 'participant', provider: 'openai', configured_default_model: 'gpt-configured', local_prerequisite: 'met', applicability: 'selected' },
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
    })
  })
})
