// Cross-Channel Reconciliation V1 -- visibilidade humana da classificação
// DETERMINÍSTICA (sem LLM) de RELACIONAMENTO entre o canal Judge e o
// canal Source Analysis por claim corrente. AUDIT-ONLY na origem, mas o
// relacionamento em si já é refletido na resposta final pelo Editor --
// esta view só inspeciona o registro estruturado por trás disso, nunca
// recalcula nada.
//
// CHANNEL RELATIONSHIP != JUDGE VERDICT != TRUTH: os rótulos aqui
// (via formatChannelRelationship/formatSourceChannelState) descrevem só
// COMO os dois canais se relacionam, nunca qual dos dois está certo, e
// nunca um terceiro veredito de verdade.
//
// Associação claim<->outcome é SEMPRE por claim_id estruturado (nunca
// por comparação de texto); judge_verdict_id/source_claim_result_ids são
// expostos como referências de auditoria (IDs), nunca duplicando texto
// já disponível em claims/judge_verdict/source_analysis no mesmo audit.

import type { ClaimPublic, SourceJudgeReconciliationResultPublic } from '../api/types'
import { formatChannelRelationship, formatSourceChannelState } from '../api/formatting'

interface ReconciliationViewProps {
  reconciliation: SourceJudgeReconciliationResultPublic | null
  claims: ClaimPublic[]
}

export function ReconciliationView({ reconciliation, claims }: ReconciliationViewProps) {
  if (reconciliation === null) {
    return (
      <p>
        Execução anterior a este recurso — nenhuma reconciliação estruturada foi computada.
      </p>
    )
  }

  if (reconciliation.status === 'judge_unavailable') {
    return (
      <>
        <p>
          O juiz não ficou disponível nesta execução — o relacionamento entre canais não é
          comparável, mas o estado da fonte por afirmação continua registrado abaixo.
        </p>
        <ReconciliationOutcomeList reconciliation={reconciliation} claims={claims} />
      </>
    )
  }

  if (reconciliation.claim_outcomes.length === 0) {
    return <p>Nenhuma afirmação corrente para reconciliar nesta execução.</p>
  }

  return <ReconciliationOutcomeList reconciliation={reconciliation} claims={claims} />
}

function ReconciliationOutcomeList({
  reconciliation,
  claims,
}: {
  reconciliation: SourceJudgeReconciliationResultPublic
  claims: ClaimPublic[]
}) {
  if (reconciliation.claim_outcomes.length === 0) {
    return <p>Nenhuma afirmação corrente para reconciliar nesta execução.</p>
  }

  const claimsById = new Map(claims.map((c) => [c.id, c]))

  return (
    <ul className="reconciliation-view claims-list">
      {reconciliation.claim_outcomes.map((outcome) => {
        const claim = claimsById.get(outcome.claim_id)
        return (
          <li key={outcome.claim_id} className="reconciliation-view__item claims-list__item">
            <p className="reconciliation-view__claim-text">
              {claim
                ? claim.text
                : `Afirmação não encontrada nos dados desta execução (id: ${outcome.claim_id}).`}
            </p>
            <p className="reconciliation-view__relationship">
              {formatChannelRelationship(outcome.channel_relationship)}
            </p>
            <p className="reconciliation-view__source-state">
              {formatSourceChannelState(outcome.source_state)}
            </p>
            <dl className="reconciliation-view__references">
              <dt>Avaliação do juiz referenciada</dt>
              <dd>{outcome.judge_verdict_id ?? '—'}</dd>
              <dt>Entradas da análise da fonte referenciadas</dt>
              <dd>
                {outcome.source_claim_result_ids.length > 0
                  ? outcome.source_claim_result_ids.join(', ')
                  : '—'}
              </dd>
            </dl>
          </li>
        )
      })}
    </ul>
  )
}
