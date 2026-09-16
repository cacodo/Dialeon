"""
Contratos públicos compartilhados -- movidos de `app/api/schemas.py`
pra cá na Etapa 14 (patch de revisão, T19A.1): originalmente descritos
como "Schemas HTTP", mas seu conteúdo real (Pydantic puro, sem FastAPI,
sem Settings/secrets) é consumido tanto por `app/api/` (rotas HTTP)
quanto por `app/cli/` (comandos embarcados) -- os dois são clientes do
MESMO contrato de apresentação, nenhum importa o outro.
`app/api/schemas.py` continua existindo como re-export fino, só pra não
forçar churn em `routes.py`/`error_handlers.py` além do necessário.

Dedicados, não aliases de domínio/storage (Decision Delta secao 16) --
exceto `TokenUsage`/`PricingProvenance`/`ProviderErrorInfo`
(app/models/provider_models.py), pequenos value objects estáveis, sem
lógica de domínio, sem segredo, reusados diretamente por conveniência
real (não por preguiça).

`RunResponse`/`RunAuditResponse` são uniões discriminadas por `status` --
o mesmo padrão já usado internamente por
`CompletedRunRecord`/`QuorumFailureRecord` (app/storage/records.py),
espelhado aqui como contrato público (HTTP e CLI).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.provider_models import (
    ModelIdentitySource,
    PricingProvenance,
    ProviderErrorInfo,
    ProviderExecutionPolicy,
    TokenUsage,
)
from app.models.request_provenance import RequestProvenance
from app.orchestrator.config import _normalize_and_validate_source_text, validate_question
from app.reconciliation.models import ChannelRelationship, SourceChannelState

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class CreateRunRequest(BaseModel):
    """Contrato de criação de Run -- deliberadamente pequeno (Decision
    Delta secao 3). Usado por `POST /runs` e pela CLI (`dialeon run`,
    Etapa 14) como a MESMA validação de forma (pergunta não-vazia,
    providers não-vazios/sem duplicata) -- nenhum dos dois reimplementa
    essa regra. Tudo além de question/enabled_providers vem de Settings
    via RunConfig.from_settings.

    `source_text` (Etapa 16): validação antecipada aqui usa a MESMA
    função canônica de `RunConfig` (`_normalize_and_validate_source_text`,
    app/orchestrator/config.py) -- nunca um segundo limite numérico que
    pudesse divergir. Isso só existe pra devolver um erro HTTP/CLI limpo
    e cedo; `RunConfig` continua sendo a autoridade real (é diretamente
    construível, não pode depender deste schema ter rodado antes).

    `question` (Accepted Question Size Boundary V1): mesma disciplina --
    `validate_question` (app/orchestrator/config.py) é a ÚNICA regra
    (vazio/só-espaço-em-branco + teto de `MAX_QUESTION_CHARACTERS`),
    reusada aqui e em `CouncilExecutionService.run()` (boundary de
    aceite autoritativa pra chamadores diretos do service), nunca
    reimplementada. Diferente de `source_text`, `RunConfig.question`
    NUNCA aplica esta regra como field_validator próprio -- ver
    docstring do campo em `RunConfig` pra por que (compatibilidade com
    dado histórico)."""

    model_config = _CONFIG

    question: str = Field(min_length=1)
    enabled_providers: list[str] = Field(min_length=1)
    source_text: str | None = None

    @field_validator("source_text")
    @classmethod
    def _source_text_normalized_and_bounded(cls, value: str | None) -> str | None:
        return _normalize_and_validate_source_text(value)

    @field_validator("question")
    @classmethod
    def _question_bounded_and_not_blank(cls, value: str) -> str:
        return validate_question(value)

    @field_validator("enabled_providers")
    @classmethod
    def _enabled_providers_has_no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("enabled_providers não pode conter duplicatas")
        return value


class ModelResponsePublic(BaseModel):
    model_config = _CONFIG

    id: str
    provider: str
    requested_model: str
    model: str
    # Provenance de `model` -- ver ModelIdentitySource
    # (app/models/provider_models.py). `None` só pra runs persistidos
    # antes desta coluna existir -- nunca substituído por inferência.
    model_identity_source: ModelIdentitySource | None
    round_number: int
    status: Literal["success", "error"]
    response_text: str | None
    usage: TokenUsage | None
    cost_usd: float | None
    pricing_provenance: PricingProvenance | None
    latency_ms: int
    attempts: int
    error: ProviderErrorInfo | None
    # Etapa 17A (B3) / 17A.1 (Objetivo B)
    had_uncertain_prior_attempts: bool
    provider_finish_reason: str | None
    # Provider-Neutral Request Provenance V1 -- ver docstring de
    # RequestProvenance (app/models/request_provenance.py). `None` só
    # pra runs persistidos antes deste slice existir -- nunca
    # regenerado a partir de builders atuais.
    request_provenance: RequestProvenance | None
    created_at: datetime


class RoundAccountingPublic(BaseModel):
    """Accounting de uma rodada -- nunca recalculado, sempre copiado dos
    campos já derivados no domínio (Decision Delta secao 10).

    `estimated_cost_usd` (não `total_cost_usd`, Etapa 11 patch final):
    o domínio calcula isso via PricingRegistry/tabela pública -- é
    ESTIMATIVA, nunca cobrança real/faturada/reportada pelo provider.
    O nome no contrato HTTP deixa isso explícito, mesmo sem renomear o
    campo equivalente no domínio/storage (fora de escopo aqui)."""

    model_config = _CONFIG

    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float
    has_unknown_accounting_components: bool


class InitialRoundAudit(BaseModel):
    model_config = _CONFIG

    responses: list[ModelResponsePublic]
    successful_count: int
    total_providers: int
    insufficient_data_for_consensus: bool
    budget_exceeded: bool
    accounting: RoundAccountingPublic


class RoundAudit(BaseModel):
    """Rodada "crua" (RoundResult) -- SEM `insufficient_data_for_consensus`/
    `budget_exceeded`, que são exclusivos de `InitialResponsesResult` e
    nunca chegam a ser calculados quando o quórum falha
    (`_apply_quorum_and_budget` levanta a exceção ANTES disso). Usado só
    pra `QuorumFailureAudit.round_result` -- não confundir com
    `InitialRoundAudit`, que é estritamente mais rico e só existe pra
    runs que passaram no quórum."""

    model_config = _CONFIG

    responses: list[ModelResponsePublic]
    successful_count: int
    total_participants: int
    accounting: RoundAccountingPublic


class ClaimSupportPublic(BaseModel):
    model_config = _CONFIG

    model_response_id: str
    provider: str
    model: str
    # Provenance de `model` -- ver ModelIdentitySource
    # (app/models/provider_models.py). Exposta diretamente aqui (nunca
    # exigindo um join com ModelResponsePublic pra descobrir uma
    # provenance que este próprio registro já carrega) -- `None` só pra
    # supports persistidos antes desta coluna existir.
    model_identity_source: ModelIdentitySource | None


class ClaimPublic(BaseModel):
    model_config = _CONFIG

    id: str
    text: str
    source_model_response_id: str | None
    round_introduced: int
    parent_claim_id: str | None
    merged_from_claim_ids: list[str]
    status: str
    supporting_model_response_ids: list[ClaimSupportPublic]
    # Correção pós-revisão independente (HIGH 2) -- projeção DEDUPLICADA
    # de "provider/model" (o mesmo `Claim.supporting_models` computed
    # field já usado internamente, ver app/models/domain.py) -- exposta
    # publicamente pra que o numerador de participantes exibido em
    # QUALQUER consumidor (frontend incluído) nunca precise reimplementar
    # a lógica de deduplicação a partir de `supporting_model_response_ids`
    # (a lista bruta, NUNCA deduplicada -- um mesmo provider/model
    # respondendo em 2 rodadas aparece 2 vezes ali, por design, pra
    # preservar o histórico de auditoria completo). Uma fonte de verdade
    # só: este campo É a mesma projeção que já alimenta
    # `supporting_model_ratio` no domínio, nunca uma segunda
    # implementação incompatível.
    supporting_models: list[str]
    total_models_in_round: int
    # Cross-round claim reconciliation -- ver Claim.support_scope_model_count
    # (app/models/domain.py). `None` pra toda claim histórica/ordinária de
    # rodada única (comportamento de sempre, `total_models_in_round` é o
    # denominador); presente só numa claim canônica de reconciliação
    # cross-round, onde carrega o universo de suporte real.
    support_scope_model_count: int | None
    confidence: float | None
    created_at: datetime


class ClaimProcessingAttemptPublic(BaseModel):
    model_config = _CONFIG

    id: str
    operation: str
    round_number: int
    attempt_number: int
    provider: str
    requested_model: str
    model: str
    model_identity_source: ModelIdentitySource | None
    target_model_response_id: str | None
    target_claim_ids: list[str]
    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None
    raw_output_text: str | None
    parse_status: str
    parse_error_message: str | None
    usage: TokenUsage | None
    cost_usd: float | None
    pricing_provenance: PricingProvenance | None
    latency_ms: int
    had_uncertain_prior_attempts: bool
    provider_finish_reason: str | None
    # Provider-Neutral Request Provenance V1 -- ver
    # ModelResponsePublic.request_provenance acima.
    request_provenance: RequestProvenance | None
    created_at: datetime


class ArithmeticAssertionPublic(BaseModel):
    """Etapa 15 — asserção normalizada exata que foi avaliada. Strings
    decimais (nunca número JSON), mesmo contrato do domínio."""

    model_config = _CONFIG

    kind: Literal["arithmetic"]
    left: str
    operator: Literal["+", "-", "*", "/"]
    right: str
    asserted_result: str


class DeterministicVerificationAttemptPublic(BaseModel):
    """Etapa 15 — audit-first: os 4 estados reais aparecem aqui
    (`invalid_proposal`/`computation_failed` são audit-only, nunca
    chegam ao Judge — ver judge/context.py)."""

    model_config = _CONFIG

    id: str
    claim_id: str
    state: Literal["invalid_proposal", "computation_failed", "supports", "contradicts"]
    raw_proposal: Any
    assertion: ArithmeticAssertionPublic | None
    computed_result: str | None
    created_at: datetime


class SourceAnalysisAttemptPublic(BaseModel):
    """Etapa 16 — espelha ClaimProcessingAttemptPublic/JudgeAttemptPublic,
    sem `inconsistent_references` em parse_status."""

    model_config = _CONFIG

    id: str
    attempt_number: int
    provider: str
    requested_model: str
    model: str
    model_identity_source: ModelIdentitySource | None
    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None
    raw_output_text: str | None
    parse_status: Literal["accepted", "malformed", "not_attempted"]
    parse_error_message: str | None
    usage: TokenUsage | None
    cost_usd: float | None
    pricing_provenance: PricingProvenance | None
    latency_ms: int
    had_uncertain_prior_attempts: bool
    provider_finish_reason: str | None
    # Provider-Neutral Request Provenance V1 -- ver
    # ModelResponsePublic.request_provenance acima.
    request_provenance: RequestProvenance | None
    created_at: datetime


class ValidSourceRelationPublic(BaseModel):
    """Etapa 16 — conclusão epistêmica real (supports/contradicts/
    unresolved). Excerpt sempre verificado mecanicamente como substring
    exata da fonte original antes de chegar aqui — nunca confiado da LLM."""

    model_config = _CONFIG

    kind: Literal["relation"]
    id: str
    claim_id: str
    relation: Literal["supports", "contradicts", "unresolved"]
    excerpt: str | None
    excerpt_start: int | None
    excerpt_end: int | None
    created_at: datetime


class RejectedSourceEntryPublic(BaseModel):
    """Etapa 16 — NUNCA uma relação epistêmica (ver docstring do domínio,
    app/source_analysis/models.py). `claim_id=None` quando a entrada nem
    corresponde a uma claim corrente conhecida."""

    model_config = _CONFIG

    kind: Literal["rejected"]
    id: str
    claim_id: str | None
    reason: Literal["omitted_by_model", "duplicate_claim_id", "invalid_entry"]
    raw_entry: Any
    created_at: datetime


SourceClaimAnalysisResultPublic = Annotated[
    Union[ValidSourceRelationPublic, RejectedSourceEntryPublic], Field(discriminator="kind")
]


class ClaimAssessmentPublic(BaseModel):
    model_config = _CONFIG

    claim_id: str
    verdict: str
    explanation: str


class JudgeVerdictPublic(BaseModel):
    model_config = _CONFIG

    id: str
    evaluated_through_round: int
    judge_model: str
    judge_model_identity_source: ModelIdentitySource | None
    claim_assessments: list[ClaimAssessmentPublic]
    best_arguments_by: dict[str, str]
    debate_limitations: list[str]
    confidence: float
    reasoning: str
    created_at: datetime


class JudgeAttemptPublic(BaseModel):
    model_config = _CONFIG

    id: str
    attempt_number: int
    provider: str
    requested_model: str
    model: str
    model_identity_source: ModelIdentitySource | None
    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None
    raw_output_text: str | None
    parse_status: str
    parse_error_message: str | None
    usage: TokenUsage | None
    cost_usd: float | None
    pricing_provenance: PricingProvenance | None
    latency_ms: int
    had_uncertain_prior_attempts: bool
    provider_finish_reason: str | None
    # Provider-Neutral Request Provenance V1 -- ver
    # ModelResponsePublic.request_provenance acima.
    request_provenance: RequestProvenance | None
    created_at: datetime


class EditorAttemptPublic(BaseModel):
    model_config = _CONFIG

    id: str
    attempt_number: int
    provider: str
    requested_model: str
    model: str
    model_identity_source: ModelIdentitySource | None
    transport_status: Literal["success", "error"]
    transport_error: ProviderErrorInfo | None
    raw_output_text: str | None
    parse_status: str
    parse_error_message: str | None
    usage: TokenUsage | None
    cost_usd: float | None
    pricing_provenance: PricingProvenance | None
    latency_ms: int
    had_uncertain_prior_attempts: bool
    provider_finish_reason: str | None
    # Provider-Neutral Request Provenance V1 -- ver
    # ModelResponsePublic.request_provenance acima.
    request_provenance: RequestProvenance | None
    created_at: datetime


class FinalAnswerPublic(BaseModel):
    model_config = _CONFIG

    answer_text: str
    limitations: list[str]
    # Etapa 17B -- "llm_planned" é o status de runs novos (LLM escolheu
    # EditorPlan, aplicação renderizou o texto); "llm_composed" segue
    # aceito só pra leitura de runs históricos anteriores (ver
    # app/editor/result.py, FinalAnswer.status).
    status: Literal[
        "llm_planned", "llm_composed", "deterministic_from_verdict", "deterministic_no_verdict"
    ]
    editor_model: str | None
    editor_model_identity_source: ModelIdentitySource | None
    judge_confidence: float | None


class QuorumPublic(BaseModel):
    model_config = _CONFIG

    min_for_debate: int
    min_to_return: int


# Historical Non-Finite Execution-Limit Public Representation V1.
#
# Antes de "Deployment Execution Configuration Boundary V1", `Settings`
# aceitava `+inf` pra `default_max_cost_usd`/
# `orchestrator_round_dispatch_timeout_seconds` -- então `RunConfig`
# (`Field(gt=0)`, sem `allow_inf_nan=False`) podia legitimamente
# persistir `max_cost_usd=+inf`/`round_dispatch_timeout_seconds=+inf`
# através dos dois roots de execução suportados (POST /runs, `dialeon
# run`). `-inf`/NaN NUNCA foram historicamente alcançáveis por esse
# caminho (`gt=0` já rejeitava os dois -- `-inf > 0` e `nan > 0` são
# ambos False) -- só `+inf` é um estado histórico genuíno.
#
# `null` JÁ tinha um significado diferente (nenhum destes dois campos é
# opcional em RunConfig -- nunca foram nullable), então nunca pode ser
# reaproveitado pra significar "+inf histórico" -- isso apagaria a
# distinção entre "não registrado" e "positivo infinito, de fato".
#
# `"positive_infinity"` é o ÚNICO token de compatibilidade OUTWARD
# (nunca um formato de INPUT novo -- RunConfig/Settings/criação de Run
# via API/CLI continuam finito-apenas, sem parsear este token de
# volta). O tipo público abaixo FALHA FECHADO pra qualquer outro estado
# não-finito (`-inf`, NaN) -- mesmo se alguém tentar construir
# `RunConfigPublic` diretamente com esses valores, nunca só através do
# mapper.
PositiveExecutionLimitPublic = Annotated[float, Field(allow_inf_nan=False)] | Literal[
    "positive_infinity"
]


class RunConfigPublic(BaseModel):
    """Subconjunto público de RunConfig -- nomes de provider e budgets,
    nunca segredo (Decision Delta secao 12). Etapa 11 patch final:
    inclui todos os campos não-secretos que alteram materialmente o
    comportamento da execução (max_output_tokens_per_call,
    round_dispatch_timeout_seconds, quorum completo) -- antes omitidos,
    perdendo capacidade de explicar decisões históricas via audit."""

    model_config = _CONFIG

    question: str
    enabled_providers: list[str]
    claim_processor_provider: str
    judge_provider: str
    editor_provider: str
    # Etapa 16 -- nunca segredo (é texto colado pelo próprio usuário,
    # não credencial); None quando nenhuma fonte foi fornecida.
    source_analyzer_provider: str
    source_text: str | None
    # Historical Non-Finite Execution-Limit Public Representation V1 --
    # ver comentário acima de `PositiveExecutionLimitPublic`. Só estes
    # dois campos (os únicos historicamente alcançáveis com +inf) usam
    # este tipo -- nenhum outro campo numérico público é enfraquecido.
    max_cost_usd: PositiveExecutionLimitPublic
    max_total_tokens: int
    max_output_tokens_per_call: int
    # Etapa 17A.2 -- tetos PRÓPRIOS de agrupamento/Judge (ver
    # RunConfig.max_output_tokens_grouping/max_output_tokens_judge),
    # materialmente diferentes de max_output_tokens_per_call -- omiti-los
    # aqui perderia capacidade de explicar historicamente por que um
    # agrupamento/Judge específico truncou ou não.
    max_output_tokens_grouping: int
    max_output_tokens_judge: int
    # Renomeado de overall_timeout_seconds (clarificação de contrato de
    # execução) -- limita o dispatch paralelo de UMA rodada, reinicia a
    # cada rodada, nunca a execução inteira do Council. Ver
    # RunConfig.round_dispatch_timeout_seconds.
    round_dispatch_timeout_seconds: PositiveExecutionLimitPublic
    quorum: QuorumPublic


class AccountingSummary(BaseModel):
    """Accounting agregado top-level -- copiado de
    CouncilRunResult.total_*/has_unknown_accounting_components, nunca
    recalculado (principio 7). `estimated_cost_usd` -- ver
    RoundAccountingPublic, mesma justificativa."""

    model_config = _CONFIG

    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float
    has_unknown_accounting_components: bool


class RunSummaryResponse(BaseModel):
    """Dedicado -- NÃO é `storage.records.RunSummary` reexportado
    (Decision Delta secao 9).

    T02.4: `ended_at=None` é o único valor honesto pra `status="running"`
    -- a execução ainda não terminou (ou o processo morreu antes de
    terminar; as duas situações são indistinguíveis por design, ver
    `app.storage.models.AcceptedRunRow`), então não existe timestamp de
    fim nenhum pra reportar."""

    model_config = _CONFIG

    id: str
    status: Literal["completed", "insufficient_quorum", "running", "failed"]
    started_at: datetime
    ended_at: datetime | None


class ProvidersResponse(BaseModel):
    """`GET /providers` -- Etapa 12. Só identificadores selecionáveis
    atualmente válidos, vindos de `AppComponents.providers` (bootstrap
    real) -- nunca hardcoded, nunca expõe instância/config/segredo."""

    model_config = _CONFIG

    providers: list[str]


class RunListResponse(BaseModel):
    model_config = _CONFIG

    runs: list[RunSummaryResponse]
    limit: int
    offset: int


class CompletedRunResponse(BaseModel):
    model_config = _CONFIG

    status: Literal["completed"] = "completed"
    id: str
    started_at: datetime
    completed_at: datetime
    final_answer: FinalAnswerPublic
    accounting: AccountingSummary
    config: RunConfigPublic
    # T02.2 -- SIBLING de `config`, nunca dentro de `RunConfigPublic`:
    # política de transporte de provider é uma autoridade de deployment
    # distinta da configuração de execução/domínio do Run (ver
    # `app.models.provider_models.ProviderExecutionPolicy`). `None` só
    # pra runs persistidos antes desta coluna existir -- nunca
    # substituído pelos defaults atuais.
    provider_execution_policy: ProviderExecutionPolicy | None


class QuorumFailureRunResponse(BaseModel):
    model_config = _CONFIG

    status: Literal["insufficient_quorum"] = "insufficient_quorum"
    id: str
    started_at: datetime
    failed_at: datetime
    successful_count: int
    total_providers: int
    min_to_return: int
    accounting: RoundAccountingPublic
    config: RunConfigPublic
    # T02.2 -- ver docstring de CompletedRunResponse.provider_execution_policy.
    provider_execution_policy: ProviderExecutionPolicy | None


class RunningRunResponse(BaseModel):
    """T02.4 -- um run aceito que ainda não tem desfecho terminal:
    honestamente incompleto (em andamento, ou processo morreu antes de
    terminar -- indistinguíveis por design, ver
    `app.storage.records.AcceptedRunRecord`). Sem `final_answer`/
    `accounting` -- não existe persistência incremental neste slice
    (fora de escopo), então não há nada intermediário real pra expor
    além de identidade + configuração aceita."""

    model_config = _CONFIG

    status: Literal["running"] = "running"
    id: str
    started_at: datetime
    config: RunConfigPublic
    # T02.2 -- ver docstring de CompletedRunResponse.provider_execution_policy.
    # Um run "running" NOVO sempre tem valor concreto (mesma garantia de
    # `save_accepted`); `None` só numa linha legada pré-upgrade.
    provider_execution_policy: ProviderExecutionPolicy | None


class FailedRunResponse(BaseModel):
    """T02.4 -- exceção inesperada durante a execução (nem validação de
    request, nem quórum insuficiente -- ver
    `app.application.service.CouncilExecutionService.run`).
    `failure_reason` é uma classificação interna estável (nome da classe
    da exceção) + mensagem genérica fixa -- NUNCA traceback, segredo, ou
    texto cru do provider/LLM (item 6 do contrato T02.4)."""

    model_config = _CONFIG

    status: Literal["failed"] = "failed"
    id: str
    started_at: datetime
    failed_at: datetime
    failure_reason: str
    message: str
    config: RunConfigPublic
    # T02.2 -- ver docstring de CompletedRunResponse.provider_execution_policy.
    provider_execution_policy: ProviderExecutionPolicy | None


RunResponse = Annotated[
    Union[CompletedRunResponse, QuorumFailureRunResponse, RunningRunResponse, FailedRunResponse],
    Field(discriminator="status"),
]


class DebateOutcome(BaseModel):
    """Espelha os campos ARMAZENADOS (não computed_field) de DebateResult
    que explicam por que a crítica ocorreu ou não (Etapa 11 patch final,
    Parte A)."""

    model_config = _CONFIG

    skipped_reason: str | None
    cumulative_budget_exceeded: bool


class JudgeOutcome(BaseModel):
    """Espelha os campos ARMAZENADOS de JudgeResult que explicam por que
    o veredito existe ou não."""

    model_config = _CONFIG

    verdict_unavailable_reason: str | None
    cumulative_budget_exceeded: bool


class EditorOutcome(BaseModel):
    """Espelha os campos ARMAZENADOS de EditorResult que explicam se a
    resposta final veio de composição LLM ou de algum fallback
    determinístico, e qual."""

    model_config = _CONFIG

    fallback_reason: str | None
    cumulative_budget_exceeded: bool


class SourceAnalysisOutcome(BaseModel):
    """Etapa 16 -- espelha os campos ARMAZENADOS de SourceAnalysisResult.
    `None` no nível de `CompletedRunAudit.source_analysis` inteiro
    significa "nenhuma fonte foi fornecida" (a análise nem existe) --
    diferente deste objeto existir com `skipped_reason` preenchido
    (fonte fornecida, análise pulada/falhada)."""

    model_config = _CONFIG

    skipped_reason: (
        Literal[
            "no_claims_to_analyze",
            "budget_exhausted_before_source_analysis",
            "source_analysis_transport_failed",
            "source_analysis_output_invalid",
        ]
        | None
    )
    source_analyzer_provider: str
    cumulative_budget_exceeded: bool
    attempts: list[SourceAnalysisAttemptPublic]
    claim_results: list[SourceClaimAnalysisResultPublic]


class ClaimReconciliationOutcomePublic(BaseModel):
    """Cross-Channel Reconciliation V1 -- resultado por-claim-corrente.
    Referencia registros canônicos só por ID (`source_claim_result_ids`)
    -- nunca duplica texto de claim/explicação do Judge/excerto de fonte
    (já disponíveis via `claims`/`judge_verdict`/`source_analysis` no
    mesmo `CompletedRunAudit`)."""

    model_config = _CONFIG

    claim_id: str
    judge_verdict_id: str | None
    source_claim_result_ids: tuple[str, ...]
    source_state: SourceChannelState
    channel_relationship: ChannelRelationship


class SourceJudgeReconciliationResultPublic(BaseModel):
    """Cross-Channel Reconciliation V1 -- contrato `contract_version`.
    Distingue explicitamente os 3 estados possíveis no nível de
    `CompletedRunAudit.reconciliation`:

    - Este campo é `None`: execução histórica persistida ANTES deste
      slice existir -- reconciliação estruturada nunca foi computada,
      NUNCA reinterpretado como `status="judge_unavailable"` nem como
      nenhum `channel_relationship="not_comparable"` por claim.
    - `status="complete"`: Judge disponível, toda claim corrente tem uma
      comparação direcional (ou `not_comparable`/`source_unresolved`/
      `source_channel_conflict` quando aplicável).
    - `status="judge_unavailable"`: Judge indisponível pra esta execução
      -- toda claim corrente ainda recebe um outcome, com
      `channel_relationship="not_comparable"` e `source_state` honesto
      (nunca apagado só porque o Judge falhou)."""

    model_config = _CONFIG

    contract_version: Literal["source_judge_reconciliation_v1"]
    status: Literal["complete", "judge_unavailable"]
    claim_outcomes: list[ClaimReconciliationOutcomePublic]


class CompletedRunAudit(BaseModel):
    model_config = _CONFIG

    status: Literal["completed"] = "completed"
    id: str
    started_at: datetime
    completed_at: datetime
    config: RunConfigPublic
    debate_outcome: DebateOutcome
    judge_outcome: JudgeOutcome
    editor_outcome: EditorOutcome
    # Etapa 16 -- None SÓ quando nenhuma fonte foi fornecida.
    source_analysis: SourceAnalysisOutcome | None
    initial_round: InitialRoundAudit
    critique_round: RoundAudit | None
    claims: list[ClaimPublic]
    claim_processing_attempts: list[ClaimProcessingAttemptPublic]
    numeric_verification_attempts: list[DeterministicVerificationAttemptPublic]
    judge_verdict: JudgeVerdictPublic | None
    judge_attempts: list[JudgeAttemptPublic]
    editor_attempts: list[EditorAttemptPublic]
    final_answer: FinalAnswerPublic
    accounting: AccountingSummary
    # T02.2 -- ver docstring de CompletedRunResponse.provider_execution_policy.
    provider_execution_policy: ProviderExecutionPolicy | None
    # Cross-Channel Reconciliation V1 -- ver docstring de
    # SourceJudgeReconciliationResultPublic pros 3 estados distintos.
    reconciliation: SourceJudgeReconciliationResultPublic | None


class QuorumFailureAudit(BaseModel):
    model_config = _CONFIG

    status: Literal["insufficient_quorum"] = "insufficient_quorum"
    id: str
    started_at: datetime
    failed_at: datetime
    config: RunConfigPublic
    successful_count: int
    total_providers: int
    min_to_return: int
    round_result: RoundAudit
    # T02.2 -- ver docstring de CompletedRunResponse.provider_execution_policy.
    provider_execution_policy: ProviderExecutionPolicy | None


# T02.4: um run "running"/"failed" nunca tem detalhe de auditoria pra
# mostrar alem do que o detail (RunResponse) já mostra -- nenhuma claim/
# attempt/verdict foi produzida (sem persistência incremental, fora de
# escopo), ou, se foi, nunca foi persistida (item 6: só o desfecho é
# sanitizado e gravado). Reusar RunningRunResponse/FailedRunResponse
# aqui em vez de criar RunningRunAudit/FailedRunAudit idênticos evita
# inventar detalhe de auditoria que não existe (requisito de teste I:
# "não invente fatos de auditoria detalhados pra um run incompleto").
RunAuditResponse = Annotated[
    Union[CompletedRunAudit, QuorumFailureAudit, RunningRunResponse, FailedRunResponse],
    Field(discriminator="status"),
]


class ErrorBody(BaseModel):
    model_config = _CONFIG

    code: Literal[
        "invalid_provider",
        "invalid_request",
        "insufficient_quorum",
        "run_not_found",
        "internal_error",
    ]
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    model_config = _CONFIG

    error: ErrorBody
