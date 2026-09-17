// Representação MVP de claims: lista/cards expansíveis, relações por ID
// resolvidas via JOIN em memória sobre o MESMO payload de audit já
// recebido (nenhuma lógica de negócio nova, nenhuma chamada extra).
// Nunca calcula truth score nem reinterpreta supporting_model_ratio como
// confidence (CONSENSUS != TRUTH).
//
// O denominador de suporte de CADA claim é o denominador EFETIVO --
// claim.support_scope_model_count quando presente (claim canônica de
// reconciliação cross-round, cujo universo de suporte atravessa mais de
// uma rodada), senão claim.total_models_in_round (comportamento
// histórico, toda claim de rodada única) -- nunca o total da rodada
// inicial da execução como um todo, e nunca total_models_in_round
// diretamente quando support_scope_model_count existe (ver
// Claim._effective_support_denominator, app/models/domain.py). Usar o
// total errado alteraria a semântica de um dado já persistido (UI NÃO
// PODE ALTERAR A SEMÂNTICA DO DADO PERSISTIDO).
//
// Correção pós-revisão independente (HIGH 2) -- o NUMERADOR de
// participantes é claim.supporting_models.length (a projeção já
// deduplicada por provider/model, exposta publicamente pela API, ver
// app/presentation/schemas.py), NUNCA
// claim.supporting_model_response_ids.length (a lista BRUTA de
// ClaimSupport, nunca deduplicada por design -- o mesmo provider/model
// respondendo em 2 rodadas aparece 2 vezes ali, preservando o histórico
// de auditoria completo). Usar a contagem bruta como numerador podia
// exibir "2 de 1 participantes" pra uma claim cujo único
// provider/model único respondeu 2 vezes -- numerador > denominador,
// uma proporção matematicamente impossível. A lista de auditoria
// completa (supporting_model_response_ids) continua exibida abaixo, sem
// nenhuma mudança -- só a CONTAGEM usada na proporção precisa ser a
// deduplicada.
//
// Repair pós-revisão adversarial (nº4) -- esta é a lista TÉCNICA
// completa (ver InspectionPanel.tsx, Auditoria técnica), montada sobre
// `audit.claims` BRUTO, sem passar pelo boundary de integridade de
// `buildClaimInspectionModel` -- então ela também pode conter claim_id
// duplicado. Nunca finge que um registro é canônico quando há
// ambiguidade: `assessmentsByClaimId` é `Map<string, ClaimAssessmentPublic[]>`
// (nunca `Map<string, ClaimAssessmentPublic>` -- essa construção via
// `new Map(pairs)` colapsaria silenciosamente pra "a última avaliação"),
// a chave React de cada item nunca é só `claim.id` (índice junto, pra
// nunca colidir e nunca sugerir que um registro "venceu" o outro), e a
// resolução de lineage (`parent_claim_id`) só resolve quando o id é
// ÚNICO nesta lista -- um id ambíguo aqui nunca é silenciosamente
// escolhido como "o" pai.

import { useState } from 'react'
import type { ClaimAssessmentPublic, ClaimPublic } from '../api/types'
import { formatClaimVerdict, formatModelIdentitySource, formatSupportRatio } from '../api/formatting'

interface ClaimsListProps {
  claims: ClaimPublic[]
  assessmentsByClaimId: Map<string, ClaimAssessmentPublic[]>
}

function ClaimCard({
  claim,
  assessments,
  claimsById,
}: {
  claim: ClaimPublic
  assessments: ClaimAssessmentPublic[]
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
            {formatSupportRatio(
              claim.supporting_models.length,
              claim.support_scope_model_count ?? claim.total_models_in_round,
            )}
          </p>
          <ul className="claims-list__supporters">
            {claim.supporting_model_response_ids.map((support) => (
              <li key={support.model_response_id}>
                {support.provider} / {support.model} (
                {formatModelIdentitySource(support.model_identity_source)})
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
          {assessments.map((assessment, index) => (
            <p key={index} className="claims-list__assessment">
              Avaliação do juiz: <strong>{formatClaimVerdict(assessment.verdict)}</strong> —{' '}
              {assessment.explanation}
            </p>
          ))}
        </div>
      )}
    </li>
  )
}

export function ClaimsList({ claims, assessmentsByClaimId }: ClaimsListProps) {
  // Agrupado, nunca `new Map(claims.map((c) => [c.id, c]))` -- um id
  // duplicado nunca deve resolver silenciosamente pra "o último claim
  // com esse id" ao montar a linha de lineage abaixo.
  const claimGroupsById = new Map<string, ClaimPublic[]>()
  for (const claim of claims) {
    const group = claimGroupsById.get(claim.id)
    if (group) group.push(claim)
    else claimGroupsById.set(claim.id, [claim])
  }
  const claimsById = new Map<string, ClaimPublic>()
  for (const [id, group] of claimGroupsById) {
    if (group.length === 1) claimsById.set(id, group[0])
  }

  if (claims.length === 0) {
    return <p>Nenhuma afirmação foi extraída nesta execução.</p>
  }

  return (
    <ul className="claims-list">
      {claims.map((claim, index) => (
        <ClaimCard
          // claim.id sozinho colidiria (e implicaria um "vencedor") se
          // houver claim_id duplicado nesta lista bruta -- o índice
          // garante uma key sempre única, sem sugerir canonicidade.
          key={`${claim.id}::${index}`}
          claim={claim}
          assessments={assessmentsByClaimId.get(claim.id) ?? []}
          claimsById={claimsById}
        />
      ))}
    </ul>
  )
}
