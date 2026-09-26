// "Reutilizar pergunta" -- REUSO DE ENTRADA DO USUÁRIO, nunca continuidade
// conversacional. Só três campos autorados pelo usuário viajam (pergunta,
// fonte, participantes); nenhum artefato gerado por modelo, nenhuma
// proveniência, nenhum id de Run -- a nova submissão é uma Run independente
// comum, sem parent/lineage. Transportado via `location.state` (navegação
// leve, sem backend).

export interface ReuseInput {
  question: string
  sourceText: string | null
  enabledProviders: string[]
  // Direct Answer Execution V1 -- presente só pra reuso de uma run direta
  // (o reuso do Conselho continua exatamente como era).
  kind?: 'direct'
}

export const REUSE_STATE_KEY = 'reuseInput'

export function buildReuseState(config: {
  question: string
  source_text: string | null
  enabled_providers: string[]
}): { [REUSE_STATE_KEY]: ReuseInput } {
  return {
    [REUSE_STATE_KEY]: {
      question: config.question,
      sourceText: config.source_text ?? null,
      enabledProviders: [...config.enabled_providers],
    },
  }
}

// `location.state` é de origem não confiável (History API persiste entre
// reloads e pode ser manufaturado) -- valida a forma estritamente e ignora
// qualquer outra chave.
// Direct Answer Execution V1 -- "Perguntar de novo" de uma run direta: a
// mesma pergunta e o mesmo provider, como ENTRADA pra uma run nova. Nunca
// leva o modelo solicitado historicamente (a run nova usa o padrão atual do
// deployment) nem nada do resultado.
export function buildDirectReuseState(config: {
  question: string
  provider: string
}): { [REUSE_STATE_KEY]: ReuseInput } {
  return {
    [REUSE_STATE_KEY]: {
      question: config.question,
      sourceText: null,
      enabledProviders: [config.provider],
      kind: 'direct',
    },
  }
}

export function parseReuseInput(state: unknown): ReuseInput | null {
  if (typeof state !== 'object' || state === null || !(REUSE_STATE_KEY in state)) return null
  const raw = (state as Record<string, unknown>)[REUSE_STATE_KEY]
  if (typeof raw !== 'object' || raw === null) return null
  const { question, sourceText, enabledProviders, kind } = raw as Record<string, unknown>
  if (typeof question !== 'string') return null
  if (sourceText !== null && typeof sourceText !== 'string') return null
  if (!Array.isArray(enabledProviders) || !enabledProviders.every((p) => typeof p === 'string')) {
    return null
  }
  if (kind === 'direct') {
    // Direta: exatamente um provider e nenhuma fonte, ou nada é reusado.
    if (enabledProviders.length !== 1 || sourceText !== null) return null
    return { question, sourceText: null, enabledProviders, kind: 'direct' }
  }
  return { question, sourceText: sourceText as string | null, enabledProviders }
}
