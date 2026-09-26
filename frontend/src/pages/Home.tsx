import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import { isDirectRun, type LocalPrerequisiteState } from '../api/types'
import { formatErrorCode, formatInvalidRequest } from '../api/formatting'
import { parseReuseInput } from '../lib/reuseInput'
import { completedRunState } from '../lib/completedRunState'
import { RunComposer } from '../components/RunComposer'
import { PendingInvestigation } from '../components/PendingInvestigation'

// Depois do envio, uma pergunta com desfecho persistido tem UMA superfície de
// leitura e UMA URL: /runs/:id. A resposta concluída já recebida segue no
// state da navegação (sem novo GET); quórum insuficiente também já tem
// registro persistido, então abre a mesma página.
//
// Falha sem desfecho confirmado (erro do servidor ou de rede depois do envio)
// NUNCA oferece reenvio de um clique: o POST é síncrono e pode já ter feito
// chamadas pagas aos modelos. A pergunta continua no composer para um reenvio
// deliberado, e o usuário é levado a conferir o Histórico antes.
type SubmissionState =
  | { phase: 'idle' }
  | { phase: 'submitting' }
  | { phase: 'invalid'; message: string }
  | { phase: 'unknown_provider'; message: string }
  | { phase: 'outcome_uncertain' }
  | { phase: 'insufficient_without_record'; message: string }

export function Home() {
  const location = useLocation()
  const navigate = useNavigate()
  const [initialInput] = useState(() => parseReuseInput(location.state))
  const [providers, setProviders] = useState<string[]>([])
  const [localPrerequisites, setLocalPrerequisites] = useState<Record<string, LocalPrerequisiteState>>({})
  const [providersLoading, setProvidersLoading] = useState(true)
  const [providersError, setProvidersError] = useState<string | null>(null)
  const [providersAttempt, setProvidersAttempt] = useState(0)
  const [submission, setSubmission] = useState<SubmissionState>({ phase: 'idle' })

  useEffect(() => {
    let cancelled = false
    apiClient
      .getProviders()
      .then((response) => {
        if (cancelled) return
        setProviders(response.providers)
        setLocalPrerequisites(response.local_prerequisites ?? {})
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
  }, [providersAttempt])

  // GET /providers é seguro de repetir (nenhuma execução, nenhum custo). Só
  // relê o que o servidor JÁ carregou ao iniciar: não relê a configuração
  // nem testa os serviços -- uma configuração nova exige reiniciar a API.
  function retryProviders() {
    setProvidersLoading(true)
    setProvidersError(null)
    setProvidersAttempt((attempt) => attempt + 1)
  }

  async function handleSubmit(
    question: string,
    enabledProviders: string[],
    sourceText: string | null,
    kind?: 'direct',
  ) {
    setSubmission({ phase: 'submitting' })
    try {
      const result = await apiClient.createRun(
        // Conselho: exatamente o envio de sempre. Direta: opt-in explícito.
        kind === 'direct'
          ? { question, enabled_providers: enabledProviders, source_text: null, kind: 'direct' }
          : { question, enabled_providers: enabledProviders, source_text: sourceText },
      )
      if (isDirectRun(result)) {
        // Run direta criada (resposta ou falha do provider registrada): a
        // página dela busca o registro pela URL.
        navigate(`/runs/${encodeURIComponent(result.id)}`)
      } else if (result.status === 'completed') {
        navigate(`/runs/${encodeURIComponent(result.id)}`, { state: completedRunState(result) })
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === 'insufficient_quorum') {
        const runId = error.details?.run_id
        if (typeof runId === 'string' && runId.length > 0) {
          navigate(`/runs/${encodeURIComponent(runId)}`)
        } else {
          setSubmission({ phase: 'insufficient_without_record', message: error.message })
        }
      } else if (error instanceof ApiError && error.code === 'invalid_request') {
        setSubmission({ phase: 'invalid', message: formatInvalidRequest(error.details) })
      } else if (
        error instanceof ApiError &&
        (error.code === 'invalid_provider' || error.code === 'provider_prerequisites_missing')
      ) {
        setSubmission({ phase: 'unknown_provider', message: formatErrorCode(error.code) })
      } else {
        setSubmission({ phase: 'outcome_uncertain' })
      }
    }
  }

  return (
    <main className="home" id="main-content">
      <RunComposer
        providers={providers}
        localPrerequisites={localPrerequisites}
        providersLoading={providersLoading}
        providersError={providersError}
        submitting={submission.phase === 'submitting'}
        initialInput={initialInput}
        onSubmit={handleSubmit}
        onRetryProviders={retryProviders}
      />

      <div aria-live="polite" className="home__result">
        {submission.phase === 'submitting' && <PendingInvestigation />}

        {submission.phase === 'invalid' && (
          <p role="alert" className="notice notice--validation">
            {submission.message}
          </p>
        )}

        {submission.phase === 'unknown_provider' && (
          <div role="alert" className="notice notice--validation">
            <p>{submission.message}</p>
            <button type="button" onClick={retryProviders}>
              Recarregar lista de modelos
            </button>
          </div>
        )}

        {submission.phase === 'insufficient_without_record' && (
          <p role="alert" className="notice notice--neutral">
            Poucos modelos responderam para montar uma resposta.
            {submission.message ? ` ${submission.message}` : ''}
          </p>
        )}

        {submission.phase === 'outcome_uncertain' && (
          <div role="alert" className="notice notice--warning">
            <p>
              Não foi possível confirmar o resultado desta pergunta. Ela pode ter sido processada
              pelos modelos mesmo assim — confira o Histórico antes de perguntar de novo.
            </p>
            <Link to="/runs">Abrir o Histórico</Link>
          </div>
        )}
      </div>
    </main>
  )
}
