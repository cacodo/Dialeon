// Análise de fonte (Etapa 16) -- AUDIT-ONLY: nada aqui influencia
// Judge/Editor/FinalAnswer, é só visibilidade de um resultado que já
// existia, computado e persistido, mas não chegava a nenhuma superfície
// humana (patch de visibilidade, ver app/source_analysis/models.py).
//
// SOURCE RELATION != TRUTH VERDICT: os rótulos usados aqui (via
// formatSourceRelation) descrevem a relação entre a afirmação e o texto
// da fonte fornecida pelo usuário -- nunca uma segunda decisão de
// verdade/falsidade. Nunca confundir uma entrada REJEITADA (a aplicação
// não pôde confiar no que a análise devolveu) com uma relação
// "unresolved" (a análise respondeu que a fonte não decide a claim) --
// são conceitos diferentes, mantidos em listas visualmente distintas.
//
// Associação claim<->relação é SEMPRE por claim_id estruturado (nunca
// por comparação de texto) -- uma claim não encontrada nos dados desta
// execução degrada explicitamente, nunca é silenciosamente anexada a
// outra afirmação.

import type {
  ClaimPublic,
  RejectedSourceEntryPublic,
  SourceAnalysisOutcome,
  SourceClaimAnalysisResultPublic,
  ValidSourceRelationPublic,
} from '../api/types'
import {
  formatSourceAnalysisSkippedReason,
  formatSourceRejectionReason,
  formatSourceRelation,
} from '../api/formatting'

interface SourceAnalysisViewProps {
  sourceAnalysis: SourceAnalysisOutcome | null
  claims: ClaimPublic[]
}

function isRelation(
  entry: SourceClaimAnalysisResultPublic,
): entry is ValidSourceRelationPublic {
  return entry.kind === 'relation'
}

function isRejected(
  entry: SourceClaimAnalysisResultPublic,
): entry is RejectedSourceEntryPublic {
  return entry.kind === 'rejected'
}

export function SourceAnalysisView({ sourceAnalysis, claims }: SourceAnalysisViewProps) {
  if (sourceAnalysis === null) {
    return <p>Nenhuma fonte foi fornecida nesta execução.</p>
  }

  if (sourceAnalysis.skipped_reason !== null) {
    return (
      <p>
        Análise da fonte não concluída:{' '}
        {formatSourceAnalysisSkippedReason(sourceAnalysis.skipped_reason)}
      </p>
    )
  }

  const claimsById = new Map(claims.map((c) => [c.id, c]))
  const relations = sourceAnalysis.claim_results.filter(isRelation)
  const rejected = sourceAnalysis.claim_results.filter(isRejected)

  if (relations.length === 0 && rejected.length === 0) {
    return <p>A análise da fonte não produziu nenhuma relação nem entrada descartada.</p>
  }

  return (
    <div className="source-analysis">
      {relations.length > 0 && (
        <ul className="source-analysis__relations claims-list">
          {relations.map((relation) => {
            const claim = claimsById.get(relation.claim_id)
            return (
              <li key={relation.id} className="source-analysis__relation claims-list__item">
                <p className="source-analysis__claim-text">
                  {claim
                    ? claim.text
                    : `Afirmação não encontrada nos dados desta execução (id: ${relation.claim_id}).`}
                </p>
                <p className="source-analysis__relation-label">
                  {formatSourceRelation(relation.relation)}
                </p>
                {relation.excerpt !== null && (
                  <blockquote className="source-analysis__excerpt">
                    Trecho da fonte: “{relation.excerpt}”
                  </blockquote>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {rejected.length > 0 && (
        <>
          <h3>Entradas descartadas pela aplicação</h3>
          <p className="source-analysis__rejected-note">
            Entradas descartadas não são uma relação com a fonte — a aplicação não pôde
            confiar no que a análise devolveu para elas.
          </p>
          <ul className="source-analysis__rejected">
            {rejected.map((entry) => (
              <li key={entry.id}>
                {entry.claim_id ? `Afirmação ${entry.claim_id}` : 'Afirmação não identificada'}:{' '}
                {formatSourceRejectionReason(entry.reason)}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
