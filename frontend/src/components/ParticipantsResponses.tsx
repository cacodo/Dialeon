// Perspectivas dos participantes (UI Slice -- Participant Perspectives
// Document Disclosure): cada resposta é a perspectiva INDIVIDUAL de um
// participante, nunca a conclusão do Dialeon. `round` (nunca um
// `title` de texto livre) fixa tanto o heading quanto o ID seguro do
// disclosure -- nenhum ID é derivado de texto humano com espaço (ver
// InspectionPanel.tsx pro aviso único, section-level, de que estas são
// perspectivas individuais, nunca repetido por resposta).
//
// response_text é texto OPACO -- nunca Markdown/HTML/estrutura
// analisada. `white-space: pre-wrap` (CSS) preserva espaço/quebras de
// linha exatamente como persistido, sem reinterpretar "- "/"#"/URLs
// como marcação. Identidade de modelo (requested/effective/
// model_identity_source) NUNCA aparece no heading product-facing --
// provenance, não autoridade epistêmica -- e continua inteiramente
// inspecionável na Auditoria técnica (ver InspectionPanel.tsx).
//
// Falha persistida é um ESTADO HISTÓRICO da execução, nunca um alerta
// ao vivo (sem role="alert") -- e nunca é reinterpretada como
// desacordo/rejeição/opinião do participante. A categoria de falha
// mostrada aqui é LIMITADA a `error.type` (rótulo humano, ver
// formatFailureCategory) -- mensagem bruta/retryable/tentativas ficam
// só na Auditoria técnica.

import { useId, useState } from 'react'
import type { ModelResponsePublic } from '../api/types'
import { formatFailureCategory, formatProviderName } from '../api/formatting'

type RoundKind = 'initial' | 'critique'

const ROUND_LABELS: Record<RoundKind, { heading: string; sectionId: string }> = {
  initial: { heading: 'Perspectivas iniciais', sectionId: 'participants-initial' },
  critique: { heading: 'Revisões após o debate', sectionId: 'participants-critique' },
}

interface ParticipantsResponsesProps {
  responses: ModelResponsePublic[]
  round: RoundKind
}

function ParticipantPerspective({
  response,
  label,
}: {
  response: ModelResponsePublic
  label: string
}) {
  const [expanded, setExpanded] = useState(false)
  // Identidade de apresentação gerada pelo PRÓPRIO React (repair
  // pós-revisão adversarial) -- nunca `response.id` (opaco, persistido,
  // sem garantia de unicidade nem de ser DOM-safe). `useId()` é sempre
  // único por instância de componente e nunca contém espaço/caractere
  // que quebraria um seletor -- panelId aqui NUNCA é uma identidade
  // semântica, só um mecanismo de apresentação local.
  const panelId = useId()

  return (
    <li className="participant-perspective">
      <button
        type="button"
        className="participant-perspective__toggle"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="participant-perspective__label">{label}</span>
        <span className="participant-perspective__chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {expanded && (
        <div id={panelId} className="participant-perspective__panel">
          {response.response_text !== null ? (
            <p className="participant-perspective__text">{response.response_text}</p>
          ) : (
            <p className="participant-perspective__absent">
              Este participante não produziu uma perspectiva nesta rodada
              {response.error ? ` — ${formatFailureCategory(response.error.type)}` : ''}.
            </p>
          )}
        </div>
      )}
    </li>
  )
}

export function ParticipantsResponses({ responses, round }: ParticipantsResponsesProps) {
  const { heading, sectionId } = ROUND_LABELS[round]
  const headingId = `${sectionId}-heading`

  // Rótulo distinguível por controle mesmo no caso anômalo/histórico de
  // um provider aparecer mais de uma vez na MESMA rodada -- nunca dois
  // botões com o mesmo nome acessível dentro da mesma lista.
  const occurrences = new Map<string, number>()
  for (const response of responses) {
    occurrences.set(response.provider, (occurrences.get(response.provider) ?? 0) + 1)
  }
  const seenSoFar = new Map<string, number>()

  return (
    <section aria-labelledby={headingId} className="participant-responses-round">
      <h4 id={headingId}>{heading}</h4>
      <ul className="participant-responses">
        {responses.map((response, index) => {
          const seen = (seenSoFar.get(response.provider) ?? 0) + 1
          seenSoFar.set(response.provider, seen)
          const isDuplicateProvider = (occurrences.get(response.provider) ?? 0) > 1
          const label = isDuplicateProvider
            ? `Perspectiva — ${formatProviderName(response.provider)} (${seen})`
            : `Perspectiva — ${formatProviderName(response.provider)}`

          return (
            <ParticipantPerspective
              // Chave de APRESENTAÇÃO, nunca uma identidade persistida --
              // posição dentro desta rodada, colisão-segura mesmo se
              // response.id se repetir (histórico anômalo) ou não for
              // DOM-safe. `response.id` continua exibido verbatim só
              // como CONTEÚDO, na Auditoria técnica.
              key={`${sectionId}-${index}`}
              response={response}
              label={label}
            />
          )
        })}
      </ul>
    </section>
  )
}
