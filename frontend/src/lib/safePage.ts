// Validação de página compartilhada entre os dois pontos de entrada de
// "página" do Histórico: a URL (`?page=N`, parseada como string em
// History.tsx) e o `location.state.fromHistoryPage` anexado pela
// navegação History → RunDetail (um valor já numérico, mas de origem
// não confiável -- pode ser manufaturado diretamente, sem passar por
// parsing de string). Ambos os lados precisam da MESMA definição de
// "página válida" pra nunca divergir: um inteiro seguro (`isSafeInteger`,
// nunca `Infinity`/`NaN`/float/unsafe-integer-por-perda-de-precisão)
// maior ou igual a 1.
export function isValidPage(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 1
}
