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
//   3. Afirmações -- o que emergiu e mudou ao longo do debate.
//   4. Relação com a fonte -- só existe quando uma fonte foi
//      efetivamente fornecida (`source_analysis !== null`); nunca forçada
//      quando o dado não existe.
//   5. Avaliação do juiz.
//   6. Auditoria técnica -- identidade bruta de provider/modelo,
//      política de execução, detalhamento completo de consumo. Fica
//      DELIBERADAMENTE atrás de um disclosure PRÓPRIO (mais profundo que
//      a inspeção em si) -- nunca domina a experiência inicial de quem
//      só quer entender como o debate levou à resposta.
//
// Nenhuma dessas seções decide "verdade" -- perspectiva de participante
// != avaliação do juiz, suporte/consenso != verdade, fonte fornecida !=
// verificação factual, identidade de modelo != autoridade epistêmica.
// Essas distinções já vivem nos componentes reutilizados abaixo (nenhuma
// duplicada aqui).

import { useState } from 'react'
import { apiClient, ApiError } from '../api/client'
import type {
  AccountingSummary,
  ProviderExecutionPolicy,
  RoundAccountingPublic,
  RunAuditResponse,
} from '../api/types'
import { formatErrorCode } from '../api/formatting'
import { AccountingView } from './AccountingView'
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
// ProviderExecutionPolicyView -- nenhuma segunda representação de audit.
function TechnicalAudit({
  accounting,
  policy,
}: {
  accounting: AccountingSummary | RoundAccountingPublic
  policy: ProviderExecutionPolicy | null
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

  const assessmentsByClaimId =
    audit.status === 'completed' && audit.judge_verdict
      ? new Map(audit.judge_verdict.claim_assessments.map((a) => [a.claim_id, a]))
      : new Map()

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
        <ParticipantsResponses
          responses={
            audit.status === 'completed' ? audit.initial_round.responses : audit.round_result.responses
          }
          title="Rodada inicial"
        />
        {audit.status === 'completed' && audit.critique_round && (
          <ParticipantsResponses responses={audit.critique_round.responses} title="Rodada de crítica" />
        )}
      </section>

      {audit.status === 'completed' && (
        <>
          <section aria-labelledby="claims-heading">
            <h2 id="claims-heading">Afirmações</h2>
            <ClaimsList claims={audit.claims} assessmentsByClaimId={assessmentsByClaimId} />
          </section>

          {audit.source_analysis !== null && (
            // Só existe quando uma fonte foi de fato fornecida -- nunca
            // forçada com um placeholder vazio pra runs sem fonte (ver
            // SourceAnalysisOutcome, app/presentation/schemas.py: `null`
            // aqui significa "nenhuma fonte foi fornecida", distinto de
            // um objeto com `skipped_reason` preenchido -- esse segundo
            // caso É degradação e continua visível dentro da seção).
            <section aria-labelledby="source-relationship-heading">
              <h2 id="source-relationship-heading">Relação com a fonte</h2>
              <h3>O que a fonte indica</h3>
              <SourceAnalysisView sourceAnalysis={audit.source_analysis} claims={audit.claims} />
              <h3>Relação com o julgamento</h3>
              <ReconciliationView reconciliation={audit.reconciliation} claims={audit.claims} />
            </section>
          )}

          <section aria-labelledby="judgment-heading-wrapper">
            <h2 id="judgment-heading-wrapper">Avaliação do juiz</h2>
            <JudgmentView verdict={audit.judge_verdict} />
          </section>
        </>
      )}

      <TechnicalAudit
        accounting={audit.status === 'completed' ? audit.accounting : audit.round_result.accounting}
        policy={audit.provider_execution_policy}
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
