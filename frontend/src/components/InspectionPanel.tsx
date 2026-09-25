// Inspeção opcional, lazy (Decision Delta secao 14): o payload de audit
// só é buscado quando o usuário pede. Falha aqui NUNCA apaga o detail já
// carregado -- é um estado de erro isolado, próprio deste componente.
//
// Hierarquia de inspeção (UI Slice -- inspeção como explicação
// product-facing, não dump de auditoria) -- áreas semânticas
// distinguíveis, na ordem em que aparecem:
//   1. Notas da execução -- desvios/degradação que afetam interpretação
//      (nunca escondidos atrás de disclosure extra: aparecem assim que a
//      inspeção é aberta).
//   2. Perspectivas dos participantes -- o que cada participante
//      contribuiu, por rodada.
//   3. Afirmações -- claim-centered semantic inspection (UI Slice):
//      cada `ClaimPublic.id` vira UMA unidade product-facing (ver
//      ClaimInspectionList/api/claimInspectionModel.ts), reunindo debate
//      + juiz + fonte + reconciliação pra AQUELA claim específica --
//      nunca listas paralelas repetindo a mesma claim por canal.
//   4. Avaliação do juiz -- só o raciocínio/estado GLOBAL do juiz; as
//      avaliações por claim já vivem dentro de cada unidade acima.
//   5. Auditoria técnica -- identidade bruta de provider/modelo,
//      política de execução, detalhamento completo de consumo, lista
//      técnica completa de claims e problemas de integridade do audit
//      (referências malformadas/pendentes). Fica DELIBERADAMENTE atrás
//      de um disclosure PRÓPRIO (mais profundo que a inspeção em si) --
//      nunca domina a experiência inicial de quem só quer entender como
//      o debate levou à resposta.
//
// Nenhuma dessas seções decide "verdade" -- perspectiva de participante
// != avaliação do juiz, suporte/consenso != verdade, fonte fornecida !=
// verificação factual, identidade de modelo != autoridade epistêmica.
// Essas distinções já vivem nos componentes reutilizados abaixo (nenhuma
// duplicada aqui).

import { useState } from 'react'
import { apiClient, ApiError } from '../api/client'
import {
  buildClaimInspectionModel,
  type AmbiguousClaimGroup,
  type AuditIntegrityIssue,
  type QuarantinedAssessment,
  type QuarantinedReconciliationOutcome,
  type QuarantinedSourceResult,
} from '../api/claimInspectionModel'
import type {
  AccountingSummary,
  ClaimAssessmentPublic,
  ClaimPublic,
  ClaimReconciliationOutcomePublic,
  ModelResponsePublic,
  ProviderExecutionPolicy,
  RoundAccountingPublic,
  RunAuditResponse,
} from '../api/types'
import {
  formatAuditFragmentOmittedReason,
  UNSAFE_INTEGER_FIDELITY_NOTICE,
  formatChannelRelationship,
  formatClaimVerdict,
  formatDateTime,
  formatErrorCode,
  formatEstimatedCost,
  formatExactCost,
  formatModelIdentitySource,
  formatPricingCanonicalModelId,
  formatSourceChannelState,
  formatSourceRejectionReason,
  formatSourceRelation,
  formatTokenCount,
  formatUsageRecordPresence,
} from '../api/formatting'
import { containsUnsafeInteger } from '../lib/numericFidelity'
import { AccountingView } from './AccountingView'
import { ClaimInspectionList } from './ClaimInspectionList'
import { ClaimsList } from './ClaimsList'
import { DeliberationOutcomes } from './DeliberationOutcomes'
import { JudgmentView } from './JudgmentView'
import { ParticipantsResponses } from './ParticipantsResponses'
import { ProviderExecutionPolicyView } from './ProviderExecutionPolicyView'
import { ReconciliationView } from './ReconciliationView'
import { SourceAnalysisView } from './SourceAnalysisView'

interface InspectionPanelProps {
  runId: string
}

// Estado do FETCH do audit -- deliberadamente separado de `expanded`
// (ver InspectionPanel abaixo): colapsar a inspeção NUNCA descarta o
// audit já carregado, só oculta a apresentação. Reabrir depois de
// colapsar reusa exatamente o mesmo audit em memória, sem novo request
// (`loadAudit` só é chamado quando `phase` ainda é `'idle'`).
type AuditState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'loaded'; audit: RunAuditResponse }

// Auditoria técnica -- disclosure PRÓPRIO, colapsado por padrão, mais
// profundo que a inspeção em si (a inspeção já é, ela mesma, opcional --
// isto é opcional DENTRO do opcional). Reusa inteiramente AccountingView/
// ProviderExecutionPolicyView/ClaimsList -- nenhuma segunda
// representação de audit criada do zero. `claims`/`reconciliationOutcomes`
// null (run insufficient_quorum, sem claims/reconciliação) omitem as
// subseções correspondentes -- nunca um placeholder vazio forçado.
function TechnicalAudit({
  accounting,
  policy,
  claims,
  assessmentsByClaimId,
  reconciliationOutcomes,
  integrityIssues,
  ambiguousClaims,
  quarantinedAssessments,
  quarantinedSourceResults,
  quarantinedReconciliationOutcomes,
  participantResponses,
}: {
  accounting: AccountingSummary | RoundAccountingPublic
  policy: ProviderExecutionPolicy | null
  claims: ClaimPublic[] | null
  assessmentsByClaimId: Map<string, ClaimAssessmentPublic[]>
  reconciliationOutcomes: ClaimReconciliationOutcomePublic[] | null
  integrityIssues: AuditIntegrityIssue[]
  ambiguousClaims: AmbiguousClaimGroup[]
  quarantinedAssessments: QuarantinedAssessment[]
  quarantinedSourceResults: QuarantinedSourceResult[]
  quarantinedReconciliationOutcomes: QuarantinedReconciliationOutcome[]
  participantResponses: ModelResponsePublic[]
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <section aria-labelledby="technical-audit-heading" className="inspection-panel__technical">
      <h2 id="technical-audit-heading">Auditoria técnica</h2>
      <p className="inspection-panel__technical-hint">
        Identidade bruta de provider/modelo, configuração de execução e o detalhamento completo de
        consumo — não necessários pra interpretar a resposta, mas continuam inspecionáveis aqui.
      </p>
      <button
        type="button"
        className="inspection-panel__technical-toggle"
        aria-expanded={expanded}
        aria-controls="inspection-panel-technical-panel"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? 'Ocultar auditoria técnica' : 'Ver auditoria técnica'}
      </button>
      {expanded && (
        <div id="inspection-panel-technical-panel">
          <h3>Consumo e custo</h3>
          <AccountingView accounting={accounting} />

          <h3>Política de execução do provider</h3>
          <ProviderExecutionPolicyView policy={policy} />

          {participantResponses.length > 0 && (
            <>
              <h3>Respostas dos participantes — registros técnicos</h3>
              <p className="inspection-panel__technical-hint">
                Um registro por resposta (distinto do consumo AGREGADO acima) -- identidade bruta
                de modelo, erro completo e proveniência de request/preço, nunca inferidos quando
                não registrados.
              </p>
              <dl className="participant-technical-records">
                {participantResponses.map((response, index) => (
                  <div
                    // Chave de APRESENTAÇÃO, nunca uma identidade
                    // persistida -- posição na lista JÁ combinada
                    // inicial+crítica, colisão-segura mesmo se o mesmo
                    // response.id aparecesse nas duas rodadas.
                    // response.id continua exibido verbatim abaixo, só
                    // como CONTEÚDO.
                    key={`participant-response-${index}`}
                    className="participant-technical-records__row"
                  >
                    <dt>id</dt>
                    <dd>{response.id}</dd>
                    <dt>provider</dt>
                    <dd>{response.provider}</dd>
                    <dt>Rodada</dt>
                    <dd>{response.round_number}</dd>
                    <dt>Modelo solicitado</dt>
                    <dd>{response.requested_model}</dd>
                    <dt>Modelo efetivo</dt>
                    <dd>{response.model}</dd>
                    <dt>Identidade do modelo</dt>
                    <dd>{formatModelIdentitySource(response.model_identity_source)}</dd>
                    <dt>Status</dt>
                    <dd>{response.status === 'success' ? 'Sucesso' : 'Erro'}</dd>
                    <dt>Registro de uso</dt>
                    <dd>{formatUsageRecordPresence(response.usage)}</dd>
                    <dt>Tokens de entrada</dt>
                    <dd>{formatTokenCount(response.usage?.input_tokens ?? null)}</dd>
                    <dt>Tokens de saída</dt>
                    <dd>{formatTokenCount(response.usage?.output_tokens ?? null)}</dd>
                    <dt>Custo estimado</dt>
                    <dd>{formatEstimatedCost(response.cost_usd, false)}</dd>
                    <dt>Custo exato (USD, valor persistido)</dt>
                    <dd>{formatExactCost(response.cost_usd)}</dd>
                    {response.pricing_provenance ? (
                      <>
                        <dt>Proveniência de preço — fonte</dt>
                        <dd>{response.pricing_provenance.source_id}</dd>
                        <dt>Proveniência de preço — camada</dt>
                        <dd>{response.pricing_provenance.tier}</dd>
                        <dt>Proveniência de preço — modelo canônico</dt>
                        <dd>
                          {formatPricingCanonicalModelId(
                            response.pricing_provenance.canonical_model_id,
                          )}
                        </dd>
                        <dt>Proveniência de preço — taxa de entrada (USD/milhão de tokens)</dt>
                        <dd>{response.pricing_provenance.input_rate_usd_per_million_tokens}</dd>
                        <dt>Proveniência de preço — taxa de saída (USD/milhão de tokens)</dt>
                        <dd>{response.pricing_provenance.output_rate_usd_per_million_tokens}</dd>
                      </>
                    ) : (
                      <>
                        <dt>Proveniência de preço</dt>
                        <dd>não registrada (sem cálculo de preço associado a esta resposta)</dd>
                      </>
                    )}
                    <dt>Latência</dt>
                    <dd>{response.latency_ms} ms</dd>
                    <dt>Tentativas</dt>
                    <dd>{response.attempts}</dd>
                    <dt>Tipo de erro</dt>
                    <dd>{response.error ? response.error.type : '—'}</dd>
                    <dt>Mensagem de erro</dt>
                    <dd>{response.error ? response.error.message : '—'}</dd>
                    <dt>Retryable</dt>
                    <dd>{response.error ? (response.error.retryable ? 'Sim' : 'Não') : '—'}</dd>
                    <dt>Tentativas anteriores incertas</dt>
                    <dd>{response.had_uncertain_prior_attempts ? 'Sim' : 'Não'}</dd>
                    <dt>Motivo de parada do provider</dt>
                    <dd>{response.provider_finish_reason ?? '—'}</dd>
                    <dt>Proveniência do request</dt>
                    <dd>
                      {response.request_provenance
                        ? `${response.request_provenance.contract_version} (${response.request_provenance.request_digest})`
                        : '—'}
                    </dd>
                    <dt>Criado em</dt>
                    <dd>
                      <time dateTime={response.created_at}>{formatDateTime(response.created_at)}</time>{' '}
                      ({response.created_at})
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}

          {claims !== null && (
            <>
              <h3>Afirmações — lista técnica completa</h3>
              <ClaimsList claims={claims} assessmentsByClaimId={assessmentsByClaimId} />
            </>
          )}

          {reconciliationOutcomes !== null && reconciliationOutcomes.length > 0 && (
            <>
              <h3>Referências brutas de reconciliação</h3>
              <dl className="inspection-panel__technical-refs">
                {reconciliationOutcomes.map((outcome, index) => (
                  <div key={index} className="inspection-panel__technical-refs-row">
                    <dt>claim_id</dt>
                    <dd>{outcome.claim_id}</dd>
                    <dt>judge_verdict_id</dt>
                    <dd>{outcome.judge_verdict_id ?? '—'}</dd>
                    <dt>source_claim_result_ids</dt>
                    <dd>
                      {outcome.source_claim_result_ids.length > 0
                        ? outcome.source_claim_result_ids.join(', ')
                        : '—'}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}

          {ambiguousClaims.length > 0 && (
            <>
              <h3>Identidade de claim ambígua</h3>
              <p className="inspection-panel__technical-hint">
                Registros de claim que compartilham o mesmo id -- nenhum vira unidade semântica;
                nenhum "vence" sobre os outros. Todos os registros originais continuam abaixo.
              </p>
              <ul className="inspection-panel__quarantine-list">
                {ambiguousClaims.map((group) => (
                  <li key={group.claimId}>
                    <p>
                      claim_id={group.claimId} — {group.claims.length} registros
                    </p>
                    <ul>
                      {group.claims.map((claim, index) => (
                        <li key={index}>{claim.text}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          )}

          {quarantinedAssessments.length > 0 && (
            <>
              <h3>Avaliações do juiz em quarentena</h3>
              <p className="inspection-panel__technical-hint">
                Registros originais preservados, mas que não viraram uma conclusão semântica do
                juiz pra nenhuma afirmação (ver motivo de cada um).
              </p>
              <ul className="inspection-panel__quarantine-list">
                {quarantinedAssessments.map((quarantined, index) => (
                  <li key={index}>
                    <p>
                      claim_id={quarantined.assessment.claim_id} —{' '}
                      {formatClaimVerdict(quarantined.assessment.verdict)}
                    </p>
                    <p>{quarantined.assessment.explanation}</p>
                    <p className="inspection-panel__technical-hint">Motivo: {quarantined.reason}</p>
                  </li>
                ))}
              </ul>
            </>
          )}

          {quarantinedSourceResults.length > 0 && (
            <>
              <h3>Resultados de fonte em quarentena</h3>
              <p className="inspection-panel__technical-hint">
                Registros originais preservados, mas que não entraram em nenhuma unidade de claim
                (ver motivo de cada um).
              </p>
              <ul className="inspection-panel__quarantine-list">
                {quarantinedSourceResults.map((quarantined, index) => (
                  <li key={index}>
                    <p>
                      id={quarantined.entry.id} — claim_id={quarantined.entry.claim_id ?? '—'}
                    </p>
                    {quarantined.entry.kind === 'relation' ? (
                      <p>
                        {formatSourceRelation(quarantined.entry.relation)}
                        {quarantined.entry.excerpt !== null
                          ? ` — “${quarantined.entry.excerpt}”`
                          : ''}
                      </p>
                    ) : (
                      <>
                        <p>{formatSourceRejectionReason(quarantined.entry.reason)}</p>
                        {quarantined.entry.raw_entry !== null && (
                          <p className="inspection-panel__technical-hint">
                            raw_entry: {JSON.stringify(quarantined.entry.raw_entry)}
                          </p>
                        )}
                        {containsUnsafeInteger(quarantined.entry.raw_entry) && (
                          <p className="inspection-panel__technical-hint">
                            {UNSAFE_INTEGER_FIDELITY_NOTICE}
                          </p>
                        )}
                        {quarantined.entry.raw_entry_omitted_reason !== null && (
                          <p className="inspection-panel__technical-hint">
                            {formatAuditFragmentOmittedReason(quarantined.entry.raw_entry_omitted_reason)}
                          </p>
                        )}
                      </>
                    )}
                    <p className="inspection-panel__technical-hint">Motivo: {quarantined.reason}</p>
                  </li>
                ))}
              </ul>
            </>
          )}

          {quarantinedReconciliationOutcomes.length > 0 && (
            <>
              <h3>Outcomes de reconciliação em quarentena</h3>
              <p className="inspection-panel__technical-hint">
                Registros originais preservados, mas cujo envelope de referência não passou na
                validação de integridade (ver motivos de cada um) -- nunca anexados como
                relacionamento product-facing.
              </p>
              <ul className="inspection-panel__quarantine-list">
                {quarantinedReconciliationOutcomes.map((quarantined, index) => (
                  <li key={index}>
                    <p>
                      claim_id={quarantined.outcome.claim_id} — judge_verdict_id=
                      {quarantined.outcome.judge_verdict_id ?? '—'}
                    </p>
                    <p>
                      source_claim_result_ids:{' '}
                      {quarantined.outcome.source_claim_result_ids.length > 0
                        ? quarantined.outcome.source_claim_result_ids.join(', ')
                        : '—'}
                    </p>
                    <p>
                      {formatChannelRelationship(quarantined.outcome.channel_relationship)} /{' '}
                      {formatSourceChannelState(quarantined.outcome.source_state)}
                    </p>
                    <p className="inspection-panel__technical-hint">
                      Motivos: {quarantined.reasons.join(', ')}
                    </p>
                  </li>
                ))}
              </ul>
            </>
          )}

          {integrityIssues.length > 0 && (
            <>
              <h3>Problemas de integridade do audit</h3>
              <p className="inspection-panel__technical-hint">
                Referências explícitas que não puderam ser resolvidas neste audit -- nunca
                anexadas a uma afirmação adivinhada, nunca reinterpretadas como desacordo,
                ausência ou rejeição semântica de nenhum canal.
              </p>
              <ul className="inspection-panel__integrity-issues">
                {integrityIssues.map((issue, index) => (
                  <li key={index}>{issue.detail}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  )
}

function InspectionContent({ audit }: { audit: RunAuditResponse }) {
  if (audit.status !== 'completed' && audit.status !== 'insufficient_quorum') {
    // T02.4 -- este componente só é montado (ver RunDetail.tsx) pra runs
    // com desfecho terminal auditável; "running"/"failed" nunca chegam
    // aqui na prática (nenhum claim/attempt/verdict pra inspecionar) --
    // guarda defensiva só pra manter o narrowing de tipo abaixo.
    return null
  }

  const claimModel =
    audit.status === 'completed'
      ? buildClaimInspectionModel({
          claims: audit.claims,
          judgeVerdict: audit.judge_verdict,
          sourceAnalysis: audit.source_analysis,
          reconciliation: audit.reconciliation,
        })
      : null

  // Array por claim_id, nunca `new Map(pairs)` -- essa construção
  // colapsaria silenciosamente pra "a última avaliação que apareceu no
  // array" se 2+ avaliações citassem a mesma claim_id (repair pós-
  // revisão adversarial nº4: nem a lista técnica completa de claims
  // pode fingir que uma ambiguidade não existe). `ClaimsList` abaixo
  // renderiza TODAS as avaliações de cada claim_id, nunca escolhe uma.
  const assessmentsByClaimId = new Map<string, ClaimAssessmentPublic[]>()
  if (audit.status === 'completed' && audit.judge_verdict) {
    for (const assessment of audit.judge_verdict.claim_assessments) {
      const group = assessmentsByClaimId.get(assessment.claim_id)
      if (group) group.push(assessment)
      else assessmentsByClaimId.set(assessment.claim_id, [assessment])
    }
  }

  // Fonte fornecida E análise concluída -- distinto de "sem fonte" e de
  // "fonte fornecida mas pulada/falhada" (esses dois últimos já são
  // comunicados uma única vez, a nível de execução, por
  // `SourceAnalysisView`, nunca repetidos por claim).
  const sourceRelationAvailable =
    audit.status === 'completed' &&
    audit.source_analysis !== null &&
    audit.source_analysis.skipped_reason === null

  // Reconciliação roda mesmo sem fonte (produzindo um estado "não
  // comparável"/"not_supplied" honesto) -- independente de
  // `sourceRelationAvailable`. `reconciliation === null` é só o caso
  // histórico (execução anterior a este recurso), nunca inferido.
  const reconciliationAvailable = audit.status === 'completed' && audit.reconciliation !== null

  // Registros de TODAS as rodadas -- usados só pela Auditoria técnica
  // (ver TechnicalAudit abaixo); a apresentação product-facing continua
  // separando inicial/crítica explicitamente via `ParticipantsResponses`.
  const participantResponses: ModelResponsePublic[] =
    audit.status === 'completed'
      ? [...audit.initial_round.responses, ...(audit.critique_round?.responses ?? [])]
      : audit.round_result.responses

  return (
    <div className="inspection-panel__content">
      {audit.status === 'completed' && (
        // Desvios/degradação que afetam interpretação (debate, juiz,
        // editor) -- a primeira coisa visível ao abrir a inspeção, nunca
        // atrás de disclosure extra. Quando nada de material aconteceu,
        // `DeliberationOutcomes` já diz isso explicitamente (nunca um
        // silêncio ambíguo).
        <section aria-labelledby="execution-notes-heading">
          <h2 id="execution-notes-heading">Notas da execução</h2>
          <DeliberationOutcomes
            debateOutcome={audit.debate_outcome}
            judgeOutcome={audit.judge_outcome}
            editorOutcome={audit.editor_outcome}
          />
        </section>
      )}

      <section aria-labelledby="participants-heading">
        <h2 id="participants-heading">Perspectivas dos participantes</h2>
        <p className="participant-responses-caveat">
          Estas são perspectivas individuais dos participantes, não a conclusão do Dialeon. As
          revisões mostram uma etapa posterior do debate, não uma correção comprovada.
        </p>
        <ParticipantsResponses
          responses={
            audit.status === 'completed' ? audit.initial_round.responses : audit.round_result.responses
          }
          round="initial"
        />
        {audit.status === 'completed' && audit.critique_round && (
          <ParticipantsResponses responses={audit.critique_round.responses} round="critique" />
        )}
      </section>

      {audit.status === 'completed' && claimModel && (
        <>
          <section aria-labelledby="claims-heading">
            <h2 id="claims-heading">Afirmações</h2>
            <ClaimInspectionList
              model={claimModel}
              sourceRelationAvailable={sourceRelationAvailable}
              reconciliationAvailable={reconciliationAvailable}
            />
            {audit.source_analysis !== null && (
              <SourceAnalysisView
                sourceAnalysis={audit.source_analysis}
                unattributedRejectedSourceEntries={claimModel.unattributedRejectedSourceEntries}
              />
            )}
            <ReconciliationView
              reconciliation={audit.reconciliation}
              envelopeCoherent={claimModel.reconciliationEnvelopeCoherent}
            />
          </section>

          <section aria-labelledby="judgment-heading-wrapper">
            <h2 id="judgment-heading-wrapper">Avaliação do juiz</h2>
            <JudgmentView verdict={audit.judge_verdict} />
          </section>
        </>
      )}

      <TechnicalAudit
        accounting={audit.status === 'completed' ? audit.accounting : audit.round_result.accounting}
        policy={audit.provider_execution_policy}
        claims={audit.status === 'completed' ? audit.claims : null}
        assessmentsByClaimId={assessmentsByClaimId}
        participantResponses={participantResponses}
        reconciliationOutcomes={
          audit.status === 'completed' && audit.reconciliation !== null
            ? audit.reconciliation.claim_outcomes
            : null
        }
        integrityIssues={claimModel?.integrityIssues ?? []}
        ambiguousClaims={claimModel?.ambiguousClaims ?? []}
        quarantinedAssessments={claimModel?.quarantinedAssessments ?? []}
        quarantinedSourceResults={claimModel?.quarantinedSourceResults ?? []}
        quarantinedReconciliationOutcomes={claimModel?.quarantinedReconciliationOutcomes ?? []}
      />
    </div>
  )
}

export function InspectionPanel({ runId }: InspectionPanelProps) {
  const [audit, setAudit] = useState<AuditState>({ phase: 'idle' })
  // Reversível: colapsar só oculta a apresentação, nunca descarta o
  // audit já carregado (`audit` acima continua `'loaded'`) -- reabrir
  // depois de colapsar nunca dispara um novo request.
  const [expanded, setExpanded] = useState(false)

  async function loadAudit() {
    setAudit({ phase: 'loading' })
    try {
      const data = await apiClient.getRunAudit(runId)
      setAudit({ phase: 'loaded', audit: data })
    } catch (error) {
      const message = error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.'
      setAudit({ phase: 'error', message })
    }
  }

  function handleToggle() {
    if (expanded) {
      setExpanded(false)
      return
    }
    setExpanded(true)
    if (audit.phase === 'idle') {
      loadAudit()
    }
  }

  return (
    <div className="inspection-panel">
      <button
        type="button"
        className="inspection-panel__open"
        onClick={handleToggle}
        aria-expanded={expanded}
        aria-controls="inspection-panel-content"
      >
        {expanded ? 'Ocultar inspeção' : 'Inspecionar execução'}
      </button>

      {expanded && (
        <div id="inspection-panel-content">
          {audit.phase === 'loading' && (
            <p aria-live="polite" role="status">
              Carregando detalhes da execução…
            </p>
          )}

          {audit.phase === 'error' && (
            <div role="alert">
              <p>Não foi possível carregar a inspeção: {audit.message}</p>
              <button type="button" onClick={loadAudit}>
                Tentar novamente
              </button>
            </div>
          )}

          {audit.phase === 'loaded' && <InspectionContent audit={audit.audit} />}
        </div>
      )}
    </div>
  )
}
