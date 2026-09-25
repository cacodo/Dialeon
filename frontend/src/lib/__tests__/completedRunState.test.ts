import { describe, expect, it } from 'vitest'
import { completedRunState, readCompletedRunState } from '../completedRunState'
import type { CompletedRunResponse } from '../../api/types'

const primary = {
  contract_version: 'primary_answer_plan_v1',
  based_on_verdict_id: 'v-1',
  lead_in: 'Resposta principal:',
  sections: [
    {
      role: 'central_conclusion',
      heading: 'Conclusão central:',
      items: [{ claim_id: 'c1', claim_text: 'Brasília.', verdict_label: 'sustentada pelo debate' }],
    },
  ],
  limitations: [],
  assessed_claim_count: 1,
  selected_claim_count: 1,
  omitted_not_established_count: 0,
  scope_note: 'Seleção apresentacional.',
  rendered_text: 'TEXTO',
}

function makeRun(): CompletedRunResponse {
  return {
    status: 'completed',
    id: 'run-1',
    started_at: '2026-09-06T00:00:00Z',
    completed_at: '2026-09-06T00:00:05Z',
    final_answer: {
      answer_text: 'Brasília é a capital do Brasil.',
      answer_blocks: [
        { kind: 'paragraph', text: 'Resultado:' },
        {
          kind: 'claim_section',
          heading: 'Conclusões sustentadas pelo debate:',
          items: [
            {
              claim_text: 'Brasília.',
              verdict_label: 'sustentada pelo debate',
              explanation: 'e',
              source_relationship_note: null,
            },
          ],
        },
      ],
      limitations: ['Só uma rodada.'],
      status: 'llm_planned',
      editor_model: 'claude-sonnet-5',
      editor_model_identity_source: 'provider_reported',
      judge_confidence: 0.9,
      primary_answer: primary,
      natural_answer: { renderer_contract_version: 'natural_answer_v1', based_on_verdict_id: 'v-1', rendered_text: 'N' },
      natural_answer_presentation_eligible: true,
      linguistic_realization: {
        contract_version: 'linguistic_realization_v1',
        based_on_primary_answer_digest: 'a'.repeat(64),
        blocks: [{ claim_ids: ['c1'], text: 'Brasília.' }],
        rendered_text: 'Brasília.',
      },
      linguistic_realization_presentation_eligible: true,
    },
    accounting: {
      total_input_tokens: 1,
      total_output_tokens: 1,
      estimated_cost_usd: 0.01,
      has_unknown_accounting_components: false,
    },
    config: { question: 'q', enabled_providers: ['openai'], source_text: null },
    provider_execution_policy: null,
    default_model_authority_snapshot: null,
  } as unknown as CompletedRunResponse
}

// Aplica uma mutação num clone profundo do resultado válido.
function mutated(mutate: (run: Record<string, any>) => void): unknown {
  const run = structuredClone(makeRun()) as unknown as Record<string, any>
  mutate(run)
  return { completedRun: run }
}

describe('completedRunState', () => {
  it('ida e volta: um resultado completo é lido de volta intacto pra MESMA Run', () => {
    const run = makeRun()
    expect(readCompletedRunState(completedRunState(run), 'run-1')).toBe(run)
  })

  it('registro mínimo (sem os campos aditivos de apresentação) continua válido', () => {
    const state = mutated((run) => {
      delete run.final_answer.primary_answer
      delete run.final_answer.natural_answer
      delete run.final_answer.natural_answer_presentation_eligible
      delete run.final_answer.linguistic_realization
      delete run.final_answer.linguistic_realization_presentation_eligible
      run.final_answer.answer_blocks = null
    })
    expect(readCompletedRunState(state, 'run-1')).not.toBeNull()
  })

  it('bloco de tipo desconhecido não invalida (o renderizador cai pro texto canônico)', () => {
    const state = mutated((run) => {
      run.final_answer.answer_blocks.push({ kind: 'bloco_futuro' })
    })
    expect(readCompletedRunState(state, 'run-1')).not.toBeNull()
  })

  it('nunca usa o resultado de OUTRA Run (o id tem que ser o da URL)', () => {
    expect(readCompletedRunState(completedRunState(makeRun()), 'run-2')).toBeNull()
    expect(readCompletedRunState(completedRunState(makeRun()), undefined)).toBeNull()
  })

  it.each<[string, unknown]>([
    ['state ausente', null],
    ['state não objeto', 'x'],
    ['outra chave (ex.: origem do Histórico)', { fromHistoryPage: 2 }],
    ['resultado não objeto', { completedRun: 'x' }],
    ['status não concluído', mutated((r) => void (r.status = 'running'))],
    ['sem completed_at', mutated((r) => void delete r.completed_at)],
    ['sem final_answer', mutated((r) => void (r.final_answer = null))],
    ['final_answer sem limitations', mutated((r) => void delete r.final_answer.limitations)],
    ['limitations não é lista de textos', mutated((r) => void (r.final_answer.limitations = [1]))],
    ['sem answer_text', mutated((r) => void delete r.final_answer.answer_text)],
    ['bloco claim_section sem items', mutated((r) => void delete r.final_answer.answer_blocks[1].items)],
    ['item sem explanation', mutated((r) => void delete r.final_answer.answer_blocks[1].items[0].explanation)],
    ['unevaluated_claims malformado', mutated((r) => void (r.final_answer.unevaluated_claims = 'x'))],
    ['primary_answer sem sections', mutated((r) => void delete r.final_answer.primary_answer.sections)],
    ['primary_answer sem limitations', mutated((r) => void delete r.final_answer.primary_answer.limitations)],
    ['natural_answer sem rendered_text', mutated((r) => void (r.final_answer.natural_answer = {}))],
    ['realização sem blocks', mutated((r) => void delete r.final_answer.linguistic_realization.blocks)],
    ['elegibilidade não booleana', mutated((r) => void (r.final_answer.natural_answer_presentation_eligible = 'sim'))],
    ['sem config', mutated((r) => void delete r.config)],
    ['config sem enabled_providers', mutated((r) => void delete r.config.enabled_providers)],
    ['config sem source_text', mutated((r) => void delete r.config.source_text)],
    ['sem accounting', mutated((r) => void (r.accounting = null))],
    ['custo em formato inesperado', mutated((r) => void (r.accounting.estimated_cost_usd = '0.01'))],
  ])('%s → ignorado (a página busca o registro pela URL)', (_label, state) => {
    expect(readCompletedRunState(state, 'run-1')).toBeNull()
  })
})
