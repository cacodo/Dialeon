import { describe, expect, it } from 'vitest'
import { containsUnsafeInteger } from '../numericFidelity'

// Os valores chegam do backend como TEXTO JSON e passam pelo JSON.parse do
// cliente -- exatamente o caminho real de `raw_entry`.
const parsed = (text: string): unknown => JSON.parse(text)

describe('containsUnsafeInteger', () => {
  it.each([
    ['0'],
    ['-3'],
    ['1.5'],
    ['9007199254740991'], // 2^53 - 1: ainda exato
    ['-9007199254740991'],
    ['{"a": [1, 2.25, {"b": -7}], "s": "9223372036854775807"}'], // string não é número
    ['null'],
    ['[]'],
  ])('%s: sem inteiro inseguro', (text) => {
    expect(containsUnsafeInteger(parsed(text))).toBe(false)
  })

  it.each([
    ['9007199254740992'], // 2^53
    ['9007199254740993'], // 2^53 + 1 -> já arredondado pelo parse
    ['9223372036854775807'], // int64 max
    ['-9223372036854775808'], // int64 min
    ['{"x": [1, {"y": 9223372036854775807}]}'], // aninhado
    ['[{"a": 1}, [[-9007199254740993]]]'],
  ])('%s: contém inteiro acima de 2^53 - 1', (text) => {
    expect(containsUnsafeInteger(parsed(text))).toBe(true)
  })

  it('o arredondamento que a nota descreve acontece de fato no parse', () => {
    expect(parsed('9007199254740993')).toBe(9007199254740992)
    expect(String(parsed('9223372036854775807'))).not.toBe('9223372036854775807')
  })
})
