// Espera honesta por uma resposta: o POST é síncrono e não expõe etapas, então
// não há etapas, percentuais, previsão nem progresso por modelo a mostrar --
// só o tempo decorrido LOCAL e o que acontece se o usuário sair da página.
//
// A linha `role="status"` é estática (o contador não é anunciado a cada
// segundo). O indicador de atividade é neutro e decorativo (`aria-hidden`),
// sem identidade de marca; com `prefers-reduced-motion` ele fica parado.

import { useEffect, useState } from 'react'
import { formatElapsed } from '../api/formatting'

export function PendingInvestigation() {
  const [startedAt] = useState(() => Date.now())
  const [now, setNow] = useState(startedAt)

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="pending-answer">
      <p className="pending-answer__title">
        <span className="activity-indicator" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span role="status">Aguardando a resposta…</span>
      </p>
      <p className="pending-answer__elapsed">
        Tempo decorrido: <time>{formatElapsed((now - startedAt) / 1000)}</time>
      </p>
      <p className="pending-answer__note">
        Uma resposta do Dialeon pode levar vários minutos. Se você sair desta página, o resultado
        deixará de aparecer aqui; a pergunta poderá ser reaberta pelo Histórico.
      </p>
    </div>
  )
}
