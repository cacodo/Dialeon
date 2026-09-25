import { describe, expect, it } from 'vitest'
import { completedRunState, readCompletedRunState } from '../completedRunState'
import type { CompletedRunResponse } from '../../api/types'

const run = {
  status: 'completed',
  id: 'run-1',
  final_answer: { answer_text: 'x' },
  config: { question: 'q', enabled_providers: ['openai'] },
  accounting: { estimated_cost_usd: 0 },
} as unknown as CompletedRunResponse

describe('completedRunState', () => {
  it('ida e volta: o resultado entregue é lido de volta intacto pra MESMA Run', () => {
    expect(readCompletedRunState(completedRunState(run), 'run-1')).toBe(run)
  })

  it('nunca usa o resultado de OUTRA Run (o id tem que ser o da URL)', () => {
    expect(readCompletedRunState(completedRunState(run), 'run-2')).toBeNull()
    expect(readCompletedRunState(completedRunState(run), undefined)).toBeNull()
  })

  it.each([
    ['state ausente', null],
    ['state não objeto', 'x'],
    ['outra chave (ex.: origem do Histórico)', { fromHistoryPage: 2 }],
    ['resultado não objeto', { completedRun: 'x' }],
    ['status não concluído', { completedRun: { ...run, status: 'running' } }],
    ['sem final_answer', { completedRun: { ...run, final_answer: null } }],
    ['sem config', { completedRun: { ...run, config: undefined } }],
    ['config sem enabled_providers', { completedRun: { ...run, config: { question: 'q' } } }],
    ['sem accounting', { completedRun: { ...run, accounting: null } }],
  ])('%s → ignorado (a página busca o registro)', (_label, state) => {
    expect(readCompletedRunState(state, 'run-1')).toBeNull()
  })
})
