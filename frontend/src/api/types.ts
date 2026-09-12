// DTOs HTTP -- espelham exatamente os schemas de app/api/schemas.py.
// Etapa 12, Decision Delta secao 21: tipos do frontend podem representar
// os Literals conhecidos mesmo que o OpenAPI atual exponha alguns como
// string solta no backend -- isso e so tipagem estatica do lado do
// client, nao exige nem implica patch no backend.

export interface TokenUsage {
  input_tokens: number | null
  output_tokens: number | null
}

export interface PricingProvenance {
  source_id: string
  tier: 'standard' | 'long_context'
  input_rate_usd_per_million_tokens: number
  output_rate_usd_per_million_tokens: number
}

export interface ProviderErrorInfo {
  type: string
  message: string
  retryable: boolean
}

export interface ModelResponsePublic {
  id: string
  provider: string
  model: string
  round_number: number
  status: 'success' | 'error'
  response_text: string | null
  usage: TokenUsage | null
  cost_usd: number | null
  pricing_provenance: PricingProvenance | null
  latency_ms: number
  attempts: number
  error: ProviderErrorInfo | null
  created_at: string
}

export interface RoundAccountingPublic {
  total_input_tokens: number
  total_output_tokens: number
  estimated_cost_usd: number
  has_unknown_accounting_components: boolean
}

export interface InitialRoundAudit {
  responses: ModelResponsePublic[]
  successful_count: number
  total_providers: number
  insufficient_data_for_consensus: boolean
  budget_exceeded: boolean
  accounting: RoundAccountingPublic
}

export interface RoundAudit {
  responses: ModelResponsePublic[]
  successful_count: number
  total_participants: number
  accounting: RoundAccountingPublic
}

export interface ClaimSupportPublic {
  model_response_id: string
  provider: string
  model: string
}

// Valores reais do dominio (app/models/domain.py) -- disputed/resolved/
// superseded estao dormentes hoje (nunca produzidos pelo pipeline real),
// mas o tipo os inclui porque sao estados validos do dominio.
export type ClaimStatus = 'active' | 'consensus' | 'disputed' | 'resolved' | 'superseded'

export interface ClaimPublic {
  id: string
  text: string
  source_model_response_id: string | null
  round_introduced: number
  parent_claim_id: string | null
  merged_from_claim_ids: string[]
  status: ClaimStatus
  supporting_model_response_ids: ClaimSupportPublic[]
  total_models_in_round: number
  confidence: number | null
  created_at: string
}

export interface ClaimProcessingAttemptPublic {
  id: string
  operation: string
  round_number: number
  attempt_number: number
  provider: string
  model: string
  target_model_response_id: string | null
  target_claim_ids: string[]
  transport_status: 'success' | 'error'
  transport_error: ProviderErrorInfo | null
  raw_output_text: string | null
  parse_status: string
  parse_error_message: string | null
  usage: TokenUsage | null
  cost_usd: number | null
  pricing_provenance: PricingProvenance | null
  latency_ms: number
  created_at: string
}

export type ClaimVerdict =
  | 'supported'
  | 'partially_supported'
  | 'rejected'
  | 'conflicting'
  | 'unresolved'

export interface ClaimAssessmentPublic {
  claim_id: string
  verdict: ClaimVerdict
  explanation: string
}

export interface JudgeVerdictPublic {
  id: string
  evaluated_through_round: number
  judge_model: string
  claim_assessments: ClaimAssessmentPublic[]
  best_arguments_by: Record<string, string>
  debate_limitations: string[]
  confidence: number
  reasoning: string
  created_at: string
}

export interface JudgeAttemptPublic {
  id: string
  attempt_number: number
  provider: string
  model: string
  transport_status: 'success' | 'error'
  transport_error: ProviderErrorInfo | null
  raw_output_text: string | null
  parse_status: string
  parse_error_message: string | null
  usage: TokenUsage | null
  cost_usd: number | null
  pricing_provenance: PricingProvenance | null
  latency_ms: number
  created_at: string
}

export interface EditorAttemptPublic {
  id: string
  attempt_number: number
  provider: string
  model: string
  transport_status: 'success' | 'error'
  transport_error: ProviderErrorInfo | null
  raw_output_text: string | null
  parse_status: string
  parse_error_message: string | null
  usage: TokenUsage | null
  cost_usd: number | null
  pricing_provenance: PricingProvenance | null
  latency_ms: number
  created_at: string
}

// Etapa 17B: 'llm_planned' é o status de runs novos (LLM escolheu só
// estrutura de apresentação; a aplicação escreveu todo o texto).
// 'llm_composed' segue aceito só pra runs históricos anteriores.
export type FinalAnswerStatus =
  | 'llm_planned'
  | 'llm_composed'
  | 'deterministic_from_verdict'
  | 'deterministic_no_verdict'

export interface FinalAnswerPublic {
  answer_text: string
  limitations: string[]
  status: FinalAnswerStatus
  editor_model: string | null
  judge_confidence: number | null
}

export interface QuorumPublic {
  min_for_debate: number
  min_to_return: number
}

export interface RunConfigPublic {
  question: string
  enabled_providers: string[]
  claim_processor_provider: string
  judge_provider: string
  editor_provider: string
  source_analyzer_provider: string
  source_text: string | null
  max_cost_usd: number
  max_total_tokens: number
  max_output_tokens_per_call: number
  round_dispatch_timeout_seconds: number
  quorum: QuorumPublic
}

export interface AccountingSummary {
  total_input_tokens: number
  total_output_tokens: number
  estimated_cost_usd: number
  has_unknown_accounting_components: boolean
}

export interface RunSummaryResponse {
  id: string
  status: 'completed' | 'insufficient_quorum'
  started_at: string
  ended_at: string
}

export interface RunListResponse {
  runs: RunSummaryResponse[]
  limit: number
  offset: number
}

export interface CompletedRunResponse {
  status: 'completed'
  id: string
  started_at: string
  completed_at: string
  final_answer: FinalAnswerPublic
  accounting: AccountingSummary
  config: RunConfigPublic
}

export interface QuorumFailureRunResponse {
  status: 'insufficient_quorum'
  id: string
  started_at: string
  failed_at: string
  successful_count: number
  total_providers: number
  min_to_return: number
  accounting: RoundAccountingPublic
  config: RunConfigPublic
}

export type RunResponse = CompletedRunResponse | QuorumFailureRunResponse

export interface DebateOutcome {
  skipped_reason: string | null
  cumulative_budget_exceeded: boolean
}

export interface JudgeOutcome {
  verdict_unavailable_reason: string | null
  cumulative_budget_exceeded: boolean
}

export interface EditorOutcome {
  fallback_reason: string | null
  cumulative_budget_exceeded: boolean
}

export interface CompletedRunAudit {
  status: 'completed'
  id: string
  started_at: string
  completed_at: string
  config: RunConfigPublic
  debate_outcome: DebateOutcome
  judge_outcome: JudgeOutcome
  editor_outcome: EditorOutcome
  source_analysis: SourceAnalysisOutcome | null
  initial_round: InitialRoundAudit
  critique_round: RoundAudit | null
  claims: ClaimPublic[]
  claim_processing_attempts: ClaimProcessingAttemptPublic[]
  judge_verdict: JudgeVerdictPublic | null
  judge_attempts: JudgeAttemptPublic[]
  editor_attempts: EditorAttemptPublic[]
  final_answer: FinalAnswerPublic
  accounting: AccountingSummary
}

export interface QuorumFailureAudit {
  status: 'insufficient_quorum'
  id: string
  started_at: string
  failed_at: string
  config: RunConfigPublic
  successful_count: number
  total_providers: number
  min_to_return: number
  round_result: RoundAudit
}

export type RunAuditResponse = CompletedRunAudit | QuorumFailureAudit

export interface ProvidersResponse {
  providers: string[]
}

export interface CreateRunRequest {
  question: string
  enabled_providers: string[]
  source_text: string | null
}

// Etapa 16 -- audit-only. Só tipagem pra consistência; nenhum componente
// exibe isso ainda de forma exaustiva (mesmo tratamento que a Etapa 15
// deu a numeric_verification_attempts).
export interface SourceAnalysisAttemptPublic {
  id: string
  attempt_number: number
  provider: string
  requested_model: string
  model: string
  transport_status: 'success' | 'error'
  parse_status: 'accepted' | 'malformed' | 'not_attempted'
  parse_error_message: string | null
  latency_ms: number
  created_at: string
}

export interface ValidSourceRelationPublic {
  kind: 'relation'
  id: string
  claim_id: string
  relation: 'supports' | 'contradicts' | 'unresolved'
  excerpt: string | null
  excerpt_start: number | null
  excerpt_end: number | null
  created_at: string
}

export interface RejectedSourceEntryPublic {
  kind: 'rejected'
  id: string
  claim_id: string | null
  reason: 'omitted_by_model' | 'duplicate_claim_id' | 'invalid_entry'
  created_at: string
}

export type SourceClaimAnalysisResultPublic = ValidSourceRelationPublic | RejectedSourceEntryPublic

export interface SourceAnalysisOutcome {
  skipped_reason:
    | 'no_claims_to_analyze'
    | 'budget_exhausted_before_source_analysis'
    | 'source_analysis_transport_failed'
    | 'source_analysis_output_invalid'
    | null
  source_analyzer_provider: string
  cumulative_budget_exceeded: boolean
  attempts: SourceAnalysisAttemptPublic[]
  claim_results: SourceClaimAnalysisResultPublic[]
}

export type ErrorCode =
  | 'invalid_provider'
  | 'invalid_request'
  | 'insufficient_quorum'
  | 'run_not_found'
  | 'internal_error'

export interface ErrorBody {
  code: ErrorCode
  message: string
  details: Record<string, unknown> | null
}

export interface ErrorResponse {
  error: ErrorBody
}
