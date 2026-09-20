// "Copiar resposta" -- copia EXATAMENTE o `answer_text` canônico (nunca uma
// reconstrução a partir dos blocos renderizados, nunca Markdown/outro formato).
// O feedback vai numa região `role="status"` SEMPRE presente (vazia em
// repouso) pra que o anúncio a leitores de tela funcione; falha do clipboard
// (contexto inseguro, permissão negada, API ausente) nunca quebra a resposta.

import { useState } from 'react'

type CopyStatus = 'idle' | 'copied' | 'failed'

const FEEDBACK: Record<CopyStatus, string> = {
  idle: '',
  copied: 'Resposta copiada.',
  failed: 'Não foi possível copiar automaticamente. Selecione o texto da resposta e copie manualmente.',
}

export function CopyAnswerButton({ text }: { text: string }) {
  const [status, setStatus] = useState<CopyStatus>('idle')

  async function handleCopy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard indisponível')
      await navigator.clipboard.writeText(text)
      setStatus('copied')
    } catch {
      setStatus('failed')
    }
  }

  return (
    <div className="copy-answer">
      <button type="button" className="copy-answer__button" onClick={handleCopy}>
        Copiar resposta
      </button>
      <span role="status" className="copy-answer__feedback">
        {FEEDBACK[status]}
      </span>
    </div>
  )
}
