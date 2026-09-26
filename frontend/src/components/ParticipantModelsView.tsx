// Council Accepted Effective Participant Model Choice V1 -- o mapa congelado
// no aceite (`config.participant_models`): o modelo PEDIDO a cada
// participante e de onde veio. O que cada provider REPORTOU está nas
// respostas dos participantes. `null`/ausente (run anterior a este registro)
// aparece como "não registrado" -- nunca reconstruído do padrão atual.

import { formatProviderName } from '../api/formatting'
import type { ParticipantModelChoice } from '../api/types'

const ORIGIN_LABELS: Record<ParticipantModelChoice['origin'], string> = {
  configured_default: 'padrão configurado',
  run_override: 'escolhido nesta pergunta',
}

export function ParticipantModelsView({ choices }: { choices: ParticipantModelChoice[] | null | undefined }) {
  if (choices === null || choices === undefined) {
    return (
      <p className="participant-models participant-models--unknown">
        Modelos pedidos aos participantes: não registrados (execução anterior a este registro).
      </p>
    )
  }
  return (
    <ul className="participant-models">
      {choices.map((c) => (
        <li key={c.provider}>
          {formatProviderName(c.provider)}: <code>{c.requested_model}</code> ({ORIGIN_LABELS[c.origin] ?? c.origin})
        </li>
      ))}
    </ul>
  )
}
