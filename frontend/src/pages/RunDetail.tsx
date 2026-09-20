// /app/runs/:runId -- GET detail primeiro (Decision Delta secao 13);
// audit só sob ação explícita, via InspectionPanel (lazy). Falha do
// audit nunca apaga o detail já carregado -- são estados independentes.

import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import type { RunResponse } from '../api/types'
import { formatDateTime, formatErrorCode, formatProviderName } from '../api/formatting'
import { FinalAnswerView } from '../components/FinalAnswerView'
import { AccountingView } from '../components/AccountingView'
import { InspectionPanel } from '../components/InspectionPanel'
import { ProviderExecutionPolicyView } from '../components/ProviderExecutionPolicyView'
import { isValidPage } from '../lib/safePage'
import { buildReuseState } from '../lib/reuseInput'
import { characterCount, formatCharacterLimit } from '../lib/inputLimits'

// Contagem + nomes de exibição (Claude/GPT/Gemini) -- os ids canônicos ficam
// só na Auditoria técnica.
function participantCountLabel(providers: string[]): string {
  const count = providers.length
  const names = providers.map(formatProviderName).join(', ')
  return `${count} participante${count === 1 ? '' : 's'} no debate (${names})`
}

// Origem de navegação (History → RunDetail) -- narrow, sem framework de
// navegação novo: só lê `location.state.fromHistoryPage`, um número que
// `History.tsx` anexa ao `<Link>` de cada linha. Ausente/malformado/
// inseguro (acesso direto a /runs/:id, refresh, deep link, ou um
// `location.state` manufaturado por fora do fluxo normal de navegação)
// cai honestamente pro /runs simples (primeira página) -- MESMA
// validação de segurança de página que History.tsx aplica à URL (ver
// `lib/safePage.ts`), nunca uma semântica de fallback diferente entre
// os dois pontos de entrada.
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

// Atualização MANUAL de uma Run sem desfecho terminal registrado -- só um
// reload explícito do registro persistido pela API existente. Nunca polling,
// nunca progresso inventado, nunca uma alegação de que a execução está viva.
type RefreshState =
  | { phase: 'idle' }
  | { phase: 'refreshing' }
  | { phase: 'still_without_outcome' }
  | { phase: 'error'; message: string }

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>()
  const location = useLocation()
  const backHref = historyBackHref(location.state)
  const [state, setState] = useState<DetailState>({ phase: 'loading' })
  const [refresh, setRefresh] = useState<RefreshState>({ phase: 'idle' })
  // Ignora o resultado de um refresh que terminou depois de a rota mudar/
  // desmontar (mesma disciplina de `cancelled` do carregamento inicial).
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
        <Link to={backHref}>Voltar ao histórico</Link>
      </main>
    )
  }

  if (state.phase === 'error') {
    return (
      <main>
        <p role="alert">{state.message}</p>
        <Link to={backHref}>Voltar ao histórico</Link>
      </main>
    )
  }

  const run = state.run

  return (
    <main className="run-detail">
      <p>
        <Link to={backHref}>← Histórico</Link>
      </p>
      {/* Polimento visual UI Slice 2 -- a pergunta é contexto pra
          resposta, nunca o protagonista visual desta tela (o resultado
          da execução é quem tem maior autoridade visual aqui). O h1
          continua sendo o heading primário da página -- só deliberadamente
          pequeno/restrito, com a pergunta completa (que pode ser longa)
          como texto legível logo abaixo, nunca truncada/reinterpretada. */}
      <h1 className="run-detail__question-label">Pergunta</h1>
      <p className="run-detail__question-text">{run.config.question}</p>

      {run.config.source_text != null && run.config.source_text !== '' && (
        // Entrada do USUÁRIO, fiel byte a byte, recolhida por padrão (pode ter
        // até 20.000 caracteres). Nunca apresentada como evidência verificada.
        <details className="run-detail__source">
          <summary>
            Fonte fornecida ({formatCharacterLimit(characterCount(run.config.source_text))}{' '}
            caracteres)
          </summary>
          <p className="run-detail__source-note">
            Texto que você forneceu para esta execução. Ele é comparado com as afirmações do
            debate; não é verificado como verdadeiro.
          </p>
          <pre className="run-detail__source-text">{run.config.source_text}</pre>
        </details>
      )}

      <p className="run-detail__reuse">
        <Link to="/" state={buildReuseState(run.config)}>
          Reutilizar pergunta
        </Link>
        <span className="run-detail__reuse-hint">
          {' '}
          Abre uma nova investigação com a pergunta, a fonte e os participantes desta execução.
          Nada do resultado anterior é enviado.
        </span>
      </p>

      {run.status === 'completed' && (
        <>
          <FinalAnswerView finalAnswer={run.final_answer} />
          {/* Slice de hierarquia de inspeção -- este resumo é
              deliberadamente quieto: só o que ajuda a interpretar a
              resposta sem exigir nenhum clique (quando concluiu, quantos
              participantes, custo aproximado). Identidade bruta de
              provider/modelo e política de execução do provider são
              detalhe técnico, não necessário pra entender a resposta --
              vivem na auditoria técnica (disclosure explícito dentro de
              `InspectionPanel`, alcançável por "Inspecionar execução"
              logo abaixo), nunca aqui. */}
          <section aria-labelledby="execution-summary-heading">
            <h2 id="execution-summary-heading">Resumo da execução</h2>
            <p>Concluída em {formatDateTime(run.completed_at)}</p>
            <p>{participantCountLabel(run.config.enabled_providers)}.</p>
            <AccountingView accounting={run.accounting} compact />
          </section>
        </>
      )}

      {run.status === 'insufficient_quorum' && (
        <section role="alert" aria-labelledby="quorum-heading">
          <h2 id="quorum-heading">Quórum insuficiente</h2>
          <p>
            {run.successful_count} de {run.total_providers} participantes responderam — abaixo do
            mínimo de {run.min_to_return} necessário.
          </p>
          <p>Falhou em {formatDateTime(run.failed_at)}</p>
          <AccountingView accounting={run.accounting} compact />
        </section>
      )}

      {run.status === 'running' && (
        // T02.4 -- "running" persistido prova só "aceita, sem desfecho
        // terminal registrado": a execução pode ainda estar ativa ou ter
        // sido interrompida (indistinguíveis por design, ver
        // AcceptedRunRow). Nunca afirma progresso nem inventa desfecho --
        // mesmo rótulo neutro do History.
        <section aria-labelledby="running-heading">
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
          {refresh.phase === 'error' && <p role="alert">{refresh.message}</p>}
          <ProviderExecutionPolicyView policy={run.provider_execution_policy} />
        </section>
      )}

      {run.status === 'failed' && (
        <section role="alert" aria-labelledby="failed-heading">
          <h2 id="failed-heading">Falhou</h2>
          <p>Iniciada em {formatDateTime(run.started_at)}, falhou em {formatDateTime(run.failed_at)}.</p>
          <p>{run.message}</p>
          <ProviderExecutionPolicyView policy={run.provider_execution_policy} />
        </section>
      )}

      {/* T02.4 -- inspeção detalhada só existe pra runs com desfecho
          terminal auditável (completed/insufficient_quorum); "running"/
          "failed" nunca têm claim/attempt/verdict pra mostrar. */}
      {(run.status === 'completed' || run.status === 'insufficient_quorum') && (
        <InspectionPanel runId={run.id} />
      )}
    </main>
  )
}
