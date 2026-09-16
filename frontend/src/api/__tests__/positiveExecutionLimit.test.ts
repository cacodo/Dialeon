import { describe, expect, it } from 'vitest'
import type { PositiveExecutionLimitPublic, RunConfigPublic } from '../types'

// Historical Non-Finite Execution-Limit Public Representation V1.
//
// `PositiveExecutionLimitPublic` é o tipo NARROW (T23-T25 do contrato
// desta slice) pra `RunConfigPublic.max_cost_usd`/
// `round_dispatch_timeout_seconds` -- os únicos dois campos
// historicamente alcançáveis com +inf. As checagens abaixo são, na
// maior parte, checagens de TIPO EM TEMPO DE COMPILAÇÃO (`tsc` falha
// se `T25` deixar de ser rejeitado) -- os `expect()` em runtime
// confirmam que o valor sobrevive sem transformação além disso.

describe('PositiveExecutionLimitPublic', () => {
  it('T23 -- aceita um valor numérico finito', () => {
    const value: PositiveExecutionLimitPublic = 1.5
    expect(value).toBe(1.5)
    expect(typeof value).toBe('number')
  })

  it('T24 -- aceita o token literal exato de compatibilidade histórica', () => {
    const value: PositiveExecutionLimitPublic = 'positive_infinity'
    expect(value).toBe('positive_infinity')
    expect(typeof value).toBe('string')
  })

  it('T25 -- NÃO se torna um union amplo string/any (rejeitado em tempo de compilação)', () => {
    // @ts-expect-error -- qualquer outra string não é um
    // PositiveExecutionLimitPublic válido; se este comentário parar de
    // ser necessário (porque o tipo virou `string` genérico), a
    // compilação (`tsc --noEmit`) falha aqui, pegando o enfraquecimento.
    const invalid: PositiveExecutionLimitPublic = 'unlimited'
    expect(invalid).toBe('unlimited') // nunca alcançado sob o tipo correto -- só documenta intenção
  })

  it('RunConfigPublic.max_cost_usd/round_dispatch_timeout_seconds usam o tipo narrow, nunca number|string genérico', () => {
    const config: RunConfigPublic = {
      question: 'q',
      enabled_providers: ['openai'],
      claim_processor_provider: 'anthropic',
      judge_provider: 'anthropic',
      editor_provider: 'anthropic',
      source_analyzer_provider: 'anthropic',
      source_text: null,
      max_cost_usd: 'positive_infinity',
      max_total_tokens: 1000,
      max_output_tokens_per_call: 100,
      max_output_tokens_grouping: 100,
      max_output_tokens_judge: 100,
      round_dispatch_timeout_seconds: 42,
      quorum: { min_for_debate: 1, min_to_return: 1 },
    }

    expect(config.max_cost_usd).toBe('positive_infinity')
    expect(config.round_dispatch_timeout_seconds).toBe(42)

    // Campos numéricos NÃO relacionados a esta slice continuam
    // estritamente `number` -- nunca enfraquecidos pra este union.
    expect(typeof config.max_total_tokens).toBe('number')
    expect(typeof config.max_output_tokens_per_call).toBe('number')
  })

  it('nunca coage o token de volta pra Infinity do JavaScript', () => {
    const value: PositiveExecutionLimitPublic = 'positive_infinity'
    // @ts-expect-error -- comparar diretamente com Infinity não faz
    // sentido de tipo (string vs number) -- documenta que nenhuma
    // conversão implícita é esperada em nenhum consumidor.
    const asNumberComparison = value === Infinity
    expect(asNumberComparison).toBe(false)
  })

  it('review F1 LOW (M12) -- um campo numérico NÃO relacionado (max_total_tokens) rejeita o token em tempo de compilação', () => {
    const config: RunConfigPublic = {
      question: 'q',
      enabled_providers: ['openai'],
      claim_processor_provider: 'anthropic',
      judge_provider: 'anthropic',
      editor_provider: 'anthropic',
      source_analyzer_provider: 'anthropic',
      source_text: null,
      max_cost_usd: 1,
      // @ts-expect-error -- `max_total_tokens` é `number` puro, nunca
      // `PositiveExecutionLimitPublic` -- o token de compatibilidade
      // (ou qualquer string) nunca é aceito aqui. Se este comentário
      // parar de ser necessário, o campo foi acidentalmente
      // enfraquecido pro union desta slice.
      max_total_tokens: 'positive_infinity',
      max_output_tokens_per_call: 100,
      max_output_tokens_grouping: 100,
      max_output_tokens_judge: 100,
      round_dispatch_timeout_seconds: 42,
      quorum: { min_for_debate: 1, min_to_return: 1 },
    }

    expect(config.max_total_tokens).toBe('positive_infinity') // só alcançável via o erro de tipo suprimido acima
  })
})
