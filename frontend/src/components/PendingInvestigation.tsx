// Estado de ESPERA de uma investigação -- honesto por construção: só um
// relógio LOCAL de tempo decorrido e duas frases estáticas. Nenhuma etapa,
// percentual, previsão de término, animação de digitação ou progresso por
// participante (o backend não expõe nada disso; `POST /runs` é síncrono).
//
// A linha de status (`role="status"`) é ESTÁTICA -- o contador de segundos
// fica fora da região viva pra não gerar um anúncio a cada segundo.
//
// Sobre sair da página: só o resultado LOCAL desta tela deixa de aparecer; a
// execução aceita pode ser localizada no Histórico. O frontend não consegue
// provar se uma execução persistida sem desfecho ainda está rodando, então
// nada aqui afirma isso nem afirma que algo foi perdido.

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
    <div className="pending-investigation">
      <p role="status" className="pending-investigation__title">
        Investigando…
      </p>
      <p className="pending-investigation__elapsed">
        Tempo decorrido: <time>{formatElapsed((now - startedAt) / 1000)}</time>
      </p>
      <p className="pending-investigation__note">
        Uma investigação do Dialeon pode levar vários minutos. Se você sair desta página, o
        resultado deixará de aparecer aqui; a investigação poderá ser reaberta pelo Histórico.
      </p>
    </div>
  )
}
