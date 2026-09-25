// State de navegação Home -> /runs/:id com a resposta concluída que o POST
// acabou de devolver: a página da pergunta mostra o resultado já em mãos, sem
// um segundo GET e sem uma tela de carregamento no meio.
//
// O state do histórico do navegador sobrevive a recarregar a página (e a
// atualizações do app); por isso ele é só uma OTIMIZAÇÃO, nunca uma
// autoridade concorrente com a URL. A leitura exige a identidade (o id tem
// que ser o da URL) e a estrutura que a interface de resultado de fato
// percorre ao renderizar (ver FinalAnswerView/RunDetail). Qualquer coisa
// diferente -- incompleta, malformada, de outra versão -- é ignorada e a
// página busca o registro pela URL (falha fechada). Runs concluídas são
// imutáveis, então um objeto válido em mãos é o mesmo registro que o GET
// devolveria.

import type { CompletedRunResponse } from '../api/types'

const KEY = 'completedRun'

export function completedRunState(run: CompletedRunResponse): Record<string, unknown> {
  return { [KEY]: run }
}

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const isStr = (v: unknown): v is string => typeof v === 'string'
const isStrArray = (v: unknown): boolean => Array.isArray(v) && v.every(isStr)
const isArrayOf = (v: unknown, item: (x: unknown) => boolean): boolean =>
  Array.isArray(v) && v.every(item)
// Campos aditivos (ausentes em registros anteriores a eles): ausente/null é
// válido e cai no fallback do renderizador; presente tem que ter a forma
// que o renderizador usa.
const optional = (v: unknown, check: (x: unknown) => boolean): boolean => v == null || check(v)
const isOptionalBool = (v: unknown): boolean => v === undefined || typeof v === 'boolean'

const isClaimItem = (v: unknown): boolean =>
  isObj(v) &&
  isStr(v.claim_text) &&
  isStr(v.verdict_label) &&
  isStr(v.explanation) &&
  optional(v.source_relationship_note, isStr)

// Tipos de bloco desconhecidos não são renderizados (a resposta cai pro
// texto canônico); os conhecidos precisam da forma que o renderizador lê.
const isAnswerBlock = (v: unknown): boolean => {
  if (!isObj(v) || !isStr(v.kind)) return false
  if (v.kind === 'paragraph') return isStr(v.text)
  if (v.kind === 'claim_section') return isStr(v.heading) && isArrayOf(v.items, isClaimItem)
  return true
}

const isPrimaryAnswer = (v: unknown): boolean =>
  isObj(v) &&
  isStr(v.lead_in) &&
  isStr(v.scope_note) &&
  isStr(v.rendered_text) &&
  typeof v.assessed_claim_count === 'number' &&
  isStrArray(v.limitations) &&
  isArrayOf(
    v.sections,
    (section) =>
      isObj(section) &&
      isStr(section.role) &&
      isStr(section.heading) &&
      isArrayOf(
        section.items,
        (item) => isObj(item) && isStr(item.claim_id) && isStr(item.claim_text) && isStr(item.verdict_label),
      ),
  )

const isNaturalAnswer = (v: unknown): boolean => isObj(v) && isStr(v.rendered_text)

const isLinguisticRealization = (v: unknown): boolean =>
  isObj(v) && isStr(v.rendered_text) && isArrayOf(v.blocks, (block) => isObj(block) && isStr(block.text))

const isFinalAnswer = (v: unknown): boolean =>
  isObj(v) &&
  isStr(v.answer_text) &&
  isStr(v.status) &&
  isStrArray(v.limitations) &&
  optional(v.answer_blocks, (blocks) => isArrayOf(blocks, isAnswerBlock)) &&
  optional(v.unevaluated_claims, isStrArray) &&
  optional(v.primary_answer, isPrimaryAnswer) &&
  optional(v.natural_answer, isNaturalAnswer) &&
  optional(v.linguistic_realization, isLinguisticRealization) &&
  isOptionalBool(v.natural_answer_presentation_eligible) &&
  isOptionalBool(v.linguistic_realization_presentation_eligible)

const isConfig = (v: unknown): boolean =>
  isObj(v) && isStr(v.question) && isStrArray(v.enabled_providers) && (v.source_text === null || isStr(v.source_text))

const isAccounting = (v: unknown): boolean =>
  isObj(v) &&
  (v.estimated_cost_usd === null || typeof v.estimated_cost_usd === 'number') &&
  typeof v.has_unknown_accounting_components === 'boolean'

export function readCompletedRunState(state: unknown, runId: string | undefined): CompletedRunResponse | null {
  if (runId === undefined || !isObj(state)) return null
  const run = state[KEY]
  if (!isObj(run) || run.status !== 'completed' || run.id !== runId) return null
  if (!isStr(run.started_at) || !isStr(run.completed_at)) return null
  if (!isFinalAnswer(run.final_answer) || !isConfig(run.config) || !isAccounting(run.accounting)) return null
  return run as unknown as CompletedRunResponse
}
