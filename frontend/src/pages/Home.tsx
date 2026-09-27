import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { apiClient, ApiError } from '../api/client'
import { isDirectRun, type CouncilReadiness, type LocalPrerequisiteState } from '../api/types'
import { formatErrorCode, formatInvalidRequest } from '../api/formatting'
import { parseReuseInput } from '../lib/reuseInput'
import { completedRunState } from '../lib/completedRunState'
import { RunComposer, type CouncilAdmissionChoice } from '../components/RunComposer'
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
  // Council Local Execution Readiness & Admission V1 -- admissão estrita
  // recusada, ou reconhecimento de uma degradação que mudou, ANTES de
  // qualquer registro ou chamada: nada foi enviado.
  | {
      phase: 'readiness_blocked'
      code: 'council_prerequisites_missing' | 'council_readiness_changed'
      // A avaliação do servidor ainda tem ausência local CONHECIDA (e o aviso
      // aparece acima)? Sem ela, nada há a reconhecer: é só perguntar de novo.
      // `null`: a avaliação não veio num formato reconhecível -- nada é afirmado.
      knownMissing: boolean | null
    }

// A avaliação que acompanha uma recusa (`details.readiness`) só é usada se
// tiver a forma esperada -- nunca inventada a partir de outra coisa.
//
// Só `council_local_readiness_v2`: é a avaliação que o servidor ATUAL produz
// no aceite (o mapa de modelos dos participantes é resolvido antes da
// prontidão), e a interface é servida pelo mesmo pacote do servidor. `v1` só
// existe em registros históricos de aceite (auditoria), nunca numa recusa
// nova -- se aparecer aqui, é tratada como não reconhecida (texto neutro).
// A identidade da degradação precisa ter a forma do contrato: é dela que sai
// o próximo reconhecimento deliberado.
const DEGRADATION_FINGERPRINT = /^sha256:[0-9a-f]{64}$/

function readinessFromDetails(details: Record<string, unknown> | null): CouncilReadiness | null {
  const candidate = details?.readiness as Partial<CouncilReadiness> | undefined
  return candidate !== undefined &&
    candidate !== null &&
    candidate.contract_version === 'council_local_readiness_v2' &&
    Array.isArray(candidate.dependencies) &&
    // só os valores do contrato (CouncilReadinessSummary no backend)
    (candidate.summary === 'all_met' ||
      candidate.summary === 'some_unknown' ||
      candidate.summary === 'some_missing') &&
    (candidate.known_degradation_fingerprint === null ||
      (typeof candidate.known_degradation_fingerprint === 'string' &&
        DEGRADATION_FINGERPRINT.test(candidate.known_degradation_fingerprint)))
    ? (candidate as CouncilReadiness)
    : null
}

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
  const [rejectedReadiness, setRejectedReadiness] = useState<CouncilReadiness | null>(null)

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
    admission?: CouncilAdmissionChoice,
    participantModelOverrides?: Record<string, string>,
    directRequestedModel?: string,
  ) {
    setSubmission({ phase: 'submitting' })
    try {
      const result = await apiClient.createRun(
        // Direta: opt-in explícito, sem admissão do Conselho. Conselho: o envio
        // de sempre + a admissão escolhida no composer (estrita, ou padrão com
        // reconhecimento deliberado da degradação local conhecida).
        kind === 'direct'
          ? {
              question,
              enabled_providers: enabledProviders,
              source_text: null,
              kind: 'direct',
              // Direct Accepted Effective Model Choice V1 -- só quando há
              // escolha explícita (sem ela, o envio de sempre).
              ...(directRequestedModel !== undefined ? { requested_model: directRequestedModel } : {}),
            }
          : {
              question,
              enabled_providers: enabledProviders,
              source_text: sourceText,
              ...admission,
              // Council Accepted Effective Participant Model Choice V1 -- só
              // quando há escolha explícita (sem ela, o envio de sempre).
              ...(participantModelOverrides !== undefined
                ? { participant_model_overrides: participantModelOverrides }
                : {}),
            },
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
      } else if (
        error instanceof ApiError &&
        (error.code === 'council_prerequisites_missing' || error.code === 'council_readiness_changed')
      ) {
        const fresh = readinessFromDetails(error.details)
        setRejectedReadiness(fresh)
        setSubmission({
          phase: 'readiness_blocked',
          code: error.code,
          knownMissing: fresh === null ? null : fresh.summary === 'some_missing',
        })
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
        previewReadiness={apiClient.previewCouncilReadiness}
        rejectedReadiness={rejectedReadiness}
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

        {submission.phase === 'readiness_blocked' && (
          <p role="alert" className="notice notice--validation">
            {formatErrorCode(submission.code)}{' '}
            {submission.knownMissing === true
              ? 'Veja o aviso acima para decidir se quer perguntar mesmo assim.'
              : submission.knownMissing === false
                ? 'Agora não há configuração local ausente conhecida nas etapas do Conselho: você pode perguntar de novo.'
                : 'Você pode perguntar de novo; o servidor avalia a configuração local outra vez.'}
          </p>
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
