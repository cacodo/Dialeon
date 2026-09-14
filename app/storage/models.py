"""
Schema de persistência -- Etapa 10.

Normaliza objetos com identidade própria e relações reais (ModelResponse,
Claim, ClaimSupport, os 3 tipos de *Attempt, JudgeVerdict, ClaimAssessment,
FinalAnswer) em tabelas próprias. Wrappers puramente derivados (RoundResult,
InitialResponsesResult, CritiqueResult, DebateResult, JudgeResult,
EditorResult) NÃO têm tabela -- seus totais (cumulative_input_tokens,
has_unknown_accounting_components, etc.) são sempre @computed_field no
domínio, recalculados a partir dos registros-fato na hora da leitura via
sum_usage_and_cost (o MESMO helper que o domínio já usa), nunca uma segunda
fonte de verdade que possa divergir.

Três raízes, cada uma com seu próprio espaço de id (T02.4 adicionou a
terceira -- as duas primeiras existem desde a Etapa 10):

- CouncilRunRow -- execução que chegou até CouncilRunResult (Editor
  concluiu). debate_skipped_reason/cumulative_budget_exceeded (Debate/
  Judge/Editor) e insufficient_data_for_consensus/budget_exceeded (rodada
  inicial) são campos ARMAZENADOS no domínio original (não
  @computed_field) -- persistidos como colunas diretas, nunca recalculados
  a partir de lógica atual.
- QuorumFailureRow -- execução que abortou em InsufficientQuorumError
  antes de qualquer processamento de claims (Etapa 10: a exceção agora
  carrega o RoundResult real da rodada inicial). Nunca tem claims/
  attempts/verdict/resposta final -- o pipeline nem chegou lá.
- AcceptedRunRow (T02.4) -- registro mínimo de aceite/lifecycle, existe
  ANTES de qualquer chamada a CouncilRunner. Compartilha temporariamente
  o mesmo id que um CouncilRunRow/QuorumFailureRow vai assumir se a
  execução chegar a um desfecho terminal -- mas é DELETADO na mesma
  transação atômica que grava esse desfecho (ver
  CouncilRepository.save_success/save_quorum_failure), então nunca é uma
  segunda fonte de verdade competindo com as duas raízes acima. Só
  sobrevive pra sempre em "running" (honestamente incompleto) ou
  "failed" (exceção inesperada, sanitizada) -- ver docstring da classe.

ModelResponseRow.council_run_id/quorum_failure_id são mutuamente
exclusivos (exatamente um preenchido) -- um CHECK garante isso no nível
do banco, não só por convenção da aplicação.

JSON só para estruturas pequenas onde normalização não ajuda em nada
(RunConfig snapshot, PricingProvenance, best_arguments_by,
debate_limitations, target_claim_ids, error info) -- nunca pro banco
inteiro. merged_from_claim_ids/ClaimSupport têm tabela própria porque são
relações REAIS entre objetos com identidade própria (lineage auditável),
exatamente o caso onde normalizar agrega valor de verdade.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CouncilRunRow(Base):
    __tablename__ = "council_runs"

    id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(default="completed")
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime]

    run_config_json: Mapped[dict] = mapped_column(JSON)

    claim_processor_provider: Mapped[str]
    debate_skipped_reason: Mapped[str | None]
    debate_cumulative_budget_exceeded: Mapped[bool]

    initial_insufficient_data_for_consensus: Mapped[bool]
    initial_budget_exceeded: Mapped[bool]

    judge_provider: Mapped[str]
    judge_verdict_unavailable_reason: Mapped[str | None]
    judge_cumulative_budget_exceeded: Mapped[bool]
    # Nenhuma FK pra judge_verdicts aqui -- ver JudgeVerdictRow.council_run_id
    # (relação 1:1 no sentido inverso). Duas FKs mútuas entre as mesmas
    # duas tabelas criariam uma dependência circular de inserção real (o
    # SQLite verifica cada FK imediatamente, não no fim da transação) --
    # descoberto rodando os testes de atomicidade desta etapa. A presença
    # de um veredito é consultada por council_run_id, não por um ponteiro
    # de volta redundante.

    editor_provider: Mapped[str]
    editor_fallback_reason: Mapped[str | None]
    editor_cumulative_budget_exceeded: Mapped[bool]
    # Idem -- ver FinalAnswerRow.council_run_id (1:1 no sentido inverso).

    # Etapa 16 -- TODAS nullable, diferente de debate/judge/editor acima:
    # source_analysis_result inteiro pode ser None (nenhuma fonte
    # fornecida), não só "sem resultado por motivo X" -- ver
    # app/source_analysis/result.py.
    source_analyzer_provider: Mapped[str | None]
    source_analysis_skipped_reason: Mapped[str | None]
    source_analysis_cumulative_budget_exceeded: Mapped[bool | None]

    # T02.2 -- snapshot de ProviderExecutionPolicy vigente no momento do
    # aceite durável desta execução, COPIADO verbatim do accepted_runs
    # correspondente na mesma transação que grava este registro (nunca
    # recalculado a partir de Settings atual). NULL só pra runs
    # persistidos ANTES desta coluna existir (ver
    # `_upgrade_legacy_provider_execution_policy`, app/storage/database.py)
    # -- nunca backfillado com o default atual.
    provider_execution_policy_json: Mapped[dict | None] = mapped_column(JSON)


class AcceptedRunRow(Base):
    """Registro de aceite/lifecycle -- T02.4 (durable accepted-run
    envelope). Existe DESDE o instante em que a execução é aceita
    (validação de provider já passou), ANTES de qualquer chamada a
    `CouncilRunner` -- exatamente pra que uma execução validada que já
    pode consumir providers nunca desapareça do histórico se algo além
    de `InsufficientQuorumError` acontecer, ou se o processo morrer
    antes da persistência terminal (ver relatório de reconciliação
    T02.4 -- gap de auditabilidade/reparabilidade, não de corrupção de
    dado).

    Efêmero por design pros dois casos que já têm registro terminal
    canônico: uma execução que chega a `completed`/`insufficient_quorum`
    tem esta linha DELETADA na MESMA transação atômica que grava
    `CouncilRunRow`/`QuorumFailureRow` (ver `CouncilRepository.save_success`/
    `save_quorum_failure`) -- nunca uma segunda fonte de verdade
    duplicando o mesmo fato que aquelas duas tabelas já cobrem
    (principio 9: fonte única por status). Se aquela transação terminal
    falhar, o rollback preserva esta linha intacta -- é exatamente essa
    propriedade (rollback atômico de `session_scope`) que garante que
    uma falha de persistência terminal nunca apaga a evidência de que a
    execução foi aceita.

    Só sobrevive pra sempre em dois casos honestos, nunca resolvidos
    artificialmente por este slice (fora de escopo: crash recovery,
    heartbeat, lease, stale-run cleanup -- ver relatório):
    `status="running"` (processo ainda em andamento, ou morreu antes de
    terminar -- as duas situações são indistinguíveis aqui, de propósito,
    e IS o dado honesto) ou `status="failed"` (exceção inesperada durante
    a execução, já sanitizada -- ver
    `CouncilExecutionService._sanitize_unexpected_failure`)."""

    __tablename__ = "accepted_runs"

    id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]  # "running" | "failed"
    started_at: Mapped[datetime]
    run_config_json: Mapped[dict] = mapped_column(JSON)

    # Só preenchidos quando status="failed" -- todos None em "running".
    failed_at: Mapped[datetime | None]
    # Classificação interna estável (nome da classe da exceção) -- NUNCA
    # str(exc)/traceback/repr (ver docstring de
    # CouncilExecutionService._sanitize_unexpected_failure).
    failure_classification: Mapped[str | None]
    failure_message: Mapped[str | None]

    # T02.2 -- snapshot de ProviderExecutionPolicy vigente no momento do
    # aceite. Sempre preenchido por `save_accepted` (novas linhas nunca
    # nascem com isto None) -- NULL só ocorre em linhas persistidas
    # ANTES desta coluna existir (ver
    # `_upgrade_legacy_provider_execution_policy`, app/storage/database.py).
    # Sobrevive INTACTO à transição "running" -> "failed"
    # (`save_unexpected_failure` nunca toca esta coluna).
    provider_execution_policy_json: Mapped[dict | None] = mapped_column(JSON)


class ModelResponseRow(Base):
    __tablename__ = "model_responses"
    __table_args__ = (
        CheckConstraint(
            "(council_run_id IS NOT NULL) != (quorum_failure_id IS NOT NULL)",
            name="model_response_exactly_one_parent",
        ),
        Index("ix_model_responses_council_run_id", "council_run_id"),
        Index("ix_model_responses_quorum_failure_id", "quorum_failure_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str | None] = mapped_column(ForeignKey("council_runs.id"))
    quorum_failure_id: Mapped[str | None] = mapped_column(ForeignKey("quorum_failures.id"))

    round_number: Mapped[int]
    # Posição na lista original (InitialResponsesResult.responses ou
    # CritiqueResult.round_result.responses) -- SQLite não garante ordem
    # sem ORDER BY explícito, e não existe chave natural que reflita a
    # ordem de despacho/chegada das respostas em paralelo. Escopo: dentro
    # do mesmo (parent_id, round_number).
    position: Mapped[int] = mapped_column(default=0)
    provider: Mapped[str]
    # Etapa 13 (T03.A) — ver docstring de ProviderResponse
    # (app/models/provider_models.py). Coluna nova, não-nula: bancos
    # SQLite criados antes da Etapa 13 não são compatíveis com este
    # schema sem migração manual (create_all() não adiciona colunas a
    # tabelas existentes — sem Alembic neste projeto, ver Etapa 13 no
    # handoff arquitetural).
    requested_model: Mapped[str]
    model: Mapped[str]
    status: Mapped[str]
    response_text: Mapped[str | None]
    # True se um TokenUsage existia (mesmo com campos internos None,
    # como TokenUsage(None, None) -- Gemini sem usage_metadata). False
    # distingue "usage nunca existiu" de "existiu mas está vazio" --
    # sem isso os dois colapsam na mesma representação NULL/NULL.
    usage_present: Mapped[bool]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]
    pricing_provenance_json: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int]
    attempts: Mapped[int]
    error_type: Mapped[str | None]
    error_message: Mapped[str | None]
    error_retryable: Mapped[bool | None]
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py).
    had_uncertain_prior_attempts: Mapped[bool]
    # Etapa 17A.1 (Objetivo B) -- ver docstring de ProviderResponse.
    provider_finish_reason: Mapped[str | None]
    created_at: Mapped[datetime]


class ClaimRow(Base):
    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_council_run_id", "council_run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))
    # Posição em DebateResult.claims -- sem chave natural (round_introduced
    # se repete entre claims do mesmo round; created_at não é garantido
    # distinguível/estável o suficiente pra ser um contrato de ordem).
    position: Mapped[int] = mapped_column(default=0)

    text: Mapped[str]
    source_model_response_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_responses.id")
    )
    round_introduced: Mapped[int]
    parent_claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"))
    superseded_by: Mapped[str | None]
    status: Mapped[str]
    total_models_in_round: Mapped[int]
    # Cross-round claim reconciliation -- ver docstring de
    # Claim.support_scope_model_count (app/models/domain.py). Nullable de
    # propósito: `None` (comportamento histórico, toda claim que nunca
    # precisou de um universo de suporte maior que sua própria rodada)
    # continua reconstruindo exatamente como antes desta coluna existir --
    # nenhuma linha antiga precisa/pode ser retroativamente preenchida
    # (ver `_upgrade_legacy_support_scope_model_count`, app/storage/database.py).
    support_scope_model_count: Mapped[int | None]
    confidence: Mapped[float | None]
    external_evidence_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime]


class ClaimMergeRow(Base):
    __tablename__ = "claim_merges"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    source_claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    # Ordem de Claim.merged_from_claim_ids não é garantida por SQLite sem
    # ORDER BY explícito -- essa coluna preserva a ordem original.
    position: Mapped[int] = mapped_column(default=0)


class ClaimSupportRow(Base):
    __tablename__ = "claim_supports"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    model_response_id: Mapped[str] = mapped_column(
        ForeignKey("model_responses.id"), primary_key=True
    )
    provider: Mapped[str]
    model: Mapped[str]
    # Ordem de Claim.supporting_model_response_ids É semanticamente
    # significativa (Claim.supporting_models preserva "primeira
    # aparição", por design) -- preservada explicitamente aqui.
    position: Mapped[int] = mapped_column(default=0)


class ClaimProcessingAttemptRow(Base):
    __tablename__ = "claim_processing_attempts"
    __table_args__ = (Index("ix_claim_processing_attempts_council_run_id", "council_run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))
    # Posição em DebateResult.claim_processing_attempts -- attempt_number
    # sozinho NÃO é uma chave de ordem global válida aqui (se repete entre
    # diferentes target_model_response_id/target_claim_ids -- múltiplas
    # extrações/agrupamentos concorrentes cada um com seu próprio
    # attempt_number=1,2,...).
    position: Mapped[int] = mapped_column(default=0)

    operation: Mapped[str]
    round_number: Mapped[int]
    attempt_number: Mapped[int]
    provider: Mapped[str]
    # Etapa 13 (T03.A) — ver ModelResponseRow.requested_model acima.
    requested_model: Mapped[str]
    model: Mapped[str]
    target_model_response_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_responses.id")
    )
    target_claim_ids_json: Mapped[list] = mapped_column(JSON)

    transport_status: Mapped[str]
    transport_error_json: Mapped[dict | None] = mapped_column(JSON)
    transport_attempts: Mapped[int]
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py).
    had_uncertain_prior_attempts: Mapped[bool]
    # Etapa 17A.1 (Objetivo B) -- ver docstring de ProviderResponse.
    provider_finish_reason: Mapped[str | None]
    raw_output_text: Mapped[str | None]
    parse_status: Mapped[str]
    parse_error_message: Mapped[str | None]

    # True se um TokenUsage existia (mesmo com campos internos None,
    # como TokenUsage(None, None) -- Gemini sem usage_metadata). False
    # distingue "usage nunca existiu" de "existiu mas está vazio" --
    # sem isso os dois colapsam na mesma representação NULL/NULL.
    usage_present: Mapped[bool]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]
    pricing_provenance_json: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int]
    created_at: Mapped[datetime]


class DeterministicVerificationAttemptRow(Base):
    """Stage 15 -- tabela dedicada, deliberadamente SEM os campos de
    chamada de LLM (`provider`/`model`/`transport_status`/etc. de
    `ClaimProcessingAttemptRow`) -- esta operação nunca faz chamada de
    rede, é código Python síncrono. Cada Claim BRUTA tem no máximo UMA
    proposta numérica, logo `claim_id` continua único -- mas isso NÃO
    substitui `position`: a ordem da LISTA inteira
    (`DebateResult.numeric_verification_attempts`) ainda precisa ser
    explícita na reconstrução, exatamente como toda outra lista
    persistida desde a Etapa 10 (ordem de retorno do SQL nunca é
    contrato de persistência -- achado do patch de revisão
    independente)."""

    __tablename__ = "deterministic_verification_attempts"
    __table_args__ = (
        Index("ix_deterministic_verification_attempts_council_run_id", "council_run_id"),
        UniqueConstraint("claim_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"))
    # Posição em DebateResult.numeric_verification_attempts -- mesmo
    # padrão de ClaimProcessingAttemptRow/ClaimSupportRow.
    position: Mapped[int] = mapped_column(default=0)

    state: Mapped[str]
    # Preenchido SÓ quando state="invalid_proposal" -- o payload cru que
    # a LLM propôs (já JSON-safe, veio de json.loads()), pra auditoria
    # mostrar exatamente o que foi rejeitado. None nos outros 3 estados.
    raw_proposal_json: Mapped[dict | list | str | float | int | bool | None] = mapped_column(
        JSON
    )
    # Preenchidos juntos, só quando state != "invalid_proposal" -- a
    # asserção NORMALIZADA que passou na validação estrita (strings
    # decimais limitadas, nunca float).
    assertion_left: Mapped[str | None]
    assertion_operator: Mapped[str | None]
    assertion_right: Mapped[str | None]
    assertion_asserted_result: Mapped[str | None]
    # Só quando state em (supports, contradicts) -- forma canônica exata
    # de Fraction (ex. "1/3", "30") -- nunca aproximação decimal.
    computed_result: Mapped[str | None]
    created_at: Mapped[datetime]


class SourceAnalysisAttemptRow(Base):
    """Etapa 16 -- espelha JudgeAttemptRow, sem `inconsistent_references`
    em parse_status (ver app/source_analysis/attempt.py)."""

    __tablename__ = "source_analysis_attempts"
    __table_args__ = (
        Index("ix_source_analysis_attempts_council_run_id", "council_run_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))
    position: Mapped[int] = mapped_column(default=0)

    attempt_number: Mapped[int]
    provider: Mapped[str]
    requested_model: Mapped[str]
    model: Mapped[str]
    transport_status: Mapped[str]
    transport_error_json: Mapped[dict | None] = mapped_column(JSON)
    transport_attempts: Mapped[int]
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py).
    had_uncertain_prior_attempts: Mapped[bool]
    # Etapa 17A.1 (Objetivo B) -- ver docstring de ProviderResponse.
    provider_finish_reason: Mapped[str | None]
    raw_output_text: Mapped[str | None]
    parse_status: Mapped[str]
    parse_error_message: Mapped[str | None]
    usage_present: Mapped[bool]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]
    pricing_provenance_json: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int]
    created_at: Mapped[datetime]


class SourceClaimAnalysisResultRow(Base):
    """Etapa 16 -- UMA tabela pro resultado por-claim inteiro (união
    discriminada `kind`), não duas -- ver decisão fechada na
    reconciliação arquitetural. `claim_id` NULLABLE (sem FK forçada
    quando `kind='rejected'` e a claim referenciada nem é conhecida --
    nunca fabricamos uma FK falsa)."""

    __tablename__ = "source_claim_analysis_results"
    __table_args__ = (
        Index("ix_source_claim_analysis_results_council_run_id", "council_run_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))
    source_analysis_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("source_analysis_attempts.id")
    )
    position: Mapped[int] = mapped_column(default=0)

    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"))
    kind: Mapped[str]

    # Só quando kind="relation".
    relation: Mapped[str | None]
    excerpt: Mapped[str | None]
    excerpt_start: Mapped[int | None]
    excerpt_end: Mapped[int | None]

    # Só quando kind="rejected".
    reason: Mapped[str | None]
    raw_entry_json: Mapped[dict | list | str | float | int | bool | None] = mapped_column(JSON)

    created_at: Mapped[datetime]


class JudgeVerdictRow(Base):
    __tablename__ = "judge_verdicts"
    __table_args__ = (UniqueConstraint("council_run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))

    evaluated_through_round: Mapped[int]
    judge_model: Mapped[str]
    best_arguments_by_json: Mapped[dict] = mapped_column(JSON)
    debate_limitations_json: Mapped[list] = mapped_column(JSON)
    confidence: Mapped[float]
    reasoning: Mapped[str]
    triggered_self_review: Mapped[bool]
    self_review_of: Mapped[str | None]
    created_at: Mapped[datetime]


class ClaimAssessmentRow(Base):
    __tablename__ = "claim_assessments"

    judge_verdict_id: Mapped[str] = mapped_column(
        ForeignKey("judge_verdicts.id"), primary_key=True
    )
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    verdict: Mapped[str]
    explanation: Mapped[str]
    # Posição em JudgeVerdict.claim_assessments -- não cosmético: esta
    # coluna persiste a ordem exata em que o Judge avaliou (a única
    # fonte dessa ordem; claim_id não serve como chave de ordem, é só
    # identidade, sem relação com a sequência original de avaliação).
    #
    # Deterministic verdict-bucket final answer (patch de apresentação,
    # ver app/editor/compose.py) -- `FinalAnswer.answer_text` NÃO segue
    # mais esta ordem exatamente/globalmente: o renderizador particiona
    # `claim_assessments` em 2 seções fixas por `verdict`
    # (`_bucket_for_verdict`) e só preserva esta ordem de Judge DENTRO de
    # cada seção -- uma avaliação de BUCKET B listada pelo Judge antes de
    # uma de BUCKET A pode aparecer DEPOIS dela no texto final. Esta
    # coluna continua sendo a ordem real/persistida do Judge -- a
    # apresentação é quem reorganiza por veredito a partir dela, nunca o
    # contrário.
    position: Mapped[int] = mapped_column(default=0)


class JudgeAttemptRow(Base):
    __tablename__ = "judge_attempts"
    __table_args__ = (Index("ix_judge_attempts_council_run_id", "council_run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))

    attempt_number: Mapped[int]
    provider: Mapped[str]
    # Etapa 13 (T03.A) — ver ModelResponseRow.requested_model acima.
    requested_model: Mapped[str]
    model: Mapped[str]
    transport_status: Mapped[str]
    transport_error_json: Mapped[dict | None] = mapped_column(JSON)
    transport_attempts: Mapped[int]
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py).
    had_uncertain_prior_attempts: Mapped[bool]
    # Etapa 17A.1 (Objetivo B) -- ver docstring de ProviderResponse.
    provider_finish_reason: Mapped[str | None]
    raw_output_text: Mapped[str | None]
    parse_status: Mapped[str]
    parse_error_message: Mapped[str | None]
    # True se um TokenUsage existia (mesmo com campos internos None,
    # como TokenUsage(None, None) -- Gemini sem usage_metadata). False
    # distingue "usage nunca existiu" de "existiu mas está vazio" --
    # sem isso os dois colapsam na mesma representação NULL/NULL.
    usage_present: Mapped[bool]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]
    pricing_provenance_json: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int]
    created_at: Mapped[datetime]


class EditorAttemptRow(Base):
    __tablename__ = "editor_attempts"
    __table_args__ = (Index("ix_editor_attempts_council_run_id", "council_run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))

    attempt_number: Mapped[int]
    provider: Mapped[str]
    # Etapa 13 (T03.A) — ver ModelResponseRow.requested_model acima.
    requested_model: Mapped[str]
    model: Mapped[str]
    transport_status: Mapped[str]
    transport_error_json: Mapped[dict | None] = mapped_column(JSON)
    transport_attempts: Mapped[int]
    # Etapa 17A (B3) -- ver docstring de ProviderResponse
    # (app/models/provider_models.py).
    had_uncertain_prior_attempts: Mapped[bool]
    # Etapa 17A.1 (Objetivo B) -- ver docstring de ProviderResponse.
    provider_finish_reason: Mapped[str | None]
    raw_output_text: Mapped[str | None]
    parse_status: Mapped[str]
    parse_error_message: Mapped[str | None]
    # True se um TokenUsage existia (mesmo com campos internos None,
    # como TokenUsage(None, None) -- Gemini sem usage_metadata). False
    # distingue "usage nunca existiu" de "existiu mas está vazio" --
    # sem isso os dois colapsam na mesma representação NULL/NULL.
    usage_present: Mapped[bool]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]
    pricing_provenance_json: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int]
    created_at: Mapped[datetime]


class FinalAnswerRow(Base):
    __tablename__ = "final_answers"
    __table_args__ = (UniqueConstraint("council_run_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    council_run_id: Mapped[str] = mapped_column(ForeignKey("council_runs.id"))

    answer_text: Mapped[str]
    limitations_json: Mapped[list] = mapped_column(JSON)
    status: Mapped[str]
    editor_model: Mapped[str | None]
    based_on_verdict_id: Mapped[str | None] = mapped_column(ForeignKey("judge_verdicts.id"))
    judge_confidence: Mapped[float | None]
    created_at: Mapped[datetime]


class QuorumFailureRow(Base):
    __tablename__ = "quorum_failures"

    id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(default="insufficient_quorum")
    started_at: Mapped[datetime]
    failed_at: Mapped[datetime]

    run_config_json: Mapped[dict] = mapped_column(JSON)

    successful_count: Mapped[int]
    total_providers: Mapped[int]
    min_to_return: Mapped[int]
    round_number: Mapped[int]

    # T02.2 -- mesma disciplina de CouncilRunRow.provider_execution_policy_json:
    # copiado verbatim do accepted_runs correspondente na mesma
    # transação (ver CouncilRepository.save_quorum_failure), NULL só
    # pra linhas legadas pré-upgrade.
    provider_execution_policy_json: Mapped[dict | None] = mapped_column(JSON)
