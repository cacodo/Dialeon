// Direct Answer Execution V1 -- página de uma run DIRETA: a pergunta, a
// resposta de UM modelo (dita como tal, nunca como conclusão do conselho,
// consenso ou verificação), um resumo curto, as próximas ações e, sob
// demanda, só os fatos que existem pra ela (a única chamada ao provider).
// Nenhuma seção do Conselho (afirmações, juiz, fonte, avaliação) é mostrada,
// nem vazia.

import { Link } from 'react-router-dom'
import type {
  DirectCompletedRunResponse,
  DirectFailedRunResponse,
  DirectRunConfigPublic,
  DirectRunResponse,
  ModelResponsePublic,
} from '../api/types'
import {
  formatDateTime,
  formatEstimatedCost,
  formatExactCost,
  formatFailureCategory,
  formatFailureStage,
  formatModelIdentitySource,
  formatProviderName,
  formatRecordedDuration,
  formatTokenCount,
  splitAnswerParagraphs,
} from '../api/formatting'
import { buildDirectReuseState } from '../lib/reuseInput'
import { CopyAnswerButton } from './CopyAnswerButton'
import { ProviderExecutionPolicyView } from './ProviderExecutionPolicyView'

// Custo da ÚNICA chamada: desconhecido é desconhecido (nunca "0,00"); uma
// tentativa anterior incerta torna o valor conhecido parcial.
function directCost(response: ModelResponsePublic): string {
  return formatEstimatedCost(response.cost_usd, response.had_uncertain_prior_attempts)
}

function DirectNextActions({ config }: { config: DirectRunConfigPublic }) {
  return (
    <div className="run-actions">
      <Link to="/" state={buildDirectReuseState(config)} aria-describedby="reuse-description">
        Perguntar de novo
      </Link>
      <Link to="/">Nova pergunta</Link>
      <p id="reuse-description" className="sr-only">
        Perguntar de novo abre uma nova resposta direta com a mesma pergunta e o mesmo modelo, usando
        o modelo configurado agora. Nada do resultado anterior é enviado.
      </p>
    </div>
  )
}

function CallDetails({
  title,
  config,
  response,
  policy,
}: {
  title: string
  config: DirectRunConfigPublic
  response: ModelResponsePublic | null
  policy: DirectRunResponse['provider_execution_policy']
}) {
  return (
    <section aria-labelledby="direct-details-heading" className="direct-details">
      <h2 id="direct-details-heading">{title}</h2>
      <p className="direct-details__intro">
        Uma única chamada a um modelo, sem as etapas do conselho: nenhuma comparação entre modelos,
        avaliação do juiz ou análise de fonte.
      </p>
      <details>
        <summary>Ver detalhes da chamada</summary>
        <dl className="direct-details__facts">
          <dt>Provider</dt>
          <dd>{config.provider}</dd>
          <dt>Modelo solicitado</dt>
          <dd>
            {config.requested_model}{' '}
            <span className="direct-details__note">(padrão configurado quando a pergunta foi aceita)</span>
          </dd>
          {response !== null && (
            <>
              <dt>Modelo reportado</dt>
              <dd>
                {response.model}{' '}
                <span className="direct-details__note">
                  ({formatModelIdentitySource(response.model_identity_source)})
                </span>
              </dd>
              <dt>Tokens (entrada / saída)</dt>
              <dd>
                {formatTokenCount(response.usage?.input_tokens ?? null)} /{' '}
                {formatTokenCount(response.usage?.output_tokens ?? null)}
              </dd>
              <dt>Custo estimado (USD)</dt>
              <dd>
                {formatExactCost(response.cost_usd)}
                {response.pricing_provenance !== null && (
                  <span className="direct-details__note">
                    {' '}
                    (tabela {response.pricing_provenance.source_id}, {response.pricing_provenance.tier})
                  </span>
                )}
              </dd>
              <dt>Tentativas de transporte</dt>
              <dd>
                {response.attempts}
                {response.had_uncertain_prior_attempts && (
                  <span className="direct-details__note">
                    {' '}
                    (uma tentativa anterior pode ter chegado ao provider; o custo dela é desconhecido)
                  </span>
                )}
              </dd>
              <dt>Latência</dt>
              <dd>{response.latency_ms} ms</dd>
              <dt>Motivo de parada informado</dt>
              <dd>{response.provider_finish_reason ?? 'não informado'}</dd>
              {response.request_provenance !== null && (
                <>
                  <dt>Contrato do pedido</dt>
                  <dd>{response.request_provenance.contract_version}</dd>
                  <dt>Digest do pedido</dt>
                  <dd className="direct-details__digest">{response.request_provenance.request_digest}</dd>
                </>
              )}
              {response.error !== null && (
                <>
                  <dt>Erro informado pelo provider</dt>
                  <dd>
                    {formatFailureCategory(response.error.type)} — {response.error.message}
                  </dd>
                </>
              )}
            </>
          )}
        </dl>
        <ProviderExecutionPolicyView policy={policy} />
      </details>
    </section>
  )
}

function CompletedDirectAnswer({ run }: { run: DirectCompletedRunResponse }) {
  const model = formatProviderName(run.config.provider)
  const duration = formatRecordedDuration(run.started_at, run.completed_at)
  return (
    <>
      <section aria-labelledby="final-answer-heading" className="final-answer final-answer--direct">
        <div className="final-answer__header">
          {/* tabIndex -1: mesmo destino programático da página do Conselho. */}
          <h2 id="final-answer-heading" tabIndex={-1}>
            Resposta
          </h2>
          <CopyAnswerButton text={run.answer} />
        </div>
        <p className="direct-answer__label">
          Resposta direta de {model}: um único modelo, sem as etapas do conselho. Não é consenso
          nem verificação.
        </p>
        <div className="final-answer__text">
          {splitAnswerParagraphs(run.answer).map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </div>
      </section>

      <ul className="run-meta" aria-label="Resumo da resposta">
        <li>Modelo: {model}</li>
        <li>Custo estimado: {directCost(run.response)}</li>
        {duration !== null && <li>Duração: {duration}</li>}
        <li>
          Concluída em <time dateTime={run.completed_at}>{formatDateTime(run.completed_at)}</time>
        </li>
      </ul>

      <DirectNextActions config={run.config} />
      <CallDetails
        title="Como esta resposta foi produzida"
        config={run.config}
        response={run.response}
        policy={run.provider_execution_policy}
      />
    </>
  )
}

// M4 (revisão do Direct) -- o Dialeon só observa o SEU lado da chamada:
// nenhum status HTTP, tipo de erro, timeout ou texto vazio prova o que o
// modelo produziu (ou não). Sem resposta utilizável registrada, o texto diz
// só o que o Dialeon sabe, e depende apenas de ONDE a run parou -- nunca do
// tipo de erro: no estágio "provider" a chamada terminou e está registrada,
// sem resposta utilizável; na execução ou na gravação do desfecho, nenhuma
// resposta foi registrada. Mesma regra da CLI (app/cli/output.py).
function callWasRecorded(run: DirectFailedRunResponse): boolean {
  return run.failure_stage === 'provider'
}

function unconfirmedAnswerNote(run: DirectFailedRunResponse): string {
  return run.failure_stage === 'terminal_persistence'
    ? 'O modelo pode ter produzido uma resposta, mas o resultado não pôde ser gravado.'
    : 'Não é possível confirmar se o modelo chegou a produzir uma resposta.'
}

function failedMessage(run: DirectFailedRunResponse): string {
  if (run.failure_stage === 'execution' || run.failure_stage === 'terminal_persistence') {
    return formatFailureStage(run.failure_stage)
  }
  return run.message ?? 'A chamada ao modelo terminou sem resposta registrada.'
}

function FailedDirectRun({ run }: { run: DirectFailedRunResponse }) {
  const recorded = callWasRecorded(run)
  const model = formatProviderName(run.config.provider)
  return (
    <>
      <section aria-labelledby="direct-failed-heading" className="notice notice--error run-outcome">
        <h2 id="direct-failed-heading">{recorded ? 'Sem resposta utilizável' : 'Sem resposta registrada'}</h2>
        <p>{failedMessage(run)}</p>
        <p>
          {recorded
            ? 'O Dialeon não recebeu uma resposta utilizável'
            : 'Nenhuma resposta foi registrada'}
          , e nenhum outro modelo foi usado no lugar de {model}.
        </p>
        {!recorded && <p>{unconfirmedAnswerNote(run)}</p>}
        {run.response?.had_uncertain_prior_attempts && (
          <p>Uma tentativa anterior pode ter chegado ao provider; o custo dela é desconhecido.</p>
        )}
        <ul className="run-meta" aria-label="Resumo da tentativa">
          <li>Modelo: {formatProviderName(run.config.provider)}</li>
          {run.response !== null && <li>Custo estimado: {directCost(run.response)}</li>}
          {run.failed_at !== null && (
            <li>
              Encerrada em <time dateTime={run.failed_at}>{formatDateTime(run.failed_at)}</time>
            </li>
          )}
        </ul>
      </section>
      <DirectNextActions config={run.config} />
      {/* Sem resposta, o título nunca fala de "esta resposta" (mesmo título
          das falhas do Conselho). */}
      <CallDetails
        title="O que aconteceu nesta pergunta"
        config={run.config}
        response={run.response}
        policy={run.provider_execution_policy}
      />
    </>
  )
}

export function DirectRunView({
  run,
  refreshing,
  stillWithoutOutcome,
  refreshError,
  onRefresh,
}: {
  run: DirectRunResponse
  refreshing: boolean
  stillWithoutOutcome: boolean
  refreshError: string | null
  onRefresh: () => void
}) {
  return (
    <>
      {/* A pergunta é contexto pra resposta (mesma hierarquia da página do
          Conselho). */}
      <h1 className="run-detail__question-label">Pergunta</h1>
      <p className="run-detail__question-text">{run.config.question}</p>

      {run.status === 'completed' && <CompletedDirectAnswer run={run} />}
      {run.status === 'failed' && <FailedDirectRun run={run} />}
      {run.status === 'running' && (
        <>
          <section aria-labelledby="running-heading" className="notice notice--neutral run-outcome">
            <h2 id="running-heading">Sem desfecho registrado</h2>
            <p>Resposta direta de {formatProviderName(run.config.provider)}, iniciada em {formatDateTime(run.started_at)}.</p>
            <p>
              Nenhum desfecho terminal foi registrado. A chamada pode ainda estar em andamento ou ter
              sido interrompida; os dois casos são indistinguíveis a partir deste registro.
            </p>
            <p>
              <button type="button" className="run-detail__refresh" onClick={onRefresh} disabled={refreshing}>
                {refreshing ? 'Atualizando…' : 'Atualizar registro'}
              </button>
            </p>
            {stillWithoutOutcome && <p role="status">Continua sem desfecho registrado.</p>}
            {refreshError !== null && (
              <p role="alert" className="notice notice--error">
                {refreshError}
              </p>
            )}
          </section>
          <DirectNextActions config={run.config} />
        </>
      )}
    </>
  )
}
