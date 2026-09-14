import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient, ApiError } from '../client'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('apiClient', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('getProviders retorna a lista de providers', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ providers: ['openai', 'anthropic'] }))

    const result = await apiClient.getProviders()

    expect(result.providers).toEqual(['openai', 'anthropic'])
    expect(fetch).toHaveBeenCalledWith('/providers', expect.any(Object))
  })

  it('createRun completed retorna CompletedRunResponse', async () => {
    const body = {
      status: 'completed',
      id: 'run-1',
      started_at: '2026-09-06T00:00:00Z',
      completed_at: '2026-09-06T00:00:05Z',
      final_answer: {
        answer_text: 'Brasília é a capital.',
        limitations: [],
        status: 'llm_composed',
        editor_model: 'claude-sonnet-5',
        editor_model_identity_source: 'provider_reported',
        judge_confidence: 0.9,
      },
      accounting: {
        total_input_tokens: 100,
        total_output_tokens: 20,
        estimated_cost_usd: 0.01,
        has_unknown_accounting_components: false,
      },
      config: {
        question: 'Qual a capital do Brasil?',
        enabled_providers: ['openai'],
        claim_processor_provider: 'anthropic',
        judge_provider: 'anthropic',
        editor_provider: 'anthropic',
        max_cost_usd: 1,
        max_total_tokens: 1000,
        max_output_tokens_per_call: 100,
        round_dispatch_timeout_seconds: 30,
        quorum: { min_for_debate: 1, min_to_return: 1 },
      },
    }
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(body, 201))

    const result = await apiClient.createRun({
      question: 'Qual a capital do Brasil?',
      enabled_providers: ['openai'],
      source_text: null,
    })

    expect(result.status).toBe('completed')
    expect(fetch).toHaveBeenCalledWith(
      '/runs',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('createRun com 409 lança ApiError contendo o run_id persistido', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: 'insufficient_quorum',
            message: 'Quórum insuficiente.',
            details: { run_id: 'failed-run-1', successful_count: 1, total_providers: 3, min_to_return: 2 },
          },
        },
        409,
      ),
    )

    await expect(
      apiClient.createRun({ question: 'x', enabled_providers: ['openai', 'anthropic', 'gemini'], source_text: null }),
    ).rejects.toMatchObject({
      status: 409,
      code: 'insufficient_quorum',
      details: { run_id: 'failed-run-1' },
    })
  })

  it('createRun com 422 lança ApiError com code invalid_request', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        { error: { code: 'invalid_request', message: 'Request inválido.', details: { errors: [] } } },
        422,
      ),
    )

    await expect(apiClient.createRun({ question: '', enabled_providers: [], source_text: null })).rejects.toBeInstanceOf(
      ApiError,
    )
  })

  it('createRun com 500 lança ApiError genérica sem detalhe interno', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ error: { code: 'internal_error', message: 'Erro interno inesperado.', details: null } }, 500),
    )

    await expect(apiClient.createRun({ question: 'x', enabled_providers: ['openai'], source_text: null })).rejects.toMatchObject(
      { status: 500, code: 'internal_error' },
    )
  })

  it('listRuns envia limit/offset como query params', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ runs: [], limit: 10, offset: 5 }))

    await apiClient.listRuns(10, 5)

    expect(fetch).toHaveBeenCalledWith('/runs?limit=10&offset=5', expect.any(Object))
  })

  it('getRun busca por id', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        status: 'insufficient_quorum',
        id: 'r1',
        started_at: 'x',
        failed_at: 'y',
        successful_count: 1,
        total_providers: 2,
        min_to_return: 2,
        accounting: {
          total_input_tokens: 0,
          total_output_tokens: 0,
          estimated_cost_usd: 0,
          has_unknown_accounting_components: false,
        },
        config: {
          question: 'q',
          enabled_providers: ['openai'],
          claim_processor_provider: 'a',
          judge_provider: 'a',
          editor_provider: 'a',
          max_cost_usd: 1,
          max_total_tokens: 1,
          max_output_tokens_per_call: 1,
          round_dispatch_timeout_seconds: 1,
          quorum: { min_for_debate: 1, min_to_return: 1 },
        },
      }),
    )

    const result = await apiClient.getRun('r1')

    expect(result.status).toBe('insufficient_quorum')
    expect(fetch).toHaveBeenCalledWith('/runs/r1', expect.any(Object))
  })

  it('getRunAudit busca o audit por id', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ status: 'completed' }))

    await apiClient.getRunAudit('r1')

    expect(fetch).toHaveBeenCalledWith('/runs/r1/audit', expect.any(Object))
  })

  it('lança ApiError genérica quando o corpo de erro não é JSON', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response('<html>502</html>', { status: 502 }),
    )

    await expect(apiClient.getProviders()).rejects.toMatchObject({ status: 502, code: 'unknown' })
  })
})
