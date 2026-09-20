// /app -- experiência principal (Decision Delta secao 7). Uma nova
// submissão é sempre uma nova Run independente -- nenhum estado de
// Conversation é mantido entre submissões.

import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import type { RunResponse } from '../api/types'
import { formatErrorCode, formatInvalidRequest } from '../api/formatting'
import { parseReuseInput } from '../lib/reuseInput'
import { RunComposer } from '../components/RunComposer'
import { FinalAnswerView } from '../components/FinalAnswerView'
import { AccountingView } from '../components/AccountingView'

type SubmissionState =
  | { phase: 'idle' }
  | { phase: 'submitting' }
  | { phase: 'completed'; result: Extract<RunResponse, { status: 'completed' }> }
  | { phase: 'insufficient_quorum'; runId: string; message: string }
  | { phase: 'error'; message: string }

export function Home() {
  // Reuso de entrada vindo de "Reutilizar pergunta" (RunDetail) -- só os
  // três campos do usuário; a submissão continua uma Run independente comum.
  const location = useLocation()
  const [initialInput] = useState(() => parseReuseInput(location.state))
  const [providers, setProviders] = useState<string[]>([])
  const [providersLoading, setProvidersLoading] = useState(true)
  const [providersError, setProvidersError] = useState<string | null>(null)
  const [submission, setSubmission] = useState<SubmissionState>({ phase: 'idle' })

  useEffect(() => {
    let cancelled = false
    apiClient
      .getProviders()
      .then((response) => {
        if (!cancelled) setProviders(response.providers)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setProvidersError(error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.')
        }
      })
      .finally(() => {
        if (!cancelled) setProvidersLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSubmit(
    question: string,
    enabledProviders: string[],
    sourceText: string | null,
  ) {
    setSubmission({ phase: 'submitting' })
    try {
      const result = await apiClient.createRun({
        question,
        enabled_providers: enabledProviders,
        source_text: sourceText,
      })
      if (result.status === 'completed') {
        setSubmission({ phase: 'completed', result })
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === 'insufficient_quorum') {
        const runId = (error.details?.run_id as string | undefined) ?? null
        setSubmission({
          phase: 'insufficient_quorum',
          runId: runId ?? '',
          message: error.message,
        })
      } else if (error instanceof ApiError && error.code === 'invalid_request') {
        setSubmission({ phase: 'error', message: formatInvalidRequest(error.details) })
      } else if (error instanceof ApiError) {
        setSubmission({ phase: 'error', message: formatErrorCode(error.code) })
      } else {
        setSubmission({ phase: 'error', message: 'Erro inesperado.' })
      }
    }
  }

  return (
    <main className="home">
      <RunComposer
        providers={providers}
        providersLoading={providersLoading}
        providersError={providersError}
        submitting={submission.phase === 'submitting'}
        initialInput={initialInput}
        onSubmit={handleSubmit}
      />

      <div aria-live="polite" className="home__result">
        {submission.phase === 'submitting' && <p role="status">Investigando…</p>}

        {submission.phase === 'completed' && (
          <>
            <FinalAnswerView finalAnswer={submission.result.final_answer} />
            <AccountingView accounting={submission.result.accounting} compact />
            <Link to={`/runs/${submission.result.id}`}>Ver detalhes desta execução</Link>
          </>
        )}

        {submission.phase === 'insufficient_quorum' && (
          <div role="alert" className="home__quorum-failure">
            <p>
              Não houve participantes suficientes para produzir um resultado desta vez.
              {submission.message ? ` ${submission.message}` : ''}
            </p>
            {submission.runId && <Link to={`/runs/${submission.runId}`}>Ver detalhes</Link>}
          </div>
        )}

        {submission.phase === 'error' && (
          <p role="alert" className="home__error">
            {submission.message}
          </p>
        )}
      </div>
    </main>
  )
}
