// Camada de apresentação/formatting -- Etapa 12, Decision Delta secao 3
// (continuação): DTO != presentation model. `types.ts` representa o
// contrato HTTP puro; este arquivo traduz esses valores em texto
// legível, sem nunca inventar semântica que o backend não registrou
// (EXPLAIN FROM PROVENANCE, DO NOT INVENT POST-HOC REASONING).

import type {
  ChannelRelationship,
  ClaimVerdict,
  DebateOutcome,
  EditorOutcome,
  ErrorCode,
  FinalAnswerStatus,
  JudgeOutcome,
  ModelIdentitySource,
  SourceChannelState,
  TokenUsage,
} from './types'

// Provenance de identidade de modelo (ver ModelIdentitySource, ./types.ts)
// -- `null` é história persistida ANTES desta coluna existir, nunca
// confundida com 'requested_fallback' (nunca inferida a partir de
// model === requested_model).
const MODEL_IDENTITY_SOURCE_LABELS: Record<ModelIdentitySource, string> = {
  provider_reported: 'reportado pelo provider',
  requested_fallback: 'fallback do modelo solicitado',
}

export function formatModelIdentitySource(source: ModelIdentitySource | null): string {
  if (source === null) {
    return 'não registrada (execução anterior a este registro)'
  }
  return MODEL_IDENTITY_SOURCE_LABELS[source] ?? source
}

// Participant Perspectives (UI Slice) -- nome de display do provider,
// nunca o ID enviado/persistido (mesma disciplina de
// ProviderSelector.tsx::displayName). Um mapa FECHADO só pros 3
// providers reais conhecidos hoje (ver app/providers/*.py:
// provider_name) -- um provider novo/desconhecido cai no fallback
// (capitalização simples), nunca quebra nem inventa um nome bonito.
const PROVIDER_DISPLAY_NAME_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Gemini',
}

export function formatProviderName(providerId: string): string {
  return (
    PROVIDER_DISPLAY_NAME_LABELS[providerId] ??
    providerId.charAt(0).toUpperCase() + providerId.slice(1)
  )
}

// Participant Perspectives (UI Slice) -- categoria HUMANA e LIMITADA de
// falha, derivada só de `ProviderErrorInfo.type` (ver
// app/models/provider_models.py::ProviderErrorType, os 6 únicos valores
// reais). NUNCA expõe `error.message`/`retryable`/detalhe de
// transporte -- isso continua só na Auditoria técnica. Um `type`
// desconhecido (schema drift futuro) cai num rótulo honesto, nunca
// finge saber a causa.
const FAILURE_CATEGORY_LABELS: Record<string, string> = {
  timeout: 'tempo limite excedido',
  auth: 'falha de autenticação',
  rate_limit: 'limite de taxa atingido',
  api_error: 'erro do provider',
  malformed_response: 'resposta em formato inesperado',
  unknown: 'motivo não identificado',
}

export function formatFailureCategory(type: string): string {
  return FAILURE_CATEGORY_LABELS[type] ?? 'motivo não identificado'
}

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

// Auditoria técnica (repair pós-revisão adversarial) -- distingue
// "nenhum objeto de uso persistido" (usage === null) de "objeto de uso
// presente, mas com contadores desconhecidos" (usage != null com
// input_tokens/output_tokens null cada um) -- os dois nunca podem
// colapsar pro mesmo "—" visual sem MAIS NENHUM sinal, senão a
// Auditoria técnica perde exatamente a distinção que existe pra
// registrar. `formatTokenCount` acima continua correta pra cada
// CONTADOR individual -- esta função só cobre a presença do objeto.
export function formatUsageRecordPresence(usage: TokenUsage | null): string {
  return usage === null ? 'não registrado (nenhum objeto de uso persistido)' : 'registrado'
}

// Auditoria técnica -- `canonical_model_id === null` significa
// RESOLUÇÃO DIRETA na tabela de preços (a chave provider/modelo bateu
// sem nenhum alias envolvido) -- NUNCA "dado ausente"/"não registrado"
// (ver PricingProvenance.canonical_model_id, app/models/
// provider_models.py). Reinterpretar null aqui como ausência inventaria
// uma semântica de degradação que o contrato não tem.
export function formatPricingCanonicalModelId(canonicalModelId: string | null): string {
  return canonicalModelId === null
    ? 'resolução direta na tabela de preços (sem alias)'
    : `via alias de ${canonicalModelId}`
}

// Auditoria técnica -- valor EXATO de `cost_usd` como persistido, sem
// arredondar valores positivos minúsculos pra algo que pareça zero
// (`formatEstimatedCost` acima usa `.toFixed(4)` pra exibição AMIGÁVEL,
// o que arredondaria 0.00001 pra "0,0000" -- aceitável lá, nunca aqui).
// `null` continua distinto de `0`, que continua distinto de qualquer
// valor positivo, por menor que seja.
export function formatExactCost(cost: number | null): string {
  return cost === null ? 'não registrado' : String(cost)
}

const DEBATE_SKIPPED_REASON_LABELS: Record<string, string> = {
  insufficient_initial_quorum: 'Poucas respostas na rodada inicial para justificar uma crítica.',
  budget_exhausted_before_critique: 'Orçamento esgotado antes da rodada de crítica.',
  // Repair (Run02 claim-extraction exhaustion) -- distinto dos dois
  // acima: havia respostas substantivas suficientes e orçamento
  // suficiente, mas a extração estruturada das afirmações falhou pra
  // TODAS elas.
  all_initial_extractions_failed:
    'Os participantes responderam, mas a extração estruturada das afirmações falhou.',
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
  // Repair (Run02 claim-extraction exhaustion) -- distinto de
  // "no_claims_to_judge": nunca implica que nenhuma informação
  // avaliável existia -- os participantes responderam, só a extração
  // estruturada falhou.
  claim_extraction_failed:
    'Os participantes responderam, mas a extração estruturada das afirmações falhou.',
  // Repair (adversarial review, recheck Finding B) -- distinto TANTO de
  // "claim_extraction_failed" (falha TOTAL) QUANTO de "no_claims_to_judge"
  // (extração completa e genuinamente vazia): aqui a extração
  // ESTRUTURADA ficou incompleta -- algumas respostas de participantes
  // não puderam ser extraídas -- sem nenhuma claim sobrevivente pra
  // avaliação. Redação deliberadamente PRECISA: nunca afirma que os
  // participantes falharam (eles responderam normalmente), nunca afirma
  // ausência de informação avaliável, nunca sugere discordância do
  // Judge, nunca generaliza pra "falha do provider".
  claim_extraction_incomplete:
    'A extração estruturada das afirmações ficou incompleta -- algumas respostas dos ' +
    'participantes não puderam ser extraídas.',
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

// Cross-Channel Reconciliation V1 -- rótulos descrevem só o RELACIONAMENTO
// estrutural entre os canais Judge e Source Analysis pra uma claim, nunca
// um veredito de verdade e nunca autoridade da fonte sobre o Judge
// (CHANNEL RELATIONSHIP != JUDGE VERDICT != TRUTH).
// Repair #5 (revisão adversarial) -- "mixed"/source_channel_conflict cobrem
// QUALQUER combinação de entradas canônicas do lado da fonte que não seja
// redutível a um único estado coerente (não só supports+contradicts --
// também supports+unresolved, direcional+rejeitada, etc.). A anomalia é
// da ANÁLISE DE FONTE (o processo), nunca do texto da fonte em si, que
// pode ser perfeitamente coerente -- por isso o wording nunca diz "a
// fonte contém/produziu resultados conflitantes" nem "conflito interno
// na fonte" (ambos atribuiriam o conflito ao texto/à fonte).
const SOURCE_CHANNEL_STATE_LABELS: Record<SourceChannelState, string> = {
  not_supplied: 'Nenhuma fonte foi fornecida nesta execução.',
  analysis_unavailable: 'A análise da fonte não pôde ser concluída.',
  entry_rejected: 'A análise da fonte não produziu uma relação válida para esta afirmação.',
  supports: 'A fonte apoia esta afirmação.',
  contradicts: 'A fonte contradiz esta afirmação.',
  unresolved: 'A análise não conseguiu determinar a relação da fonte com esta afirmação.',
  mixed: 'A análise da fonte produziu entradas que não puderam ser reduzidas a um único estado coerente para esta afirmação.',
}

export function formatSourceChannelState(state: SourceChannelState): string {
  return SOURCE_CHANNEL_STATE_LABELS[state] ?? state
}

const CHANNEL_RELATIONSHIP_LABELS: Record<ChannelRelationship, string> = {
  directionally_aligned: 'Julgamento e fonte apontam na mesma direção.',
  in_tension: 'Julgamento e fonte apontam em direções opostas.',
  source_adds_direction: 'O julgamento não deu direção; a fonte acrescenta uma direção.',
  source_unresolved: 'A fonte não conseguiu determinar uma direção para esta afirmação.',
  source_channel_conflict:
    'A análise da fonte produziu entradas que não puderam ser reduzidas a um único estado coerente.',
  not_comparable: 'Julgamento e fonte não são comparáveis nesta execução.',
}

export function formatChannelRelationship(relationship: ChannelRelationship): string {
  return CHANNEL_RELATIONSHIP_LABELS[relationship] ?? relationship
}

/**
 * UI Slice 2 -- quebra puramente TIPOGRÁFICA de `answer_text` em blocos,
 * pra leitura longa (parágrafos com respiro visual em vez de um único
 * bloco `pre-wrap`). Divide só em linhas em branco literais (o mesmo
 * separador que `_render_final_answer_text` já usa entre seções, ver
 * app/editor/compose.py) -- NUNCA interpreta "- " como marcador de lista,
 * nem promove nenhuma linha a heading: nenhuma estrutura nova é inventada
 * sobre um texto cujo formato não é parte do contrato de tipos
 * (`answer_text: string`). Quebras de linha simples DENTRO de um bloco
 * são preservadas verbatim pelo chamador via `white-space: pre-wrap`.
 */
export function splitAnswerParagraphs(answerText: string): string[] {
  return answerText
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter((block) => block.length > 0)
}
