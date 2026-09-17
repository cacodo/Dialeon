// Resposta final -- protagonista absoluta (ANSWER FIRST). limitations
// aparecem logo abaixo, sempre que existirem (nunca escondidas).

import type { FinalAnswerPublic } from '../api/types'
import { formatFinalAnswerStatus, splitAnswerParagraphs } from '../api/formatting'

interface FinalAnswerViewProps {
  finalAnswer: FinalAnswerPublic
}

export function FinalAnswerView({ finalAnswer }: FinalAnswerViewProps) {
  return (
    <section aria-labelledby="final-answer-heading" className="final-answer">
      <h2 id="final-answer-heading">Resposta</h2>
      <div className="final-answer__text">
        {splitAnswerParagraphs(finalAnswer.answer_text).map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>

      {finalAnswer.limitations.length > 0 && (
        <div className="final-answer__limitations">
          <h3>Limitações</h3>
          <ul>
            {finalAnswer.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="final-answer__status">{formatFinalAnswerStatus(finalAnswer.status)}</p>
    </section>
  )
}
