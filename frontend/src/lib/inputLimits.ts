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

// Code points que o `str.strip()` do backend (Python) remove -- exatamente o
// conjunto de `str.isspace()`. O backend usa isso SÓ pra decidir se a fonte é
// vazia (vazia -> ausente); fonte não vazia é aceita e persistida VERBATIM.
// `String.prototype.trim()` usa outro conjunto (inclui U+FEFF; não inclui
// U+001C..U+001F nem U+0085), então não serve pra essa decisão. Um teste de
// backend (tests/test_frontend_input_limits.py) compara esta lista com a do
// Python.
export const BACKEND_WHITESPACE_CODE_POINTS = [
  0x0009, 0x000a, 0x000b, 0x000c, 0x000d, 0x001c, 0x001d, 0x001e, 0x001f, 0x0020, 0x0085, 0x00a0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]

const BACKEND_WHITESPACE = new Set(BACKEND_WHITESPACE_CODE_POINTS)

// Mesma decisão de "vazio" do backend (`not value.strip()`).
export function isBlankLikeBackend(text: string): boolean {
  for (const char of text) {
    if (!BACKEND_WHITESPACE.has(char.codePointAt(0) as number)) return false
  }
  return true
}
