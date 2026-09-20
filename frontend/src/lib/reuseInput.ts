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
export function parseReuseInput(state: unknown): ReuseInput | null {
  if (typeof state !== 'object' || state === null || !(REUSE_STATE_KEY in state)) return null
  const raw = (state as Record<string, unknown>)[REUSE_STATE_KEY]
  if (typeof raw !== 'object' || raw === null) return null
  const { question, sourceText, enabledProviders } = raw as Record<string, unknown>
  if (typeof question !== 'string') return null
  if (sourceText !== null && typeof sourceText !== 'string') return null
  if (!Array.isArray(enabledProviders) || !enabledProviders.every((p) => typeof p === 'string')) {
    return null
  }
  return { question, sourceText: sourceText as string | null, enabledProviders }
}
