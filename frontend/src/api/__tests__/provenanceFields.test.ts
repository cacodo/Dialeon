import { describe, expect, it } from 'vitest'
import type {
  ClaimProcessingAttemptPublic,
  DeterministicVerificationAttemptPublic,
  EditorAttemptPublic,
  JudgeAttemptPublic,
  ModelResponsePublic,
  PricingProvenance,
  RejectedSourceEntryPublic,
  RequestProvenance,
  SourceAnalysisAttemptPublic,
} from '../types'

// Pre-release contract sync -- fixa em tipo E em runtime os campos de
// provenance/audit que o backend já retorna (app/presentation/schemas.py)
// mas que este arquivo deixava de representar: RequestProvenance,
// had_uncertain_prior_attempts, provider_finish_reason,
// PricingProvenance.canonical_model_id, os campos completos de
// SourceAnalysisAttemptPublic, e DeterministicVerificationAttemptPublic
// (numeric_verification_attempts). Objetivo: se um destes campos for
// removido do tipo, a atribuição abaixo falha em tempo de compilação
// (`tsc -b`), não só em runtime.

describe('RequestProvenance', () => {
  it('contract_version + request_digest, referenciável por *AttemptPublic/ModelResponsePublic', () => {
    const provenance: RequestProvenance = {
      contract_version: 'judge_v1',
      request_digest: 'completion-request-sha256-v1:' + 'a'.repeat(64),
    }
    expect(provenance.contract_version).toBe('judge_v1')

    const modelResponse: ModelResponsePublic = {
      id: 'mr-1',
      provider: 'anthropic',
      requested_model: 'claude-sonnet-5',
      model: 'claude-sonnet-5',
      model_identity_source: 'provider_reported',
      round_number: 1,
      status: 'success',
      response_text: 'ok',
      usage: { input_tokens: 10, output_tokens: 5 },
      cost_usd: 0.01,
      pricing_provenance: null,
      latency_ms: 100,
      attempts: 1,
      error: null,
      had_uncertain_prior_attempts: false,
      provider_finish_reason: 'end_turn',
      request_provenance: provenance,
      created_at: '2026-09-16T00:00:00Z',
    }
    expect(modelResponse.request_provenance).toBe(provenance)
    expect(modelResponse.had_uncertain_prior_attempts).toBe(false)
    expect(modelResponse.provider_finish_reason).toBe('end_turn')
  })

  it('null significa "não registrada pra este registro histórico", nunca contrato vazio', () => {
    const modelResponse: ModelResponsePublic = {
      id: 'mr-2',
      provider: 'openai',
      requested_model: 'gpt-5.5',
      model: 'gpt-5.5',
      model_identity_source: null,
      round_number: 1,
      status: 'success',
      response_text: 'ok',
      usage: null,
      cost_usd: null,
      pricing_provenance: null,
      latency_ms: 50,
      attempts: 1,
      error: null,
      had_uncertain_prior_attempts: false,
      provider_finish_reason: null,
      request_provenance: null,
      created_at: '2026-09-16T00:00:00Z',
    }
    expect(modelResponse.request_provenance).toBeNull()
  })
})

describe('had_uncertain_prior_attempts / provider_finish_reason nos *AttemptPublic', () => {
  it('ClaimProcessingAttemptPublic carrega os dois campos', () => {
    const attempt: ClaimProcessingAttemptPublic = {
      id: 'cpa-1',
      operation: 'extract',
      round_number: 1,
      attempt_number: 1,
      provider: 'anthropic',
      requested_model: 'claude-sonnet-5',
      model: 'claude-sonnet-5',
      model_identity_source: 'provider_reported',
      target_model_response_id: 'mr-1',
      target_claim_ids: [],
      transport_status: 'success',
      transport_error: null,
      raw_output_text: '{}',
      parse_status: 'accepted',
      parse_error_message: null,
      usage: null,
      cost_usd: null,
      pricing_provenance: null,
      latency_ms: 10,
      had_uncertain_prior_attempts: true,
      provider_finish_reason: 'stop',
      request_provenance: null,
      created_at: '2026-09-16T00:00:00Z',
    }
    expect(attempt.had_uncertain_prior_attempts).toBe(true)
    expect(attempt.provider_finish_reason).toBe('stop')
  })

  it('JudgeAttemptPublic e EditorAttemptPublic carregam os mesmos dois campos', () => {
    const judgeAttempt: JudgeAttemptPublic = {
      id: 'ja-1',
      attempt_number: 1,
      provider: 'anthropic',
      requested_model: 'claude-sonnet-5',
      model: 'claude-sonnet-5',
      model_identity_source: 'provider_reported',
      transport_status: 'success',
      transport_error: null,
      raw_output_text: '{}',
      parse_status: 'accepted',
      parse_error_message: null,
      usage: null,
      cost_usd: null,
      pricing_provenance: null,
      latency_ms: 10,
      had_uncertain_prior_attempts: false,
      provider_finish_reason: 'end_turn',
      request_provenance: null,
      created_at: '2026-09-16T00:00:00Z',
    }
    const editorAttempt: EditorAttemptPublic = { ...judgeAttempt, id: 'ea-1' }

    expect(judgeAttempt.provider_finish_reason).toBe('end_turn')
    expect(editorAttempt.provider_finish_reason).toBe('end_turn')
  })
})

describe('PricingProvenance.canonical_model_id', () => {
  it('null quando a chave (provider, model) bateu diretamente na tabela', () => {
    const pricing: PricingProvenance = {
      source_id: 'default-2026-09',
      tier: 'standard',
      input_rate_usd_per_million_tokens: 3,
      output_rate_usd_per_million_tokens: 15,
      canonical_model_id: null,
    }
    expect(pricing.canonical_model_id).toBeNull()
  })

  it('preenchido quando a taxa veio de um alias de snapshot', () => {
    const pricing: PricingProvenance = {
      source_id: 'default-2026-09',
      tier: 'standard',
      input_rate_usd_per_million_tokens: 3,
      output_rate_usd_per_million_tokens: 15,
      canonical_model_id: 'gpt-5.5',
    }
    expect(pricing.canonical_model_id).toBe('gpt-5.5')
  })
})

describe('SourceAnalysisAttemptPublic -- paridade completa com o backend', () => {
  it('inclui transport_error/raw_output_text/usage/cost_usd/pricing_provenance/provenance de request', () => {
    const attempt: SourceAnalysisAttemptPublic = {
      id: 'saa-1',
      attempt_number: 1,
      provider: 'anthropic',
      requested_model: 'claude-sonnet-5',
      model: 'claude-sonnet-5',
      model_identity_source: 'provider_reported',
      transport_status: 'error',
      transport_error: { type: 'timeout', message: 'deadline exceeded', retryable: true },
      raw_output_text: null,
      parse_status: 'not_attempted',
      parse_error_message: null,
      usage: null,
      cost_usd: null,
      pricing_provenance: null,
      latency_ms: 5000,
      had_uncertain_prior_attempts: true,
      provider_finish_reason: null,
      request_provenance: null,
      created_at: '2026-09-16T00:00:00Z',
    }
    expect(attempt.transport_error?.type).toBe('timeout')
    expect(attempt.had_uncertain_prior_attempts).toBe(true)
  })
})

describe('RejectedSourceEntryPublic.raw_entry', () => {
  it('carrega a entrada bruta rejeitada, nunca reescrita', () => {
    const rejected: RejectedSourceEntryPublic = {
      kind: 'rejected',
      id: 'rse-1',
      claim_id: null,
      reason: 'invalid_entry',
      raw_entry: { claim_id: 'unknown-claim', relation: 'nonsense' },
      raw_entry_omitted_reason: null,
      created_at: '2026-09-16T00:00:00Z',
    }
    expect(rejected.raw_entry).toEqual({ claim_id: 'unknown-claim', relation: 'nonsense' })
  })
})

describe('DeterministicVerificationAttemptPublic (numeric_verification_attempts)', () => {
  it('audit-only, com os 4 estados reais e assertion tipada', () => {
    const attempt: DeterministicVerificationAttemptPublic = {
      id: 'dva-1',
      claim_id: 'claim-1',
      state: 'contradicts',
      raw_proposal: { left: '2', operator: '+', right: '2', asserted_result: '5' },
      raw_proposal_omitted_reason: null,
      assertion: {
        kind: 'arithmetic',
        left: '2',
        operator: '+',
        right: '2',
        asserted_result: '5',
      },
      computed_result: '4',
      created_at: '2026-09-16T00:00:00Z',
    }
    expect(attempt.state).toBe('contradicts')
    expect(attempt.assertion?.operator).toBe('+')
  })
})
