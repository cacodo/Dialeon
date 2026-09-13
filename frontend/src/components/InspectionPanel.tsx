// Inspeção opcional, lazy (Decision Delta secao 14): o payload de audit
// só é buscado quando o usuário pede. Falha aqui NUNCA apaga o detail já
// carregado -- é um estado de erro isolado, próprio deste componente.

import { useState } from 'react'
import { apiClient, ApiError } from '../api/client'
import type { RunAuditResponse } from '../api/types'
import { formatErrorCode } from '../api/formatting'
import { AccountingView } from './AccountingView'
import { ClaimsList } from './ClaimsList'
import { DeliberationOutcomes } from './DeliberationOutcomes'
import { JudgmentView } from './JudgmentView'
import { ParticipantsResponses } from './ParticipantsResponses'
import { SourceAnalysisView } from './SourceAnalysisView'

interface InspectionPanelProps {
  runId: string
}

type InspectionState =
  | { phase: 'collapsed' }
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'loaded'; audit: RunAuditResponse }

export function InspectionPanel({ runId }: InspectionPanelProps) {
  const [state, setState] = useState<InspectionState>({ phase: 'collapsed' })

  async function loadAudit() {
    setState({ phase: 'loading' })
    try {
      const audit = await apiClient.getRunAudit(runId)
      setState({ phase: 'loaded', audit })
    } catch (error) {
      const message = error instanceof ApiError ? formatErrorCode(error.code) : 'Erro inesperado.'
      setState({ phase: 'error', message })
    }
  }

  if (state.phase === 'collapsed') {
    return (
      <button type="button" className="inspection-panel__open" onClick={loadAudit}>
        Inspecionar execução
      </button>
    )
  }

  if (state.phase === 'loading') {
    return (
      <p aria-live="polite" role="status">
        Carregando detalhes da execução…
      </p>
    )
  }

  if (state.phase === 'error') {
    return (
      <div role="alert">
        <p>Não foi possível carregar a inspeção: {state.message}</p>
        <button type="button" onClick={loadAudit}>
          Tentar novamente
        </button>
      </div>
    )
  }

  const audit = state.audit

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
    <div className="inspection-panel">
      <section aria-labelledby="participants-heading">
        <h2 id="participants-heading">Participantes e respostas</h2>
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
            <ClaimsList
              claims={audit.claims}
              assessmentsByClaimId={assessmentsByClaimId}
            />
          </section>

          <section aria-labelledby="source-analysis-heading">
            <h2 id="source-analysis-heading">Análise da fonte</h2>
            <SourceAnalysisView sourceAnalysis={audit.source_analysis} claims={audit.claims} />
          </section>

          <section aria-labelledby="deliberation-heading">
            <h2 id="deliberation-heading">Deliberação</h2>
            <DeliberationOutcomes
              debateOutcome={audit.debate_outcome}
              judgeOutcome={audit.judge_outcome}
              editorOutcome={audit.editor_outcome}
            />
          </section>

          <section aria-labelledby="judgment-heading-wrapper">
            <h2 id="judgment-heading-wrapper">Julgamento</h2>
            <JudgmentView verdict={audit.judge_verdict} />
          </section>
        </>
      )}

      <section aria-labelledby="accounting-heading">
        <h2 id="accounting-heading">Consumo e custo</h2>
        <AccountingView
          accounting={audit.status === 'completed' ? audit.accounting : audit.round_result.accounting}
        />
      </section>
    </div>
  )
}
