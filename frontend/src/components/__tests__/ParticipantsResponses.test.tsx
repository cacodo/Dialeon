import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ParticipantsResponses } from '../ParticipantsResponses'
import type { ModelResponsePublic } from '../../api/types'

function makeResponse(overrides: Partial<ModelResponsePublic> = {}): ModelResponsePublic {
  return {
    id: 'mr-1',
    provider: 'openai',
    requested_model: 'gpt-5.5',
    model: 'gpt-5.5',
    model_identity_source: 'provider_reported',
    round_number: 1,
    status: 'success',
    response_text: 'resposta',
    usage: { input_tokens: 10, output_tokens: 5 },
    cost_usd: 0.001,
    pricing_provenance: null,
    latency_ms: 100,
    attempts: 1,
    error: null,
    had_uncertain_prior_attempts: false,
    provider_finish_reason: null,
    request_provenance: null,
    created_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

async function expandTechnicalDetails() {
  await userEvent.click(screen.getByRole('button', { name: /detalhes técnicos/i }))
}

describe('ParticipantsResponses — provenance de identidade do modelo', () => {
  it('mostra "reportado pelo provider" quando model_identity_source é provider_reported', async () => {
    render(
      <ParticipantsResponses
        responses={[makeResponse({ model_identity_source: 'provider_reported' })]}
        title="Rodada inicial"
      />,
    )
    await expandTechnicalDetails()

    expect(screen.getByText(/reportado pelo provider/i)).toBeInTheDocument()
  })

  it('mostra "fallback do modelo solicitado" quando model_identity_source é requested_fallback', async () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({
            requested_model: 'gpt-5.5',
            model: 'gpt-5.5',
            model_identity_source: 'requested_fallback',
          }),
        ]}
        title="Rodada inicial"
      />,
    )
    await expandTechnicalDetails()

    expect(screen.getByText(/fallback do modelo solicitado/i)).toBeInTheDocument()
  })

  it('histórico (null) renderiza honestamente "não registrada", nunca como fallback', async () => {
    render(
      <ParticipantsResponses
        responses={[makeResponse({ model_identity_source: null })]}
        title="Rodada inicial"
      />,
    )
    await expandTechnicalDetails()

    expect(screen.getByText(/não registrada/i)).toBeInTheDocument()
    expect(screen.queryByText(/fallback do modelo solicitado/i)).not.toBeInTheDocument()
  })

  it('divergência requested/reported -- os dois identificadores continuam inspecionáveis', async () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({
            requested_model: 'gpt-5.5',
            model: 'gpt-5.5-2026-01-15',
            model_identity_source: 'provider_reported',
          }),
        ]}
        title="Rodada inicial"
      />,
    )
    await expandTechnicalDetails()

    expect(screen.getByText('gpt-5.5')).toBeInTheDocument()
    expect(screen.getByText('gpt-5.5-2026-01-15')).toBeInTheDocument()
    expect(screen.getByText(/reportado pelo provider/i)).toBeInTheDocument()
  })
})
