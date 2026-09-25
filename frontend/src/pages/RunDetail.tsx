import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import type { RunConfigPublic, RunResponse } from '../api/types'
import {
  formatDateTime,
  formatErrorCode,
  formatEstimatedCost,
  formatFailureStage,
  formatModelList,
  formatRecordedDuration,
} from '../api/formatting'
import { AnswerAssessmentDetails, FinalAnswerView } from '../components/FinalAnswerView'
import { InspectionPanel } from '../components/InspectionPanel'
import { ProviderExecutionPolicyView } from '../components/ProviderExecutionPolicyView'
import { isValidPage } from '../lib/safePage'
import { buildReuseState } from '../lib/reuseInput'
import { readCompletedRunState } from '../lib/completedRunState'
import { characterCount, formatCharacterLimit } from '../lib/inputLimits'

// Página de UMA pergunta respondida -- a mesma superfície pra resposta recém
// concluída (vinda da Home, com o resultado já em mãos) e pra uma pergunta
// reaberta pelo Histórico. Hierarquia (resposta primeiro):
//
//   pergunta -> resposta (+ limitações necessárias) -> resumo factual curto
//   -> próximas ações -> "Como esta resposta foi produzida" (profundidade 1+)
//   -> auditoria técnica (profundidade 3).

function historyBackHref(state: unknown): string {
  if (typeof state === 'object' && state !== null && 'fromHistoryPage' in state) {
    const page = (state as { fromHistoryPage: unknown }).fromHistoryPage
    if (isValidPage(page)) {
      return page > 1 ? `/runs?page=${page}` : '/runs'
    }
  }
  return '/runs'
}

type DetailState =
  | { phase: 'loading' }
  | { phase: 'not_found' }
  | { phase: 'error'; message: string }
  | { phase: 'loaded'; run: RunResponse }

type RefreshState =
  | { phase: 'idle' }
  | { phase: 'refreshing' }
  | { phase: 'still_without_outcome' }
  | { phase: 'error'; message: string }

function NextActions({ config }: { config: RunConfigPublic }) {
  return (
    <div className="run-actions">
      <Link to="/" state={buildReuseState(config)} aria-describedby="reuse-description">
        Perguntar de novo
      </Link>
      <Link to="/">Nova pergunta</Link>
      <p id="reuse-description" className="sr-only">
        Perguntar de novo abre uma nova pergunta com a pergunta, a fonte e os modelos desta
        execução. Nada do resultado anterior é enviado.
      </p>
    </div>
  )
}

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>()
  const location = useLocation()
  const backHref = historyBackHref(location.state)
  // Resposta que a Home acabou de receber (ver lib/completedRunState.ts):
  // mostrada direto, sem segundo GET nem tela de carregamento.
  const handedOver = readCompletedRunState(location.state, runId)
  const [state, setState] = useState<DetailState>(() =>
    handedOver !== null ? { phase: 'loaded', run: handedOver } : { phase: 'loading' },
  )
  // Id da Run que está na tela. Evita um GET pra resposta que já chegou em
  // mãos -- inclusive com os efeitos invocados duas vezes do StrictMode --, e
  // qualquer troca de Run na URL busca a nova normalmente.
  const displayedRunId = useRef<string | null>(handedOver?.id ?? null)
  const [refresh, setRefresh] = useState<RefreshState>({ phase: 'idle' })
  // Uma resposta de "Atualizar registro" que chega depois que o usuário já
  // navegou pra outra Run nunca pode sobrescrever a Run atual.
  const activeRunId = useRef<string | undefined>(runId)
  useEffect(() => {
    activeRunId.current = runId
    return () => {
      activeRunId.current = undefined
    }
  }, [runId])

  async function handleRefresh() {
    if (!runId) return
    const requested = runId
    setRefresh({ phase: 'refreshing' })
    try {
      const run = await apiClient.getRun(requested)
      if (activeRunId.current !== requested) return
      setState({ phase: 'loaded', run })
      setRefresh(run.status === 'running' ? { phase: 'still_without_outcome' } : { phase: 'idle' })
    } catch (error) {
      if (activeRunId.current !== requested) return
      setRefresh({
        phase: 'error',
        message: error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.',
      })
    }
  }

  useEffect(() => {
    if (!runId) return
    if (displayedRunId.current === runId) return
    let cancelled = false
    setState({ phase: 'loading' })
    apiClient
      .getRun(runId)
      .then((run) => {
        if (cancelled) return
        displayedRunId.current = runId
        setState({ phase: 'loaded', run })
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
      <main className="run-detail" id="main-content">
        <p role="status">Carregando pergunta…</p>
      </main>
    )
  }

  if (state.phase === 'not_found') {
    return (
      <main className="run-detail" id="main-content">
        <h1>Pergunta não encontrada</h1>
        <Link to={backHref}>Voltar ao histórico</Link>
      </main>
    )
  }

  if (state.phase === 'error') {
    return (
      <main className="run-detail" id="main-content">
        <p role="alert" className="notice notice--error">
          {state.message}
        </p>
        <Link to={backHref}>Voltar ao histórico</Link>
      </main>
    )
  }

  const run = state.run
  const models = formatModelList(run.config.enabled_providers)

  return (
    <main className="run-detail" id="main-content">
      <p className="run-detail__back">
        <Link to={backHref}>← Histórico</Link>
      </p>

      {/* A pergunta é contexto pra resposta: o h1 é um rótulo pequeno, com a
          pergunta completa (nunca truncada/reinterpretada) logo abaixo. */}
      <h1 className="run-detail__question-label">Pergunta</h1>
      <p className="run-detail__question-text">{run.config.question}</p>

      {run.config.source_text != null && run.config.source_text !== '' && (
        <details className="run-detail__source">
          <summary>
            Fonte fornecida ({formatCharacterLimit(characterCount(run.config.source_text))}{' '}
            caracteres)
          </summary>
          <p className="run-detail__source-note">
            Texto que você forneceu para esta pergunta. Ele é comparado com as afirmações da
            resposta; não é verificado como verdadeiro.
          </p>
          <pre className="run-detail__source-text">{run.config.source_text}</pre>
        </details>
      )}

      {run.status === 'completed' && (
        <>
          <FinalAnswerView finalAnswer={run.final_answer} />

          {/* Resumo factual curto -- só o que ajuda a situar a resposta.
              Identidade bruta de modelo e política de execução ficam na
              auditoria técnica. */}
          <ul className="run-meta" aria-label="Resumo da resposta">
            <li>Modelos: {models}</li>
            <li>
              Custo estimado:{' '}
              {formatEstimatedCost(
                run.accounting.estimated_cost_usd,
                run.accounting.has_unknown_accounting_components,
              )}
            </li>
            {formatRecordedDuration(run.started_at, run.completed_at) !== null && (
              <li>Duração: {formatRecordedDuration(run.started_at, run.completed_at)}</li>
            )}
            <li>
              Concluída em <time dateTime={run.completed_at}>{formatDateTime(run.completed_at)}</time>
            </li>
          </ul>

          <NextActions config={run.config} />

          <InspectionPanel runId={run.id} returnTargetId="final-answer-heading">
            <AnswerAssessmentDetails finalAnswer={run.final_answer} />
          </InspectionPanel>
        </>
      )}

      {run.status === 'insufficient_quorum' && (
        <>
          <section aria-labelledby="quorum-heading" className="notice notice--neutral run-outcome">
            <h2 id="quorum-heading">Não houve respostas suficientes</h2>
            <p>
              {run.successful_count} de {run.total_providers}{' '}
              {run.total_providers === 1 ? 'modelo respondeu' : 'modelos responderam'} — eram
              necessárias pelo menos {run.min_to_return} para montar uma resposta, então nenhuma
              resposta foi produzida.
            </p>
            <p>
              O motivo de cada modelo aparece em “O que aconteceu nesta pergunta”, logo abaixo.
            </p>
            <ul className="run-meta" aria-label="Resumo da tentativa">
              <li>Modelos: {models}</li>
              <li>
                Custo estimado:{' '}
                {formatEstimatedCost(
                  run.accounting.estimated_cost_usd,
                  run.accounting.has_unknown_accounting_components,
                )}
              </li>
              <li>
                Encerrada em <time dateTime={run.failed_at}>{formatDateTime(run.failed_at)}</time>
              </li>
            </ul>
          </section>

          <NextActions config={run.config} />

          <InspectionPanel
            runId={run.id}
            title="O que aconteceu nesta pergunta"
            intro="O que cada modelo respondeu, o motivo de cada falha e a auditoria técnica completa."
          />
        </>
      )}

      {run.status === 'running' && (
        <>
          <section aria-labelledby="running-heading" className="notice notice--neutral run-outcome">
            <h2 id="running-heading">Sem desfecho registrado</h2>
            <p>Iniciada em {formatDateTime(run.started_at)}.</p>
            <p>
              Nenhum desfecho terminal foi registrado para esta execução. Ela pode ainda estar
              ativa ou ter sido interrompida; os dois casos são indistinguíveis a partir deste
              registro.
            </p>
            <p>
              <button
                type="button"
                className="run-detail__refresh"
                onClick={handleRefresh}
                disabled={refresh.phase === 'refreshing'}
              >
                {refresh.phase === 'refreshing' ? 'Atualizando…' : 'Atualizar registro'}
              </button>
            </p>
            {refresh.phase === 'still_without_outcome' && (
              <p role="status">Continua sem desfecho registrado.</p>
            )}
            {refresh.phase === 'error' && (
              <p role="alert" className="notice notice--error">
                {refresh.message}
              </p>
            )}
            <ProviderExecutionPolicyView policy={run.provider_execution_policy} />
          </section>
          <NextActions config={run.config} />
        </>
      )}

      {run.status === 'failed' && (
        <>
          <section role="alert" aria-labelledby="failed-heading" className="notice notice--error run-outcome">
            <h2 id="failed-heading">Falhou</h2>
            <p>
              Iniciada em {formatDateTime(run.started_at)}, falhou em {formatDateTime(run.failed_at)}.
            </p>
            {run.failure_stage !== null && <p>{formatFailureStage(run.failure_stage)}</p>}
            <p>{run.message}</p>
            <ProviderExecutionPolicyView policy={run.provider_execution_policy} />
          </section>
          <NextActions config={run.config} />
        </>
      )}
    </main>
  )
}
