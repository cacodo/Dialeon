// State de navegação Home -> /runs/:id com a resposta concluída que o POST
// acabou de devolver: a página da pergunta mostra o resultado já em mãos, sem
// um segundo GET e sem uma tela de carregamento no meio.
//
// O state do histórico do navegador sobrevive a recarregar a página; por isso
// a leitura valida o formato mínimo E a identidade (o id tem que ser o da
// URL). Qualquer coisa diferente é ignorada e a página busca o registro
// normalmente. Runs concluídas são imutáveis, então o objeto em mãos é o
// mesmo registro que o GET devolveria.

import type { CompletedRunResponse } from '../api/types'

const KEY = 'completedRun'

export function completedRunState(run: CompletedRunResponse): Record<string, unknown> {
  return { [KEY]: run }
}

export function readCompletedRunState(state: unknown, runId: string | undefined): CompletedRunResponse | null {
  if (runId === undefined || typeof state !== 'object' || state === null || !(KEY in state)) return null
  const candidate = (state as Record<string, unknown>)[KEY]
  if (typeof candidate !== 'object' || candidate === null) return null
  const run = candidate as Partial<CompletedRunResponse>
  if (run.status !== 'completed' || run.id !== runId) return null
  if (typeof run.final_answer !== 'object' || run.final_answer === null) return null
  if (typeof run.config !== 'object' || run.config === null || !Array.isArray(run.config.enabled_providers)) {
    return null
  }
  if (typeof run.accounting !== 'object' || run.accounting === null) return null
  return run as CompletedRunResponse
}
