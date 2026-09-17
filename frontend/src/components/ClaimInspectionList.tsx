// Inspeção semântica centrada em claim (UI Slice -- claim-centered
// semantic inspection): cada `ClaimPublic.id` vira UMA unidade
// product-facing reunindo tudo que os canais existentes dizem
// EXPLICITAMENTE sobre ela (debate, juiz, fonte, reconciliação),
// preservando a autoridade de cada canal -- NUNCA combinados num
// indicador único de "verdade"/confiança.
//
// Fonte de verdade: `buildClaimInspectionModel` (api/claimInspectionModel.ts)
// já fez todo o join por claim_id; este componente só apresenta o
// resultado, nunca resolve referência nenhuma por conta própria.
//
// Identificadores técnicos brutos (UUID de claim/veredito/source-result,
// response IDs, requested/effective model identity) NUNCA aparecem aqui
// -- ficam só na Auditoria técnica (ver InspectionPanel.tsx).

import { useState } from 'react'
import type { ClaimInspectionModel, ClaimInspectionUnit } from '../api/claimInspectionModel'
import {
  formatChannelRelationship,
  formatClaimVerdict,
  formatSourceChannelState,
  formatSourceRejectionReason,
  formatSourceRelation,
  formatSupportRatio,
} from '../api/formatting'

interface ClaimInspectionListProps {
  model: ClaimInspectionModel
  // Fonte foi fornecida E a análise concluiu (skipped_reason === null).
  // Quando false, a subseção de fonte nunca aparece por claim -- o
  // status de nível de execução (ausente/pulada/falha) já é comunicado
  // uma única vez por `SourceAnalysisView`, nunca repetido N vezes aqui.
  sourceRelationAvailable: boolean
  // `reconciliation !== null` -- reconciliação roda mesmo sem fonte
  // (produzindo um estado "não comparável" honesto), então esta flag é
  // independente de `sourceRelationAvailable`.
  reconciliationAvailable: boolean
}

function ClaimInspectionChannel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="claim-inspection-list__channel">
      <p className="claim-inspection-list__channel-label">{label}</p>
      {children}
    </div>
  )
}

function renderJudgeChannel(unit: ClaimInspectionUnit, judgeVerdictPresent: boolean) {
  if (unit.judgeAssessments.length === 0) {
    return (
      <p>
        {judgeVerdictPresent
          ? 'Esta afirmação não foi avaliada no veredito desta execução.'
          : 'O juiz não ficou disponível nesta execução.'}
      </p>
    )
  }
  return (
    <>
      {unit.judgeAssessments.map((assessment, index) => (
        <p key={index} className="claim-inspection-list__judge-assessment">
          <strong>{formatClaimVerdict(assessment.verdict)}</strong> — {assessment.explanation}
        </p>
      ))}
    </>
  )
}

function renderSourceChannel(unit: ClaimInspectionUnit) {
  if (unit.sourceResults.length === 0) {
    return <p>A análise da fonte não produziu uma relação para esta afirmação.</p>
  }
  return (
    <>
      {unit.sourceResults.map((entry) =>
        entry.kind === 'relation' ? (
          <div key={entry.id} className="claim-inspection-list__source-entry">
            <p>{formatSourceRelation(entry.relation)}</p>
            {entry.excerpt !== null && (
              <blockquote className="claim-inspection-list__excerpt">
                Trecho da fonte: “{entry.excerpt}”
              </blockquote>
            )}
          </div>
        ) : (
          <p key={entry.id} className="claim-inspection-list__source-rejected">
            Entrada descartada pela aplicação — {formatSourceRejectionReason(entry.reason)}
          </p>
        ),
      )}
    </>
  )
}

function renderReconciliationChannel(unit: ClaimInspectionUnit) {
  if (unit.reconciliationOutcomes.length === 0) {
    return <p>Nenhuma reconciliação registrada para esta afirmação nesta execução.</p>
  }
  return (
    <>
      {unit.reconciliationOutcomes.map((outcome, index) => (
        <div key={index} className="claim-inspection-list__reconciliation-entry">
          <p>{formatChannelRelationship(outcome.channel_relationship)}</p>
          <p>{formatSourceChannelState(outcome.source_state)}</p>
        </div>
      ))}
    </>
  )
}

function ClaimInspectionItem({
  unit,
  judgeVerdictPresent,
  sourceRelationAvailable,
  reconciliationAvailable,
}: {
  unit: ClaimInspectionUnit
  judgeVerdictPresent: boolean
  sourceRelationAvailable: boolean
  reconciliationAvailable: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const { claim } = unit

  return (
    <li className="claim-inspection-list__item">
      <button
        type="button"
        className="claim-inspection-list__toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="claim-inspection-list__claim-text">{claim.text}</span>
        <span className="claim-inspection-list__chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {expanded && (
        <div className="claim-inspection-list__details">
          {(unit.parentClaim !== null || unit.mergedFromClaims.length > 0) && (
            <p className="claim-inspection-list__lineage">
              {unit.parentClaim !== null && <>Revisão de: “{unit.parentClaim.text}” </>}
              {unit.mergedFromClaims.length > 0 && (
                <>
                  Fusão de {unit.mergedFromClaims.length} afirmação
                  {unit.mergedFromClaims.length === 1 ? '' : 'ões'} anteriores.
                </>
              )}
            </p>
          )}

          <ClaimInspectionChannel label="Participação no debate">
            <p>
              {formatSupportRatio(
                claim.supporting_models.length,
                claim.support_scope_model_count ?? claim.total_models_in_round,
              )}
            </p>
            {claim.supporting_models.length > 0 && (
              <p className="claim-inspection-list__participants">
                {claim.supporting_models.join(', ')}
              </p>
            )}
          </ClaimInspectionChannel>

          <ClaimInspectionChannel label="Avaliação do juiz">
            {renderJudgeChannel(unit, judgeVerdictPresent)}
          </ClaimInspectionChannel>

          {sourceRelationAvailable && (
            <ClaimInspectionChannel label="Relação com a fonte fornecida">
              {renderSourceChannel(unit)}
            </ClaimInspectionChannel>
          )}

          {reconciliationAvailable && (
            <ClaimInspectionChannel label="Relação entre juiz e fonte">
              {renderReconciliationChannel(unit)}
            </ClaimInspectionChannel>
          )}
        </div>
      )}
    </li>
  )
}

export function ClaimInspectionList({
  model,
  sourceRelationAvailable,
  reconciliationAvailable,
}: ClaimInspectionListProps) {
  if (model.units.length === 0) {
    return <p>Nenhuma afirmação foi extraída nesta execução.</p>
  }

  return (
    <ul className="claim-inspection-list">
      {model.units.map((unit) => (
        <ClaimInspectionItem
          key={unit.claim.id}
          unit={unit}
          judgeVerdictPresent={model.judgeVerdictPresent}
          sourceRelationAvailable={sourceRelationAvailable}
          reconciliationAvailable={reconciliationAvailable}
        />
      ))}
    </ul>
  )
}
