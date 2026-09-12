// /app/runs/:runId -- GET detail primeiro (Decision Delta secao 13);
// audit só sob ação explícita, via InspectionPanel (lazy). Falha do
// audit nunca apaga o detail já carregado -- são estados independentes.

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import type { RunResponse } from '../api/types'
import { formatDateTime, formatErrorCode } from '../api/formatting'
import { FinalAnswerView } from '../components/FinalAnswerView'
import { AccountingView } from '../components/AccountingView'
import { InspectionPanel } from '../components/InspectionPanel'

type DetailState =
  | { phase: 'loading' }
  | { phase: 'not_found' }
  | { phase: 'error'; message: string }
  | { phase: 'loaded'; run: RunResponse }

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>()
  const [state, setState] = useState<DetailState>({ phase: 'loading' })

  useEffect(() => {
    if (!runId) return
    let cancelled = false
    setState({ phase: 'loading' })
    apiClient
      .getRun(runId)
      .then((run) => {
        if (!cancelled) setState({ phase: 'loaded', run })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (error instanceof ApiError && error.code === 'run_not_found') {
          setState({ phase: 'not_found' })
        } else {
          setState({
            phase: 'error',
            message: error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.',
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [runId])

  if (state.phase === 'loading') {
    return (
      <main>
        <p role="status">Carregando execução…</p>
      </main>
    )
  }

  if (state.phase === 'not_found') {
    return (
      <main>
        <h1>Execução não encontrada</h1>
        <Link to="/runs">Voltar ao histórico</Link>
      </main>
    )
  }

  if (state.phase === 'error') {
    return (
      <main>
        <p role="alert">{state.message}</p>
        <Link to="/runs">Voltar ao histórico</Link>
      </main>
    )
  }

  const run = state.run

  return (
    <main className="run-detail">
      <p>
        <Link to="/runs">← Histórico</Link>
      </p>
      <h1>{run.config.question}</h1>

      {run.status === 'completed' ? (
        <>
          <FinalAnswerView finalAnswer={run.final_answer} />
          <section aria-labelledby="execution-summary-heading">
            <h2 id="execution-summary-heading">Resumo da execução</h2>
            <p>Concluída em {formatDateTime(run.completed_at)}</p>
            <p>Participantes: {run.config.enabled_providers.join(', ')}</p>
            <AccountingView accounting={run.accounting} />
          </section>
        </>
      ) : (
        <section role="alert" aria-labelledby="quorum-heading">
          <h2 id="quorum-heading">Quórum insuficiente</h2>
          <p>
            {run.successful_count} de {run.total_providers} participantes responderam — abaixo do
            mínimo de {run.min_to_return} necessário.
          </p>
          <p>Falhou em {formatDateTime(run.failed_at)}</p>
          <AccountingView accounting={run.accounting} />
        </section>
      )}

      <InspectionPanel runId={run.id} />
    </main>
  )
}
