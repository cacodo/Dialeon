// Qual forma da resposta final do Conselho é apresentada como "a resposta".
//
// Mesma regra do backend (app/presentation/answer_presentation.py, usada pela
// CLI e pela exportação legível): as duas implementações são conferidas contra
// a MESMA tabela de casos (./__tests__/answer_presentation_cases.json), pra que
// nunca divirjam em silêncio. Ordem de fallback: realização linguística
// (elegível) -> resposta natural (elegível) -> resposta principal estruturada
// -> avaliação completa. A elegibilidade vem pronta do servidor
// (`*_presentation_eligible`); nunca é recalculada aqui.

import type { FinalAnswerPublic } from '../api/types'

export type AnswerPresentationKind =
  | 'linguistic_realization'
  | 'natural_answer'
  | 'primary_answer'
  | 'complete_assessment'

export type AnswerPresentationInput = Pick<
  FinalAnswerPublic,
  | 'linguistic_realization'
  | 'linguistic_realization_presentation_eligible'
  | 'natural_answer'
  | 'natural_answer_presentation_eligible'
  | 'primary_answer'
>

export function selectAnswerPresentationKind(finalAnswer: AnswerPresentationInput): AnswerPresentationKind {
  if (
    finalAnswer.linguistic_realization != null &&
    finalAnswer.primary_answer != null &&
    finalAnswer.linguistic_realization_presentation_eligible === true
  ) {
    return 'linguistic_realization'
  }
  if (
    finalAnswer.natural_answer != null &&
    finalAnswer.primary_answer != null &&
    finalAnswer.natural_answer_presentation_eligible === true
  ) {
    return 'natural_answer'
  }
  if (finalAnswer.primary_answer != null) {
    return 'primary_answer'
  }
  return 'complete_assessment'
}
