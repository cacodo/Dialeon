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

// T02.2 -- snapshot da política de transporte de provider (timeout +
// retry) vigente no momento em que o Run foi aceito. SIBLING de
// RunConfigPublic, nunca dentro dele -- autoridade de deployment
// distinta da configuração de execução/domínio do Run (ver
// app/models/provider_models.py:ProviderExecutionPolicy). `null` só pra
// runs persistidos antes desta feature existir -- nunca substituído
// pelos defaults atuais.
export interface ProviderExecutionPolicy {
  attempt_timeout_seconds: number
  max_transport_attempts_per_completion: number
}

// Provider Default-Model Snapshot Provenance V1 -- snapshot IMUTÁVEL,
// tomado no aceite, da autoridade de modelo padrão/fallback CONFIGURADA
// pra cada provider autorizado (ver
// app/models/provider_models.py:DefaultModelAuthoritySnapshot). SIBLING
// de RunConfigPublic, mesma disciplina de ProviderExecutionPolicy acima.
//
// NÃO significa que aquele modelo foi solicitado/executado/reportado --
// é observacional/histórico, nunca uma opção de configuração editável
// aqui. `null` só pra runs persistidos antes desta feature existir.
export interface DefaultModelAuthoritySnapshot {
  configured_default_models: Record<string, string>
}

// Provenance FECHADA de um campo `model`/`judge_model`/`editor_model` --
// distingue um identificador de modelo genuinamente REPORTADO pelo
// provider de um substituto de fallback pro modelo solicitado (ver
// app/models/provider_models.py:ModelIdentitySource). `null` é um
// terceiro estado -- história persistida ANTES desta coluna existir --
// nunca confundido com 'requested_fallback' (nunca inferido de
// model === requested_model).
export type ModelIdentitySource = 'provider_reported' | 'requested_fallback'

export interface ModelResponsePublic {
  id: string
  provider: string
  requested_model: string
  model: string
  model_identity_source: ModelIdentitySource | null
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
  model_identity_source: ModelIdentitySource | null
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
  // Correcao pos-revisao independente (HIGH 2) -- projecao DEDUPLICADA
  // de "provider/model" (mesma fonte de verdade que alimenta
  // Claim.supporting_model_ratio no backend, ver app/models/domain.py).
  // NUNCA usar supporting_model_response_ids.length como contagem de
  // participantes -- essa lista e bruta, nao deduplicada por design (o
  // mesmo provider/model respondendo em 2 rodadas aparece 2 vezes ali).
  supporting_models: string[]
  total_models_in_round: number
  // Cross-round claim reconciliation -- null pra toda claim
  // historica/ordinaria de rodada unica; presente so numa claim canonica
  // de reconciliacao cross-round (ver app/models/domain.py).
  support_scope_model_count: number | null
  confidence: number | null
  created_at: string
}

export interface ClaimProcessingAttemptPublic {
  id: string
  operation: string
  round_number: number
  attempt_number: number
  provider: string
  requested_model: string
  model: string
  model_identity_source: ModelIdentitySource | null
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
  judge_model_identity_source: ModelIdentitySource | null
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
  requested_model: string
  model: string
  model_identity_source: ModelIdentitySource | null
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
  requested_model: string
  model: string
  model_identity_source: ModelIdentitySource | null
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
  editor_model_identity_source: ModelIdentitySource | null
  judge_confidence: number | null
}

export interface QuorumPublic {
  min_for_debate: number
  min_to_return: number
}

// Historical Non-Finite Execution-Limit Public Representation V1 --
// os dois únicos campos historicamente alcançáveis com +inf (antes de
// "Deployment Execution Configuration Boundary V1", Settings aceitava
// +inf pra estes dois defaults). O token literal `"positive_infinity"`
// é OUTWARD-ONLY: representa um valor histórico já persistido, nunca
// um formato de input aceito por POST /runs ou por qualquer campo de
// configuração nova. NUNCA converter de volta pra `Infinity` do
// JavaScript nem tratar como uma opção de "sem limite" pra uma nova
// execução -- é presentation de um fato histórico, não uma semântica
// de execução.
export type PositiveExecutionLimitPublic = number | 'positive_infinity'

export interface RunConfigPublic {
  question: string
  enabled_providers: string[]
  claim_processor_provider: string
  judge_provider: string
  editor_provider: string
  source_analyzer_provider: string
  source_text: string | null
  max_cost_usd: PositiveExecutionLimitPublic
  max_total_tokens: number
  max_output_tokens_per_call: number
  // Tetos PRÓPRIOS de agrupamento/reconciliação e do Judge -- ver
  // RunConfigPublic (app/presentation/schemas.py), distintos de
  // max_output_tokens_per_call porque o output cresce com a contagem
  // de claims (cobertura obrigatória, uma entrada/avaliação por claim).
  max_output_tokens_grouping: number
  max_output_tokens_judge: number
  round_dispatch_timeout_seconds: PositiveExecutionLimitPublic
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
  status: 'completed' | 'insufficient_quorum' | 'running' | 'failed'
  started_at: string
  // T02.4 -- null é o único valor honesto pra status="running" (a
  // execução ainda não terminou).
  ended_at: string | null
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
  provider_execution_policy: ProviderExecutionPolicy | null
  default_model_authority_snapshot: DefaultModelAuthoritySnapshot | null
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
  provider_execution_policy: ProviderExecutionPolicy | null
  default_model_authority_snapshot: DefaultModelAuthoritySnapshot | null
}

// T02.4 -- run aceito ainda sem desfecho terminal: em andamento, ou o
// processo morreu antes de terminar (indistinguíveis por design). Sem
// final_answer/accounting -- não existe persistência incremental neste
// slice, então não há nada intermediário real pra expor.
export interface RunningRunResponse {
  status: 'running'
  id: string
  started_at: string
  config: RunConfigPublic
  provider_execution_policy: ProviderExecutionPolicy | null
  default_model_authority_snapshot: DefaultModelAuthoritySnapshot | null
}

// T02.4 -- exceção inesperada durante a execução (nem validação de
// request, nem quórum insuficiente). failure_reason/message já chegam
// sanitizados do backend -- nunca traceback/segredo/texto cru.
export interface FailedRunResponse {
  status: 'failed'
  id: string
  started_at: string
  failed_at: string
  failure_reason: string
  message: string
  config: RunConfigPublic
  provider_execution_policy: ProviderExecutionPolicy | null
  default_model_authority_snapshot: DefaultModelAuthoritySnapshot | null
}

export type RunResponse =
  | CompletedRunResponse
  | QuorumFailureRunResponse
  | RunningRunResponse
  | FailedRunResponse

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
  provider_execution_policy: ProviderExecutionPolicy | null
  default_model_authority_snapshot: DefaultModelAuthoritySnapshot | null
  // Cross-Channel Reconciliation V1 -- null SÓ em execuções persistidas
  // antes deste recurso existir; nunca reinterpretado como
  // status="judge_unavailable" nem como nenhum channel_relationship.
  reconciliation: SourceJudgeReconciliationResultPublic | null
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
  provider_execution_policy: ProviderExecutionPolicy | null
  default_model_authority_snapshot: DefaultModelAuthoritySnapshot | null
}

// T02.4 -- um run "running"/"failed" nunca tem detalhe de auditoria
// alem do que o detail já mostra (ver RunningRunResponse/FailedRunResponse
// acima) -- reusados aqui em vez de tipos "Audit" idênticos.
export type RunAuditResponse =
  | CompletedRunAudit
  | QuorumFailureAudit
  | RunningRunResponse
  | FailedRunResponse

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
  model_identity_source: ModelIdentitySource | null
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

// Cross-Channel Reconciliation V1 -- classificação DETERMINÍSTICA (sem LLM)
// do RELACIONAMENTO entre o canal Judge e o canal Source Analysis por
// claim corrente. Nunca é um veredito de verdade, nunca dá autoridade
// à fonte sobre o Judge.
export type SourceChannelState =
  | 'not_supplied'
  | 'analysis_unavailable'
  | 'entry_rejected'
  | 'supports'
  | 'contradicts'
  | 'unresolved'
  | 'mixed'

export type ChannelRelationship =
  | 'directionally_aligned'
  | 'in_tension'
  | 'source_adds_direction'
  | 'source_unresolved'
  | 'source_channel_conflict'
  | 'not_comparable'

export interface ClaimReconciliationOutcomePublic {
  claim_id: string
  judge_verdict_id: string | null
  source_claim_result_ids: string[]
  source_state: SourceChannelState
  channel_relationship: ChannelRelationship
}

export interface SourceJudgeReconciliationResultPublic {
  contract_version: 'source_judge_reconciliation_v1'
  status: 'complete' | 'judge_unavailable'
  claim_outcomes: ClaimReconciliationOutcomePublic[]
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
