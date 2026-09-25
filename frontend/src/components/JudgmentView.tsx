// Veredito do juiz -- estados reais, sem cor como único indicador, sem
// transformar confidence em probabilidade objetiva de verdade.
//
// Claim-centered semantic inspection (UI Slice) -- este componente
// mantém SÓ o raciocínio/estado GLOBAL do juiz (nunca por claim -- as
// avaliações por claim_id já aparecem dentro de cada unidade em
// `ClaimInspectionList`, via `buildClaimInspectionModel`, nunca
// duplicadas aqui). Identidade de modelo é PROVENIÊNCIA, não o conteúdo
// semântico principal -- por isso aparece por último, como rodapé,
// nunca como a primeira linha (rodeada/reasoning primeiro).

import type { JudgeVerdictPublic } from '../api/types'
import { formatModelIdentitySource } from '../api/formatting'

interface JudgmentViewProps {
  verdict: JudgeVerdictPublic | null
}

export function JudgmentView({ verdict }: JudgmentViewProps) {
  if (verdict === null) {
    return <p>Esta execução não teve uma avaliação de juiz disponível.</p>
  }

  return (
    <section aria-labelledby="judgment-heading">
      <h4 id="judgment-heading">Avaliação</h4>
      <p className="judgment__reasoning">{verdict.reasoning}</p>
      <p className="judgment__confidence-caveat">
        Confiança declarada pelo juiz: {(verdict.confidence * 100).toFixed(0)}% — uma avaliação
        subjetiva do próprio modelo, não uma medida objetiva de veracidade.
      </p>
      {verdict.debate_limitations.length > 0 && (
        <div>
          <h5>Limitações do debate</h5>
          <ul>
            {verdict.debate_limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="judgment__model">
        Juiz: {verdict.judge_model} (
        {formatModelIdentitySource(verdict.judge_model_identity_source)})
      </p>
    </section>
  )
}
