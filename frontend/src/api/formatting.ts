// Camada de apresentação/formatting -- Etapa 12, Decision Delta secao 3
// (continuação): DTO != presentation model. `types.ts` representa o
// contrato HTTP puro; este arquivo traduz esses valores em texto
// legível, sem nunca inventar semântica que o backend não registrou
// (EXPLAIN FROM PROVENANCE, DO NOT INVENT POST-HOC REASONING).

import type {
  ClaimVerdict,
  DebateOutcome,
  EditorOutcome,
  ErrorCode,
  FinalAnswerStatus,
  JudgeOutcome,
} from './types'

/** UNKNOWN NÃO PODE SER FORMATADO COMO ZERO -- distinção explícita entre
 * os 3 estados reais (>0 conhecido, ===0 conhecido, null desconhecido). */
export function formatEstimatedCost(cost: number | null, hasUnknown: boolean): string {
  if (cost === null) {
    return 'Estimativa indisponível'
  }
  const formatted = cost === 0 ? '$0,00 (estimativa conhecida)' : `~$${cost.toFixed(4)}`
  return hasUnknown ? `${formatted} · estimativa parcial (dados incompletos)` : formatted
}

export function formatTokenCount(tokens: number | null): string {
  return tokens === null ? '—' : tokens.toLocaleString('pt-BR')
}

const DEBATE_SKIPPED_REASON_LABELS: Record<string, string> = {
  insufficient_initial_quorum: 'Poucas respostas na rodada inicial para justificar uma crítica.',
  budget_exhausted_before_critique: 'Orçamento esgotado antes da rodada de crítica.',
}

export function formatDebateOutcome(outcome: DebateOutcome): string | null {
  if (outcome.skipped_reason === null) return null
  return DEBATE_SKIPPED_REASON_LABELS[outcome.skipped_reason] ?? outcome.skipped_reason
}

const JUDGE_UNAVAILABLE_REASON_LABELS: Record<string, string> = {
  budget_exhausted_before_judge: 'Orçamento esgotado antes da avaliação do juiz.',
  no_claims_to_judge: 'Não havia afirmações para avaliar.',
  judge_transport_failed: 'Falha de comunicação com o modelo juiz.',
  judge_output_invalid: 'A resposta do juiz não pôde ser interpretada.',
}

export function formatJudgeOutcome(outcome: JudgeOutcome): string | null {
  if (outcome.verdict_unavailable_reason === null) return null
  return (
    JUDGE_UNAVAILABLE_REASON_LABELS[outcome.verdict_unavailable_reason] ??
    outcome.verdict_unavailable_reason
  )
}

const EDITOR_FALLBACK_REASON_LABELS: Record<string, string> = {
  budget_exhausted_before_editor: 'Orçamento esgotado antes da composição final.',
  editor_transport_failed: 'Falha de comunicação com o modelo editor.',
  editor_output_invalid: 'A composição do editor não pôde ser interpretada.',
  judge_verdict_unavailable: 'Resposta final gerada sem avaliação do juiz.',
}

export function formatEditorOutcome(outcome: EditorOutcome): string | null {
  if (outcome.fallback_reason === null) return null
  return EDITOR_FALLBACK_REASON_LABELS[outcome.fallback_reason] ?? outcome.fallback_reason
}

const FINAL_ANSWER_STATUS_LABELS: Record<FinalAnswerStatus, string> = {
  llm_planned: 'Estruturada por um planejador de apresentação a partir da avaliação do juiz',
  llm_composed: 'Composta pelo editor a partir do debate (formato histórico)',
  deterministic_from_verdict: 'Montada automaticamente a partir da avaliação do juiz',
  deterministic_no_verdict: 'Montada automaticamente sem avaliação do juiz',
}

export function formatFinalAnswerStatus(status: FinalAnswerStatus): string {
  return FINAL_ANSWER_STATUS_LABELS[status] ?? status
}

const CLAIM_VERDICT_LABELS: Record<ClaimVerdict, string> = {
  supported: 'Sustentada',
  partially_supported: 'Parcialmente sustentada',
  rejected: 'Rejeitada',
  conflicting: 'Conflitante',
  unresolved: 'Não resolvida',
}

export function formatClaimVerdict(verdict: ClaimVerdict): string {
  return CLAIM_VERDICT_LABELS[verdict] ?? verdict
}

const ERROR_CODE_LABELS: Record<ErrorCode | 'unknown', string> = {
  invalid_provider: 'Um ou mais participantes selecionados não existem.',
  invalid_request: 'Verifique os dados informados.',
  insufficient_quorum: 'Poucos participantes responderam para gerar um resultado.',
  run_not_found: 'Execução não encontrada.',
  internal_error: 'Algo deu errado do nosso lado. Tente novamente.',
  unknown: 'Não foi possível completar a solicitação.',
}

export function formatErrorCode(code: ErrorCode | 'unknown'): string {
  return ERROR_CODE_LABELS[code] ?? ERROR_CODE_LABELS.unknown
}

export function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('pt-BR', {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return iso
  }
}

/** "3 de 3 participantes" -- nunca uma probabilidade/confidence, só uma
 * contagem literal (CONSENSUS != TRUTH). */
export function formatSupportRatio(supportingCount: number, totalInRound: number): string {
  return `${supportingCount} de ${totalInRound} participantes`
}

// Análise de fonte (Etapa 16, patch de visibilidade humana) -- audit-only:
// estes rótulos descrevem só a RELAÇÃO entre uma afirmação e o texto da
// fonte fornecida pelo usuário, nunca um segundo veredito de verdade.
// SOURCE RELATION != TRUTH VERDICT -- nunca "verdadeiro"/"falso"/"provado"/
// "desmentido" aqui (ver app/source_analysis/models.py).
const SOURCE_ANALYSIS_SKIPPED_REASON_LABELS: Record<string, string> = {
  no_claims_to_analyze: 'Não havia afirmações para analisar contra a fonte.',
  budget_exhausted_before_source_analysis: 'Orçamento esgotado antes da análise da fonte.',
  source_analysis_transport_failed: 'Falha de comunicação durante a análise da fonte.',
  source_analysis_output_invalid: 'A análise da fonte não pôde ser interpretada corretamente.',
}

export function formatSourceAnalysisSkippedReason(reason: string): string {
  return SOURCE_ANALYSIS_SKIPPED_REASON_LABELS[reason] ?? reason
}

const SOURCE_RELATION_LABELS: Record<'supports' | 'contradicts' | 'unresolved', string> = {
  supports: 'Segundo a análise, a fonte apoia esta afirmação.',
  contradicts: 'Segundo a análise, a fonte contradiz esta afirmação.',
  unresolved: 'A análise não conseguiu determinar a relação entre a fonte e esta afirmação.',
}

export function formatSourceRelation(relation: 'supports' | 'contradicts' | 'unresolved'): string {
  return SOURCE_RELATION_LABELS[relation] ?? relation
}

const SOURCE_REJECTION_REASON_LABELS: Record<
  'omitted_by_model' | 'duplicate_claim_id' | 'invalid_entry',
  string
> = {
  omitted_by_model: 'A análise não endereçou esta afirmação.',
  duplicate_claim_id: 'A análise devolveu mais de uma entrada para a mesma afirmação (descartada).',
  invalid_entry: 'A entrada da análise não pôde ser validada.',
}

export function formatSourceRejectionReason(
  reason: 'omitted_by_model' | 'duplicate_claim_id' | 'invalid_entry',
): string {
  return SOURCE_REJECTION_REASON_LABELS[reason] ?? reason
}
