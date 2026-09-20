// Limites ESTÁTICOS de entrada de uma nova Run -- ESPELHAM os limites
// canônicos do backend (`MAX_QUESTION_CHARACTERS`/`MAX_SOURCE_TEXT_CHARACTERS`
// em app/orchestrator/config.py). O backend continua a autoridade real: isto
// só existe pra dar feedback ANTES do submit. Um teste de backend
// (tests/test_frontend_input_limits.py) compara estes valores com os do
// servidor, então uma divergência falha a suíte em vez de driftar em silêncio.
export const MAX_QUESTION_CHARACTERS = 20_000
export const MAX_SOURCE_TEXT_CHARACTERS = 20_000

// O backend (Python) conta CODE POINTS; `String.length` do JS conta unidades
// UTF-16 (um emoji conta 2). Contar por code point evita rejeitar no cliente
// uma entrada que o servidor aceitaria exatamente no limite.
export function characterCount(text: string): number {
  let count = 0
  for (const _ of text) count += 1
  return count
}

export function formatCharacterLimit(limit: number): string {
  return limit.toLocaleString('pt-BR')
}
