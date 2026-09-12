// Respostas de uma rodada (inicial ou crítica) -- provider/model/texto em
// primeira camada, metadado técnico (usage/pricing/erro bruto) atrás de
// disclosure. Identidade de modelo é exibida exatamente como a API
// devolveu -- nenhuma inferência de requested/effective além dos dados.

import { useState } from 'react'
import type { ModelResponsePublic } from '../api/types'
import { formatEstimatedCost, formatTokenCount } from '../api/formatting'

interface ParticipantsResponsesProps {
  responses: ModelResponsePublic[]
  title: string
}

function ResponseCard({ response }: { response: ModelResponsePublic }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <li className="participant-response">
      <div className="participant-response__header">
        <span className="participant-response__model">
          {response.provider} / {response.model}
        </span>
        <span className="participant-response__status">
          {response.status === 'success' ? 'Respondeu' : 'Falhou'}
        </span>
      </div>
      {response.response_text && <p className="participant-response__text">{response.response_text}</p>}
      {response.error && <p role="alert">{response.error.message}</p>}

      <button
        type="button"
        className="participant-response__toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? 'Ocultar detalhes técnicos' : 'Detalhes técnicos'}
      </button>
      {expanded && (
        <dl className="participant-response__technical">
          <div>
            <dt>Tokens de entrada</dt>
            <dd>{formatTokenCount(response.usage?.input_tokens ?? null)}</dd>
          </div>
          <div>
            <dt>Tokens de saída</dt>
            <dd>{formatTokenCount(response.usage?.output_tokens ?? null)}</dd>
          </div>
          <div>
            <dt>Custo estimado</dt>
            <dd>{formatEstimatedCost(response.cost_usd, false)}</dd>
          </div>
          <div>
            <dt>Tentativas</dt>
            <dd>{response.attempts}</dd>
          </div>
        </dl>
      )}
    </li>
  )
}

export function ParticipantsResponses({ responses, title }: ParticipantsResponsesProps) {
  return (
    <section aria-labelledby={`participants-${title}`}>
      <h3 id={`participants-${title}`}>{title}</h3>
      <ul className="participant-responses">
        {responses.map((response) => (
          <ResponseCard key={response.id} response={response} />
        ))}
      </ul>
    </section>
  )
}
