// Council Local Execution Readiness & Admission V1 -- fatos de ACEITE de uma
// run do Conselho: a prontidão local avaliada no aceite, o modo de admissão
// pedido e se o envio reconheceu a degradação conhecida. Só o que o servidor
// sabia localmente naquele momento -- nunca credencial validada, modelo
// disponível ou etapa garantida. `null`/ausente (run anterior a este
// registro) renderiza "não registrado", nunca "presente" nem "ausente".

import {
  formatProviderName,
  formatReadinessRoles,
  groupReadinessByProvider,
} from '../api/formatting'
import type { CouncilAdmission } from '../api/types'

const SUMMARY_LABELS: Record<CouncilAdmission['readiness']['summary'], string> = {
  all_met: 'configuração local presente em todas as etapas do caminho pedido',
  some_unknown: 'nenhuma ausência conhecida; alguma configuração local não pôde ser verificada',
  some_missing: 'faltava configuração local em etapas do caminho pedido',
}

interface CouncilAdmissionViewProps {
  admission: CouncilAdmission | null | undefined
}

export function CouncilAdmissionView({ admission }: CouncilAdmissionViewProps) {
  if (admission === null || admission === undefined) {
    return (
      <p className="council-admission council-admission--unknown">
        Prontidão local no aceite: não registrada (execução anterior a este registro).
      </p>
    )
  }

  const { readiness } = admission
  const missing = groupReadinessByProvider(readiness, 'missing', { includeParticipants: true })
  const unknown = groupReadinessByProvider(readiness, 'unknown', { includeParticipants: true })
  const mode = admission.mode === 'strict' ? 'estrita' : 'padrão'

  return (
    <div className="council-admission">
      <p>
        Prontidão local no aceite: {SUMMARY_LABELS[readiness.summary]}. Admissão {mode}
        {admission.known_degradation_acknowledged
          ? ', enviada sabendo da configuração local ausente.'
          : '.'}
      </p>
      {missing.length > 0 && (
        <>
          <p>Sem configuração local:</p>
          <ul>
            {missing.map((group) => (
              <li key={group.provider}>
                {formatReadinessRoles(group.roles)}: {formatProviderName(group.provider)} (modelo
                configurado: {group.configuredModel})
              </li>
            ))}
          </ul>
        </>
      )}
      {unknown.length > 0 && (
        <>
          <p>Configuração local não verificável (não quer dizer que faltasse):</p>
          <ul>
            {unknown.map((group) => (
              <li key={group.provider}>
                {formatReadinessRoles(group.roles)}: {formatProviderName(group.provider)} (modelo
                configurado: {group.configuredModel})
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
