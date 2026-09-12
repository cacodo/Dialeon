// Representação MVP de claims: lista/cards expansíveis, relações por ID
// resolvidas via JOIN em memória sobre o MESMO payload de audit já
// recebido (nenhuma lógica de negócio nova, nenhuma chamada extra).
// Nunca calcula truth score nem reinterpreta supporting_model_ratio como
// confidence (CONSENSUS != TRUTH).
//
// O denominador de suporte de CADA claim é claim.total_models_in_round
// -- nunca o total da rodada inicial da execução como um todo. Uma claim
// introduzida na crítica (ou revisada depois) pode ter um total_models_in_round
// diferente de initial_round.total_providers; usar o total errado
// alteraria a semântica de um dado já persistido (UI NÃO PODE ALTERAR A
// SEMÂNTICA DO DADO PERSISTIDO).

import { useState } from 'react'
import type { ClaimAssessmentPublic, ClaimPublic } from '../api/types'
import { formatClaimVerdict, formatSupportRatio } from '../api/formatting'

interface ClaimsListProps {
  claims: ClaimPublic[]
  assessmentsByClaimId: Map<string, ClaimAssessmentPublic>
}

function ClaimCard({
  claim,
  assessment,
  claimsById,
}: {
  claim: ClaimPublic
  assessment: ClaimAssessmentPublic | undefined
  claimsById: Map<string, ClaimPublic>
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <li className="claims-list__item">
      <button
        type="button"
        className="claims-list__toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {claim.text}
      </button>
      {expanded && (
        <div className="claims-list__details">
          <p>
            {formatSupportRatio(claim.supporting_model_response_ids.length, claim.total_models_in_round)}
          </p>
          <ul className="claims-list__supporters">
            {claim.supporting_model_response_ids.map((support) => (
              <li key={support.model_response_id}>
                {support.provider} / {support.model}
              </li>
            ))}
          </ul>
          {claim.parent_claim_id && claimsById.has(claim.parent_claim_id) && (
            <p>Revisão de: “{claimsById.get(claim.parent_claim_id)?.text}”</p>
          )}
          {claim.merged_from_claim_ids.length > 0 && (
            <p>
              Fusão de {claim.merged_from_claim_ids.length} afirmação
              {claim.merged_from_claim_ids.length === 1 ? '' : 'ões'} anteriores.
            </p>
          )}
          {assessment && (
            <p className="claims-list__assessment">
              Avaliação do juiz: <strong>{formatClaimVerdict(assessment.verdict)}</strong> —{' '}
              {assessment.explanation}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

export function ClaimsList({ claims, assessmentsByClaimId }: ClaimsListProps) {
  const claimsById = new Map(claims.map((c) => [c.id, c]))

  if (claims.length === 0) {
    return <p>Nenhuma afirmação foi extraída nesta execução.</p>
  }

  return (
    <ul className="claims-list">
      {claims.map((claim) => (
        <ClaimCard
          key={claim.id}
          claim={claim}
          assessment={assessmentsByClaimId.get(claim.id)}
          claimsById={claimsById}
        />
      ))}
    </ul>
  )
}
