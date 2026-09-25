// Fragmentos estruturados brutos do provider (ex.: `raw_entry`) chegam aqui
// já passados por `JSON.parse`, que representa todo número como `Number`
// (double IEEE-754). Inteiros de magnitude acima de 2^53 - 1
// (`Number.MAX_SAFE_INTEGER`) podem ter sido ARREDONDADOS nesse parse -- o
// backend aceita inteiros até int64 (2^63 - 1). Não há como recuperar o valor
// exato depois do parse, então a apresentação só pode avisar que a
// fidelidade numérica não é garantida; a evidência exata é o texto integral
// do provider no attempt correspondente.

// Mesmo teto de valores do contrato de fragmento do backend
// (MAX_AUDIT_FRAGMENT_NODES, app/audit_fragment.py) com folga: a varredura é
// iterativa e para ao exceder, nunca recursiva.
const MAX_VISITED_VALUES = 20_000

export function containsUnsafeInteger(value: unknown): boolean {
  const stack: unknown[] = [value]
  let visited = 0
  while (stack.length > 0) {
    visited += 1
    if (visited > MAX_VISITED_VALUES) return false
    const item = stack.pop()
    if (typeof item === 'number') {
      if (Number.isInteger(item) && !Number.isSafeInteger(item)) return true
    } else if (Array.isArray(item)) {
      stack.push(...item)
    } else if (item !== null && typeof item === 'object') {
      stack.push(...Object.values(item as Record<string, unknown>))
    }
  }
  return false
}
