// Participant Perspectives Document Disclosure (UI Slice) -- cobertura
// product-facing deste componente. Provenance de identidade de modelo
// (requested/effective/model_identity_source) migrou inteiramente pra
// Auditoria técnica (ver InspectionPanel.test.tsx, seção "Respostas dos
// participantes — registros técnicos") -- não é mais exibida aqui, então
// a suíte antiga que testava isso via "Detalhes técnicos" foi substituída
// por esta, focada no que o componente de fato apresenta agora.

import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
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

describe('ParticipantsResponses — disclosure de perspectiva', () => {
  it('perspectiva bem-sucedida começa colapsada e é expansível', async () => {
    render(
      <ParticipantsResponses responses={[makeResponse({ provider: 'openai' })]} round="initial" />,
    )

    const toggle = screen.getByRole('button', { name: /perspectiva.*gpt/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('resposta')).not.toBeInTheDocument()

    await userEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('resposta')).toBeInTheDocument()
  })

  it('preserva texto/quebras de linha exatos ao expandir (sem re-flow/normalização)', async () => {
    const text = 'Primeira linha.\n\nSegunda linha com   espaços múltiplos.\nTerceira linha.'
    const { container } = render(
      <ParticipantsResponses responses={[makeResponse({ response_text: text })]} round="initial" />,
    )

    await userEvent.click(screen.getByRole('button', { name: /perspectiva/i }))

    const textElement = container.querySelector('.participant-perspective__text')
    expect(textElement).not.toBeNull()
    expect(textElement!.textContent).toBe(text)
  })

  it.each([
    ['tag <script>', '<script>alert(document.cookie)</script>'],
    ['tag <img> com onerror', '<img src=x onerror="alert(1)">'],
    ['URI javascript:', '<a href="javascript:alert(1)">clique aqui</a>'],
    ['sintaxe Markdown', '[clique aqui](javascript:alert(1)) e **negrito** # título'],
    ['URL crua', 'Veja https://exemplo.com/pagina?x=1 pra mais detalhes.'],
  ])('excerpt/response_text hostil (%s) aparece só como texto -- nenhuma estrutura é interpretada', async (_label, hostileText) => {
    const { container } = render(
      <ParticipantsResponses responses={[makeResponse({ response_text: hostileText })]} round="initial" />,
    )

    await userEvent.click(screen.getByRole('button', { name: /perspectiva/i }))

    expect(container.textContent).toContain(hostileText)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('a')).toBeNull()
    expect(container.querySelectorAll('[onerror]')).toHaveLength(0)
    expect(container.querySelector('[href^="javascript:"]')).toBeNull()
    // Nenhum <strong>/<h1>/<li> nasceu de "**negrito**"/"# título"/listas.
    expect(container.querySelector('strong')).toBeNull()
    expect(container.querySelector('h1')).toBeNull()
    expect(container.querySelector('li:not(.participant-perspective)')).toBeNull()
  })

  it('provider visível na perspectiva, mas modelo solicitado/efetivo NUNCA aparece no heading product-facing', () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({
            provider: 'openai',
            requested_model: 'gpt-5.5',
            model: 'gpt-5.5-2026-01-15',
          }),
        ]}
        round="initial"
      />,
    )

    const toggle = screen.getByRole('button', { name: /perspectiva.*gpt/i })
    expect(toggle).toBeInTheDocument()
    expect(screen.queryByText('gpt-5.5-2026-01-15')).not.toBeInTheDocument()
    expect(screen.queryByText('gpt-5.5')).not.toBeInTheDocument()
  })

  it('rodada inicial e rodada de crítica têm headings distintos, sem implicar resposta direta entre elas', () => {
    render(<ParticipantsResponses responses={[makeResponse({})]} round="initial" />)
    expect(screen.getByRole('heading', { name: 'Perspectivas iniciais' })).toBeInTheDocument()
    expect(screen.queryByText(/resposta a|em resposta|réplica/i)).not.toBeInTheDocument()
  })

  it('rodada de crítica usa seu próprio heading, distinto do da rodada inicial', () => {
    render(<ParticipantsResponses responses={[makeResponse({})]} round="critique" />)
    expect(screen.getByRole('heading', { name: 'Revisões após o debate' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Perspectivas iniciais' })).not.toBeInTheDocument()
  })
})

describe('ParticipantsResponses — falha/ausência (histórica, nunca desacordo)', () => {
  it('resposta falhada usa wording neutro de ausência, nunca role="alert", nunca linguagem de desacordo', async () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({
            status: 'error',
            response_text: null,
            error: { type: 'timeout', message: 'raw transport error xyz', retryable: true },
          }),
        ]}
        round="initial"
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /perspectiva/i }))

    expect(
      screen.getByText(/este participante não produziu uma perspectiva nesta rodada/i),
    ).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    // Categoria limitada (tempo limite excedido), nunca a mensagem bruta.
    expect(screen.getByText(/tempo limite excedido/i)).toBeInTheDocument()
    expect(screen.queryByText(/raw transport error xyz/i)).not.toBeInTheDocument()
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toMatch(/discord|desacordo|rejeitad|opinião/i)
  })

  it('falha sem error registrado (histórico) ainda usa a mensagem neutra, sem categoria inventada', async () => {
    render(
      <ParticipantsResponses
        responses={[makeResponse({ status: 'error', response_text: null, error: null })]}
        round="initial"
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /perspectiva/i }))

    expect(
      screen.getByText(/este participante não produziu uma perspectiva nesta rodada\./i),
    ).toBeInTheDocument()
  })
})

describe('ParticipantsResponses — acessibilidade: múltiplos disclosures com IDs/nomes distinguíveis', () => {
  it('cada resposta tem aria-controls único, e cada painel expandido tem exatamente esse id', async () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({ id: 'mr-1', provider: 'openai' }),
          makeResponse({ id: 'mr-2', provider: 'anthropic' }),
        ]}
        round="initial"
      />,
    )

    const gptToggle = screen.getByRole('button', { name: /gpt/i })
    const claudeToggle = screen.getByRole('button', { name: /claude/i })
    const openaiControlsId = gptToggle.getAttribute('aria-controls')
    const anthropicControlsId = claudeToggle.getAttribute('aria-controls')

    expect(openaiControlsId).toBeTruthy()
    expect(anthropicControlsId).toBeTruthy()
    expect(openaiControlsId).not.toBe(anthropicControlsId)

    await userEvent.click(gptToggle)
    await userEvent.click(claudeToggle)

    expect(document.getElementById(openaiControlsId!)).toBeInTheDocument()
    expect(document.getElementById(anthropicControlsId!)).toBeInTheDocument()
  })

  it('nomes acessíveis distinguem cada participante pelo provider', () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({ id: 'mr-1', provider: 'openai' }),
          makeResponse({ id: 'mr-2', provider: 'anthropic' }),
        ]}
        round="initial"
      />,
    )

    expect(screen.getByRole('button', { name: 'Perspectiva — GPT' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perspectiva — Claude' })).toBeInTheDocument()
  })

  it('mesmo provider duplicado na mesma rodada (cenário anômalo): nomes acessíveis continuam distinguíveis', () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({ id: 'mr-1', provider: 'openai' }),
          makeResponse({ id: 'mr-2', provider: 'openai' }),
        ]}
        round="initial"
      />,
    )

    expect(screen.getByRole('button', { name: 'Perspectiva — GPT (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Perspectiva — GPT (2)' })).toBeInTheDocument()
  })

  it('IDs de heading/seção nunca são derivados de texto humano com espaço', () => {
    const { container } = render(
      <ParticipantsResponses responses={[makeResponse({})]} round="initial" />,
    )

    const heading = screen.getByRole('heading', { name: 'Perspectivas iniciais' })
    expect(heading.id).not.toMatch(/\s/)
    const section = within(container).getByRole('region')
    expect(section).toHaveAttribute('aria-labelledby', heading.id)
  })
})

describe('ParticipantsResponses — repair pós-revisão adversarial: identidade de apresentação nunca depende de response.id', () => {
  it('duas respostas com o MESMO response.id na mesma rodada continuam duas disclosures independentemente operáveis', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ParticipantsResponses
        responses={[
          makeResponse({ id: 'mr-duplicado', provider: 'openai', response_text: 'Primeira.' }),
          makeResponse({ id: 'mr-duplicado', provider: 'anthropic', response_text: 'Segunda.' }),
        ]}
        round="initial"
      />,
    )

    const keyWarning = consoleError.mock.calls.some((call) => String(call[0]).toLowerCase().includes('key'))
    expect(keyWarning).toBe(false)
    consoleError.mockRestore()

    const gptToggle = screen.getByRole('button', { name: /gpt/i })
    const claudeToggle = screen.getByRole('button', { name: /claude/i })

    await userEvent.click(gptToggle)
    expect(screen.getByText('Primeira.')).toBeInTheDocument()
    expect(screen.queryByText('Segunda.')).not.toBeInTheDocument()

    await userEvent.click(claudeToggle)
    expect(screen.getByText('Segunda.')).toBeInTheDocument()

    await userEvent.click(gptToggle)
    expect(screen.queryByText('Primeira.')).not.toBeInTheDocument()
    // A segunda continua aberta, independente da primeira ter colapsado.
    expect(screen.getByText('Segunda.')).toBeInTheDocument()
  })

  it('duas respostas com o mesmo response.id têm aria-controls e IDs de painel ÚNICOS', async () => {
    render(
      <ParticipantsResponses
        responses={[
          makeResponse({ id: 'mr-duplicado', provider: 'openai' }),
          makeResponse({ id: 'mr-duplicado', provider: 'anthropic' }),
        ]}
        round="initial"
      />,
    )

    const gptToggle = screen.getByRole('button', { name: /gpt/i })
    const claudeToggle = screen.getByRole('button', { name: /claude/i })

    const openaiControlsId = gptToggle.getAttribute('aria-controls')
    const anthropicControlsId = claudeToggle.getAttribute('aria-controls')

    expect(openaiControlsId).toBeTruthy()
    expect(anthropicControlsId).toBeTruthy()
    expect(openaiControlsId).not.toBe(anthropicControlsId)

    await userEvent.click(gptToggle)
    await userEvent.click(claudeToggle)

    expect(document.getElementById(openaiControlsId!)).toBeInTheDocument()
    expect(document.getElementById(anthropicControlsId!)).toBeInTheDocument()
  })

  it('response.id contendo espaço/caracteres não-DOM-safe nunca entra na identidade do DOM', async () => {
    render(
      <ParticipantsResponses
        responses={[makeResponse({ id: 'mr with spaces #weird!', provider: 'openai' })]}
        round="initial"
      />,
    )

    const toggle = screen.getByRole('button', { name: /gpt/i })
    const controlsId = toggle.getAttribute('aria-controls')

    expect(controlsId).toBeTruthy()
    expect(controlsId).not.toMatch(/\s/)
    expect(controlsId).not.toContain('mr with spaces')

    await userEvent.click(toggle)
    expect(document.getElementById(controlsId!)).toBeInTheDocument()
  })
})
