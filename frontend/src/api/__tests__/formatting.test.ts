import { describe, expect, it } from 'vitest'
import type { DebateOutcome, JudgeOutcome } from '../types'
import {
  formatEstimatedCost,
  formatExactCost,
  formatTokenCount,
  formatDebateOutcome,
  formatFailureCategory,
  formatFinalAnswerStatus,
  formatJudgeOutcome,
  formatModelIdentitySource,
  formatPricingCanonicalModelId,
  formatProviderName,
  formatUsageRecordPresence,
  splitAnswerParagraphs,
} from '../formatting'

describe('formatEstimatedCost', () => {
  it('formata custo desconhecido (null) sem virar zero', () => {
    expect(formatEstimatedCost(null, false)).toBe('Estimativa indisponível')
  })

  it('formata custo conhecido-zero distinto de desconhecido', () => {
    const result = formatEstimatedCost(0, false)
    expect(result).toContain('$0,00')
    expect(result).not.toBe('Estimativa indisponível')
  })

  it('formata custo positivo conhecido', () => {
    expect(formatEstimatedCost(0.0042, false)).toContain('0.0042')
  })

  it('sinaliza quando has_unknown_accounting_components é true mesmo com custo conhecido', () => {
    const result = formatEstimatedCost(0.5, true)
    expect(result).toContain('parcial')
  })
})

describe('formatTokenCount', () => {
  it('tokens desconhecidos nunca viram 0', () => {
    const result = formatTokenCount(null)
    expect(result).not.toBe('0')
  })

  it('tokens conhecidos são formatados', () => {
    expect(formatTokenCount(1000)).not.toBe('—')
  })
})

describe('formatFinalAnswerStatus', () => {
  it('exibe um rótulo conhecido para o novo status llm_planned (Etapa 17B)', () => {
    const result = formatFinalAnswerStatus('llm_planned')
    expect(result).not.toBe('llm_planned') // nunca mostra o valor bruto
    expect(result.length).toBeGreaterThan(0)
  })

  it('continua exibindo um rótulo conhecido para o status histórico llm_composed', () => {
    const result = formatFinalAnswerStatus('llm_composed')
    expect(result).not.toBe('llm_composed')
    expect(result.length).toBeGreaterThan(0)
  })

  it('llm_planned e llm_composed têm rótulos distintos', () => {
    expect(formatFinalAnswerStatus('llm_planned')).not.toBe(formatFinalAnswerStatus('llm_composed'))
  })
})

describe('formatModelIdentitySource', () => {
  it('distingue provider_reported de requested_fallback', () => {
    const reported = formatModelIdentitySource('provider_reported')
    const fallback = formatModelIdentitySource('requested_fallback')
    expect(reported).not.toBe(fallback)
    expect(reported.length).toBeGreaterThan(0)
    expect(fallback.length).toBeGreaterThan(0)
  })

  it('null (histórico) nunca é confundido com requested_fallback', () => {
    const historical = formatModelIdentitySource(null)
    const fallback = formatModelIdentitySource('requested_fallback')
    expect(historical).not.toBe(fallback)
    expect(historical).not.toBe('requested_fallback')
    expect(historical).not.toBe('provider_reported')
  })
})

function _debateOutcome(overrides: Partial<DebateOutcome> = {}): DebateOutcome {
  return {
    skipped_reason: null,
    cumulative_budget_exceeded: false,
    claim_extraction_eligible_response_count: 0,
    claim_extraction_missing_response_count: 0,
    ...overrides,
  }
}

describe('formatDebateOutcome', () => {
  it('retorna null quando não houve skip (nada a explicar)', () => {
    expect(formatDebateOutcome(_debateOutcome())).toBeNull()
  })

  it('formata motivo conhecido de forma legível', () => {
    const result = formatDebateOutcome(
      _debateOutcome({ skipped_reason: 'insufficient_initial_quorum' }),
    )
    expect(result).not.toBe('insufficient_initial_quorum')
    expect(result).toBeTruthy()
  })

  it('formata o novo motivo de falha estrutural de extração (Run02 repair) de forma legível, nunca implicando ausência de informação', () => {
    const result = formatDebateOutcome(
      _debateOutcome({ skipped_reason: 'all_initial_extractions_failed' }),
    )
    expect(result).not.toBe('all_initial_extractions_failed')
    expect(result?.toLowerCase()).not.toContain('nenhuma informação')
  })

  it('cai de volta pro valor cru se o motivo não tiver label conhecido (nunca inventa explicação)', () => {
    const result = formatDebateOutcome(
      _debateOutcome({ skipped_reason: 'motivo_novo_desconhecido' }),
    )
    expect(result).toBe('motivo_novo_desconhecido')
  })
})

function _judgeOutcome(overrides: Partial<JudgeOutcome> = {}): JudgeOutcome {
  return {
    verdict_unavailable_reason: null,
    cumulative_budget_exceeded: false,
    ...overrides,
  }
}

describe('formatJudgeOutcome', () => {
  it('retorna null quando há veredito (nada a explicar)', () => {
    expect(formatJudgeOutcome(_judgeOutcome())).toBeNull()
  })

  // Repair (adversarial review, recheck Finding B) -- o motivo
  // "claim_extraction_incomplete" precisa de um rótulo PT-BR legível
  // (regressão: antes deste repair, caía no fallback cru via
  // `?? outcome.verdict_unavailable_reason` -- ver formatJudgeOutcome).
  it('formata claim_extraction_incomplete de forma legível, nunca o valor interno cru', () => {
    const result = formatJudgeOutcome(
      _judgeOutcome({ verdict_unavailable_reason: 'claim_extraction_incomplete' }),
    )
    expect(result).not.toBe('claim_extraction_incomplete')
    expect(result).toBeTruthy()
  })

  it('claim_extraction_incomplete nunca implica que os participantes falharam, que não havia informação avaliável, discordância do juiz, ou falha genérica de provider', () => {
    const result = formatJudgeOutcome(
      _judgeOutcome({ verdict_unavailable_reason: 'claim_extraction_incomplete' }),
    )
    const lower = result?.toLowerCase() ?? ''
    expect(lower).not.toContain('participantes falharam')
    expect(lower).not.toContain('nenhuma informação')
    expect(lower).not.toContain('discord')
    expect(lower).not.toContain('provider')
    // Semântica precisa: extração ESTRUTURADA incompleta, algumas
    // respostas não puderam ser extraídas.
    expect(lower).toContain('extração')
    expect(lower).toContain('incompleta')
  })

  it('claim_extraction_incomplete tem rótulo distinto de claim_extraction_failed e no_claims_to_judge', () => {
    const incomplete = formatJudgeOutcome(
      _judgeOutcome({ verdict_unavailable_reason: 'claim_extraction_incomplete' }),
    )
    const failed = formatJudgeOutcome(
      _judgeOutcome({ verdict_unavailable_reason: 'claim_extraction_failed' }),
    )
    const noClaims = formatJudgeOutcome(
      _judgeOutcome({ verdict_unavailable_reason: 'no_claims_to_judge' }),
    )
    expect(incomplete).not.toBe(failed)
    expect(incomplete).not.toBe(noClaims)
  })

  it('cai de volta pro valor cru se o motivo não tiver label conhecido (nunca inventa explicação)', () => {
    const result = formatJudgeOutcome(
      _judgeOutcome({ verdict_unavailable_reason: 'motivo_novo_desconhecido' }),
    )
    expect(result).toBe('motivo_novo_desconhecido')
  })
})

describe('splitAnswerParagraphs', () => {
  it('devolve um único bloco quando não há linha em branco', () => {
    expect(splitAnswerParagraphs('Uma resposta de uma linha só.')).toEqual([
      'Uma resposta de uma linha só.',
    ])
  })

  it('divide só em linhas em branco literais, preservando quebras simples dentro de um bloco', () => {
    const text = 'Primeiro bloco.\n\nSegundo bloco,\ncom uma quebra simples dentro dele.'
    expect(splitAnswerParagraphs(text)).toEqual([
      'Primeiro bloco.',
      'Segundo bloco,\ncom uma quebra simples dentro dele.',
    ])
  })

  it('nunca interpreta "- " como marcador de lista nem promove nada a heading (só divide em blocos)', () => {
    const text = 'Conclusões sustentadas pelo debate:\n- claim um\n- claim dois'
    expect(splitAnswerParagraphs(text)).toEqual([
      'Conclusões sustentadas pelo debate:\n- claim um\n- claim dois',
    ])
  })

  it('tolera múltiplas linhas em branco seguidas sem gerar blocos vazios', () => {
    expect(splitAnswerParagraphs('bloco um\n\n\n\nbloco dois')).toEqual(['bloco um', 'bloco dois'])
  })

  it('nunca inventa conteúdo pra uma resposta vazia/só-espaço', () => {
    expect(splitAnswerParagraphs('')).toEqual([])
    expect(splitAnswerParagraphs('   \n\n  ')).toEqual([])
  })
})

describe('formatProviderName', () => {
  it('formata os 3 providers conhecidos com nome próprio de display', () => {
    expect(formatProviderName('openai')).toBe('OpenAI')
    expect(formatProviderName('anthropic')).toBe('Anthropic')
    expect(formatProviderName('gemini')).toBe('Gemini')
  })

  it('provider desconhecido cai num fallback honesto (capitalização simples), nunca inventa um nome', () => {
    expect(formatProviderName('novoprovider')).toBe('Novoprovider')
  })
})

describe('formatFailureCategory', () => {
  it('formata os 6 valores reais de ProviderErrorType com uma categoria humana e limitada', () => {
    expect(formatFailureCategory('timeout')).toBe('tempo limite excedido')
    expect(formatFailureCategory('auth')).toBe('falha de autenticação')
    expect(formatFailureCategory('rate_limit')).toBe('limite de taxa atingido')
    expect(formatFailureCategory('api_error')).toBe('erro do provider')
    expect(formatFailureCategory('malformed_response')).toBe('resposta em formato inesperado')
    expect(formatFailureCategory('unknown')).toBe('motivo não identificado')
  })

  it('tipo de erro desconhecido (schema drift futuro) cai num rótulo honesto, nunca inventa a causa', () => {
    expect(formatFailureCategory('algum_tipo_novo')).toBe('motivo não identificado')
  })
})

describe('formatUsageRecordPresence — repair pós-revisão adversarial', () => {
  it('usage === null (nenhum objeto persistido) é distinto de um objeto presente', () => {
    expect(formatUsageRecordPresence(null)).toBe('não registrado (nenhum objeto de uso persistido)')
  })

  it('usage presente (mesmo com contadores internos null) nunca é confundido com ausência do objeto', () => {
    expect(formatUsageRecordPresence({ input_tokens: null, output_tokens: null })).toBe('registrado')
    expect(formatUsageRecordPresence({ input_tokens: 10, output_tokens: 5 })).toBe('registrado')
  })
})

describe('formatPricingCanonicalModelId — repair pós-revisão adversarial', () => {
  it('null significa RESOLUÇÃO DIRETA na tabela de preços, nunca "dado ausente"', () => {
    expect(formatPricingCanonicalModelId(null)).toBe('resolução direta na tabela de preços (sem alias)')
  })

  it('valor preenchido é reportado como resolução via alias, distinta da resolução direta', () => {
    expect(formatPricingCanonicalModelId('gpt-5.5')).toBe('via alias de gpt-5.5')
  })
})

describe('formatExactCost — repair pós-revisão adversarial', () => {
  it('distingue null, 0 e um valor positivo minúsculo sem arredondar', () => {
    expect(formatExactCost(null)).toBe('não registrado')
    expect(formatExactCost(0)).toBe('0')
    expect(formatExactCost(0.00001)).toBe('0.00001')
  })

  it('nunca colapsa um valor positivo minúsculo pro mesmo texto que zero (diferente de formatEstimatedCost, que arredonda pra exibição amigável)', () => {
    expect(formatExactCost(0.00001)).not.toBe(formatExactCost(0))
    // formatEstimatedCost (.toFixed(4)) arredondaria os dois pro mesmo
    // "~$0.0000" -- por isso formatExactCost existe separadamente.
    expect(formatEstimatedCost(0.00001, false)).toBe('~$0.0000')
  })
})
