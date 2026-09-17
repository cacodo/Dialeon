// /app/runs -- histórico (Decision Delta secao 12). Usa SÓ os dados de
// RunSummaryResponse -- nenhuma chamada N+1 pra descobrir "question" de
// cada Run, porque o summary genuinamente não fornece isso.

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import type { RunSummaryResponse } from '../api/types'
import { formatDateTime, formatErrorCode } from '../api/formatting'

const PAGE_SIZE = 20

// T02.4 -- rótulos pros 2 estados novos ("running"/"failed"), lado a
// lado dos 2 já existentes -- nenhuma lógica nova, só apresentação.
const STATUS_LABELS: Record<RunSummaryResponse['status'], string> = {
  completed: 'Concluída',
  insufficient_quorum: 'Quórum insuficiente',
  running: 'Em andamento',
  failed: 'Falhou',
}

type ListState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'loaded'; runs: RunSummaryResponse[] }

export function History() {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<ListState>({ phase: 'loading' })

  useEffect(() => {
    let cancelled = false
    setState({ phase: 'loading' })
    apiClient
      .listRuns(PAGE_SIZE, offset)
      .then((response) => {
        if (!cancelled) setState({ phase: 'loaded', runs: response.runs })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            phase: 'error',
            message: error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.',
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [offset])

  return (
    <main className="history">
      <h1>Histórico</h1>

      {state.phase === 'loading' && <p role="status">Carregando histórico…</p>}
      {state.phase === 'error' && <p role="alert">{state.message}</p>}

      {state.phase === 'loaded' && (
        <>
          {state.runs.length === 0 ? (
            <p>Nenhuma execução ainda.</p>
          ) : (
            <ul className="history__list">
              {state.runs.map((run) => (
                <li key={run.id} className="history__item">
                  <Link to={`/runs/${run.id}`}>
                    <span className="history__status">{STATUS_LABELS[run.status]}</span>
                    <span className="history__timestamp">{formatDateTime(run.started_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}

          <nav className="history__pagination" aria-label="Paginação do histórico">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Anterior
            </button>
            <button
              type="button"
              disabled={state.runs.length < PAGE_SIZE}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Próxima
            </button>
          </nav>
        </>
      )}
    </main>
  )
}
