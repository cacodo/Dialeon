// Análise de fonte (Etapa 16) -- AUDIT-ONLY: nada aqui influencia
// Judge/Editor/FinalAnswer, é só visibilidade de um resultado que já
// existia, computado e persistido, mas não chegava a nenhuma superfície
// humana (patch de visibilidade, ver app/source_analysis/models.py).
//
// Claim-centered semantic inspection (UI Slice) -- a renderização
// POR CLAIM de `claim_results` (relations + entradas rejeitadas
// ATRIBUÍDAS a uma claim_id conhecida) migrou pra `ClaimInspectionList`
// (via `buildClaimInspectionModel`), que já preserva a mesma disciplina
// de join por claim_id estruturado. Este componente mantém só o que é
// genuinamente de NÍVEL DE EXECUÇÃO, nunca duplicado numa unidade de
// claim:
//   - fonte não fornecida / análise pulada-ou-falhada (`skipped_reason`);
//   - entradas rejeitadas SEM claim_id (`claim_id === null`) -- por
//     definição nunca pertencem a nenhuma claim (ver
//     RejectedSourceEntryPublic, app/presentation/schemas.py), então
//     nunca aparecem em nenhuma unidade de `ClaimInspectionList`.
//
// Repair pós-revisão adversarial (nº3) -- a lista de "não atribuídas"
// vem SEMPRE de `unattributedRejectedSourceEntries`, já produzida e
// VALIDADA por `buildClaimInspectionModel` (api/claimInspectionModel.ts),
// NUNCA re-derivada por conta própria filtrando `sourceAnalysis.
// claim_results` de novo aqui. A validação do adapter já exclui
// entradas cujo `id` é ambíguo (duplicado em `claim_results`) desta
// lista -- refiltrar os dados brutos aqui reintroduziria exatamente
// esses registros em quarentena de volta na superfície product-facing,
// e com eles um `key` de React duplicado. Registros excluídos por essa
// ambiguidade continuam inspecionáveis, só que exclusivamente na
// Auditoria técnica (ver InspectionPanel.tsx).
//
// SOURCE RELATION != TRUTH VERDICT continua valendo pro texto abaixo.

import type { RejectedSourceEntryPublic, SourceAnalysisOutcome } from '../api/types'
import { formatSourceAnalysisSkippedReason, formatSourceRejectionReason } from '../api/formatting'

interface SourceAnalysisViewProps {
  sourceAnalysis: SourceAnalysisOutcome | null
  unattributedRejectedSourceEntries: RejectedSourceEntryPublic[]
}

export function SourceAnalysisView({
  sourceAnalysis,
  unattributedRejectedSourceEntries,
}: SourceAnalysisViewProps) {
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

  if (unattributedRejectedSourceEntries.length === 0) {
    return null
  }

  return (
    <div className="source-analysis">
      <h4>Entradas descartadas sem afirmação identificada</h4>
      <p className="source-analysis__rejected-note">
        Entradas descartadas não são uma relação com a fonte — a aplicação não pôde confiar no
        que a análise devolveu para elas, e estas em particular não puderam ser associadas a
        nenhuma afirmação específica desta execução.
      </p>
      <ul className="source-analysis__rejected">
        {unattributedRejectedSourceEntries.map((entry) => (
          <li key={entry.id}>
            Afirmação não identificada: {formatSourceRejectionReason(entry.reason)}
          </li>
        ))}
      </ul>
    </div>
  )
}
