// Cross-Channel Reconciliation V1 -- visibilidade humana da classificação
// DETERMINÍSTICA (sem LLM) de RELACIONAMENTO entre o canal Judge e o
// canal Source Analysis por claim corrente. AUDIT-ONLY na origem, mas o
// relacionamento em si já é refletido na resposta final pelo Editor --
// esta view só inspeciona o registro estruturado por trás disso, nunca
// recalcula nada.
//
// Claim-centered semantic inspection (UI Slice) -- a renderização POR
// CLAIM de `claim_outcomes` (relacionamento + estado da fonte por
// claim_id) migrou pra `ClaimInspectionList` (via
// `buildClaimInspectionModel`), que preserva a mesma disciplina de join
// por claim_id estruturado -- nunca por texto. Referências brutas
// (judge_verdict_id/source_claim_result_ids) NUNCA aparecem no fluxo
// product-facing; ficam só na Auditoria técnica (ver InspectionPanel).
//
// Repair pós-revisão adversarial (nº2) -- `reconciliation.status` NUNCA
// é confiado sozinho pra declarar "juiz indisponível"/"juiz avaliou":
// `envelopeCoherent` (computado por `buildClaimInspectionModel`, que
// cruza `status` com a presença REAL de `judgeVerdict`) precisa ser
// checado PRIMEIRO. Um envelope incoerente (ex.: status=judge_unavailable
// mas esta execução TEM um veredito real) nunca pode fazer esta view
// declarar product-facing que o juiz ficou indisponível -- isso seria
// uma afirmação FALSA sobre o estado real da execução. Os dados brutos
// continuam disponíveis na Auditoria técnica de qualquer forma.
//
// Este componente mantém só as notas genuinamente de nível de execução
// que não têm claim_id nenhum pra se anexar:
//   - `reconciliation === null` (execução histórica, recurso nem
//     existia -- nunca inferido como "sem reconciliação computável");
//   - envelope incoerente (nunca reinterpretado como nenhum dos dois
//     status reais -- nunca infere um status substituto);
//   - `status === 'judge_unavailable'` coerente (explica por que o
//     relacionamento não é comparável nesta execução, mesmo com o
//     estado da fonte por claim continuando visível dentro de cada
//     unidade).
//
// CHANNEL RELATIONSHIP != JUDGE VERDICT != TRUTH continua valendo.

import type { SourceJudgeReconciliationResultPublic } from '../api/types'

interface ReconciliationViewProps {
  reconciliation: SourceJudgeReconciliationResultPublic | null
  envelopeCoherent: boolean
}

export function ReconciliationView({ reconciliation, envelopeCoherent }: ReconciliationViewProps) {
  if (reconciliation === null) {
    return <p>Execução anterior a este recurso — nenhuma reconciliação estruturada foi computada.</p>
  }

  if (!envelopeCoherent) {
    return (
      <p>
        A reconciliação registrada nesta execução não é internamente coerente -- o status
        registrado não corresponde à presença real de uma avaliação do juiz. Nenhum
        relacionamento desta reconciliação é confiável o suficiente pra apresentar aqui; os
        registros originais continuam disponíveis na Auditoria técnica.
      </p>
    )
  }

  if (reconciliation.status === 'judge_unavailable') {
    return (
      <p>
        O juiz não ficou disponível nesta execução — o relacionamento entre canais não é
        comparável, mas o estado da fonte por afirmação continua registrado nas afirmações acima.
      </p>
    )
  }

  // status === 'complete': nada de nível de execução além do que já
  // está em cada unidade de claim (ver ClaimInspectionList) -- nenhum
  // resumo redundante aqui.
  return null
}
