"""
`CouncilRepository` -- Etapa 10.

Única classe que toca SQLAlchemy diretamente para persistência de execuções
do Council. Não conhece `CouncilRunner`/`Orchestrator` -- recebe objetos de
domínio já prontos (`CouncilRunResult`, ou os dados de uma
`InsufficientQuorumError` já capturada) e só persiste/recupera.

Cada `save_*` é UMA transação atômica (Decision Delta §6): se qualquer parte
falhar, nada daquele save fica gravado -- `session_scope` cuida do
commit/rollback. Sem persistência incremental: uma execução só é gravada de
uma vez, inteira, nunca em pedaços conforme cada attempt/claim é produzido.

Reconstrução na leitura usa `sum_usage_and_cost` -- o MESMO helper que o
domínio já usa para os `@computed_field` -- nunca uma fórmula duplicada que
possa divergir com o tempo.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.council.result import CouncilRunResult
from app.debate.claims import get_current_claims
from app.debate.result import CritiqueResult, DebateResult
from app.editor.result import EditorResult
from app.judge.result import JudgeResult
from app.source_analysis.result import SourceAnalysisResult
from app.models.domain import ClaimAssessment, ClaimSupport
from app.models.provider_models import DefaultModelAuthoritySnapshot, ProviderExecutionPolicy
from app.orchestrator.budget import sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.editor.natural_answer_coherence import validate_natural_answer_coherence
from app.editor.linguistic_realization_coherence import (
    validate_linguistic_realization_coherence,
)
from app.editor.primary_answer_coherence import validate_primary_answer_coherence
from app.reconciliation.errors import ReconciliationError
from app.reconciliation.reconcile import validate_reconciliation_coherence
from app.storage.database import session_scope
from app.storage.models import (
    AcceptedRunRow,
    ClaimAssessmentRow,
    ClaimMergeRow,
    ClaimProcessingAttemptRow,
    ClaimReconciliationOutcomeRow,
    ClaimReconciliationSourceResultRow,
    ClaimRow,
    ClaimSupportRow,
    CouncilRunRow,
    DeterministicVerificationAttemptRow,
    EditorAttemptRow,
    FinalAnswerRow,
    JudgeAttemptRow,
    JudgeVerdictRow,
    ModelResponseRow,
    QuorumFailureRow,
    SourceAnalysisAttemptRow,
    SourceClaimAnalysisResultRow,
    SourceJudgeReconciliationRow,
)
from app.storage.records import (
    AcceptedRunRecord,
    CompletedRunRecord,
    QuorumFailureRecord,
    RunSummary,
)
from app.storage.serializers import (
    dt_from_naive_utc,
    dt_to_naive_utc,
    claim_assessment_rows,
    claim_from_row,
    claim_merge_rows,
    claim_processing_attempt_from_row,
    claim_processing_attempt_to_row,
    claim_support_from_row,
    claim_support_rows,
    claim_to_row,
    deterministic_verification_attempt_from_row,
    deterministic_verification_attempt_to_row,
    editor_attempt_from_row,
    editor_attempt_to_row,
    final_answer_from_row,
    final_answer_to_row,
    judge_attempt_from_row,
    judge_attempt_to_row,
    judge_verdict_from_row,
    judge_verdict_to_row,
    model_response_from_row,
    model_response_to_row,
    source_analysis_attempt_from_row,
    source_analysis_attempt_to_row,
    source_claim_analysis_result_from_row,
    source_claim_analysis_result_to_row,
    source_judge_reconciliation_from_rows,
    source_judge_reconciliation_rows,
)


def _new_id() -> str:
    return str(uuid4())


def _policy_from_json(data: dict | None) -> ProviderExecutionPolicy | None:
    """T02.2 -- `None` é o valor HONESTO pra runs persistidos antes
    desta coluna existir (ver `_upgrade_legacy_provider_execution_policy`,
    app/storage/database.py) -- NUNCA substituído por um default atual."""
    return ProviderExecutionPolicy(**data) if data is not None else None


def _default_model_authority_snapshot_from_json(
    data: dict | None,
) -> DefaultModelAuthoritySnapshot | None:
    """Provider Default-Model Snapshot Provenance V1 -- mesma disciplina
    de `_policy_from_json`: `None` é o valor HONESTO pra runs
    persistidos antes desta coluna existir (ver
    `_upgrade_legacy_default_model_authority_snapshot`,
    app/storage/database.py) -- NUNCA substituído por um snapshot atual
    reconstruído do registry de provider vigente agora."""
    return DefaultModelAuthoritySnapshot(**data) if data is not None else None


def _run_config_from_json(data: dict) -> RunConfig:
    """Reconstrói `RunConfig` a partir do JSON persistido -- nunca muta o
    dict recebido (sempre trabalha sobre uma cópia), porque este blob
    pode ser reusado/inspecionado pelo chamador depois.

    Backfill 1 (Etapa 17A.2) -- adicionou `max_output_tokens_grouping`/
    `max_output_tokens_judge` (campos obrigatórios, sem default) a
    `RunConfig`, então um run persistido ANTES desta etapa não tem essas
    chaves no blob salvo. Backfill honesto, não um default arbitrário
    inventado agora: antes da Etapa 17A.2, agrupamento e Judge de fato
    usavam `max_output_tokens_per_call` (o único teto que existia) --
    então reconstruir um run antigo com `max_output_tokens_grouping`/
    `max_output_tokens_judge` iguais ao `max_output_tokens_per_call`
    DAQUELE MESMO run reflete exatamente o que aconteceu de verdade
    naquela execução, nunca o novo default global (8192) de runs
    futuros, que não tem relação com o que essa execução histórica
    realmente usou. Um blob que já tem as chaves (run nativo da Etapa
    17A.2 em diante) nunca é alterado por este backfill.

    Backfill 2 (clarificação de contrato de execução, pós-run real) --
    `RunConfig.overall_timeout_seconds` foi renomeado pra
    `round_dispatch_timeout_seconds` (o nome antigo dava a entender um
    prazo pra execução INTEIRA do Council; na verdade sempre foi só o
    dispatch paralelo de UMA rodada, reiniciado a cada rodada -- ver
    app/orchestrator/config.py). Um blob persistido ANTES dessa
    renomeação só tem a chave antiga -- RENOMEIA a chave (nunca
    reinterpreta/recalcula o VALOR, que continua o mesmo número
    exatamente como foi registrado naquela execução).

    Correção independente de revisão -- a chave legada precisa ser
    removida INCONDICIONALMENTE, nunca só quando a canônica está
    ausente: `RunConfig` usa `extra="forbid"`, então um blob com AS DUAS
    chaves (produzido, por exemplo, por uma leitura+escrita
    intermediária durante a janela de transição) fazia
    `overall_timeout_seconds` sobreviver na cópia e a reconstrução
    falhava com `extra_forbidden`, mesmo quando a canônica já estava
    presente e correta. A canônica é sempre AUTORITATIVA quando as duas
    existem (nunca um erro de conflito, nunca uma tentativa de
    reconciliar valores diferentes) -- a legada é descartada nesse
    caso, nunca lida. Um blob que só tem a chave nova (run nativo desta
    renomeação em diante, sem a legada) nunca é alterado por este
    backfill."""
    data = dict(data)
    if not ("max_output_tokens_grouping" in data and "max_output_tokens_judge" in data):
        data.setdefault("max_output_tokens_grouping", data["max_output_tokens_per_call"])
        data.setdefault("max_output_tokens_judge", data["max_output_tokens_per_call"])
    legacy_round_dispatch_timeout = data.pop("overall_timeout_seconds", None)
    if "round_dispatch_timeout_seconds" not in data and legacy_round_dispatch_timeout is not None:
        data["round_dispatch_timeout_seconds"] = legacy_round_dispatch_timeout
    return RunConfig(**data)


# `EditorAttemptRow.purpose` das tentativas de planejamento da resposta principal.
_PRIMARY_ANSWER_PURPOSE = "primary_answer_plan"
_LINGUISTIC_REALIZATION_PURPOSE = "linguistic_realization"
_LINGUISTIC_SEMANTIC_REVIEW_PURPOSE = "linguistic_semantic_review"

_REALIZATION_PERSISTENCE_PREFLIGHT_FAILED = "realization_persistence_preflight_failed"


def _drop_linguistic_realization(result: CouncilRunResult) -> CouncilRunResult:
    """Closure repair (adversarial review, audit-truth pass) -- degrada SÓ
    a camada opcional de LinguisticRealization: um Run de resto
    bem-sucedido (debate/judge/primary_answer/natural_answer/answer_text)
    nunca é tocado, e nenhum attempt/usage/custo/provider já verdadeiro é
    apagado -- só a LinguisticRealization ACEITA em si deixa de ser
    persistida/preferida. Não há mais um segundo caminho que também
    apaga tentativas: coerência falhando pra um resultado ACEITO nunca
    prova que as chamadas nunca aconteceram (ver docstring de
    `app/editor/linguistic_realization_coherence.py`), então este é o
    único destino possível -- se mesmo este estado reduzido não for
    coerente, isso é agora estruturalmente impossível (branch B daquele
    módulo nunca reconstrói/exige nada que dependa da PrimaryAnswer
    atual), não algo que precise de um fallback mais extremo."""
    editor = result.editor_result
    final_answer = editor.final_answer.model_copy(update={"linguistic_realization": None})
    editor = editor.model_copy(
        update={
            "final_answer": final_answer,
            "linguistic_realization_fallback_reason": _REALIZATION_PERSISTENCE_PREFLIGHT_FAILED,
        }
    )
    return result.model_copy(update={"editor_result": editor})


def _check_linguistic_realization_persistable(result: CouncilRunResult) -> None:
    """As MESMAS operações determinísticas específicas da LinguisticRealization
    que `save_success` precisaria fazer pra persistir/preferir essa camada
    -- coerência entre registros + forma de serialização JSON -- rodadas
    aqui isoladamente, e SÓ isoladamente: nunca toca a sessão/banco. Uma
    exceção capturada por quem chama isto é portanto GARANTIDAMENTE
    Python/domínio, nunca uma falha geral de banco/transação/
    infraestrutura (essas continuam propagando normalmente de dentro da
    transação real, inteiramente alheias a esta função)."""
    validate_linguistic_realization_coherence(
        result.editor_result.final_answer, result.editor_result, question=result.run_config.question
    )
    realization = result.editor_result.final_answer.linguistic_realization
    if realization is not None:
        # Força a mesma serialização que `final_answer_to_row`
        # (app/storage/serializers.py) fará -- nunca toca o banco, só
        # prova que a forma é serializável.
        realization.model_dump(mode="json")


def _preflight_linguistic_realization(result: CouncilRunResult) -> CouncilRunResult:
    """Closure repair (adversarial review, audit-truth pass) -- roda a
    checagem acima ANTES de abrir a transação de `save_success`. Se ela
    falhar, essa camada OPCIONAL é degradada (nunca o Run inteiro, nunca
    os attempts -- ver `_drop_linguistic_realization`).

    Uma falha GERAL de banco/transação/infraestrutura nunca passa por
    aqui -- só acontece depois, dentro da transação real, e continua
    propagando/abortando o `save_success` inteiro como sempre (nunca
    disfarçada de falha opcional de apresentação)."""
    try:
        _check_linguistic_realization_persistable(result)
        return result
    except Exception:
        return _drop_linguistic_realization(result)


class CouncilRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # -----------------------------------------------------------------
    # SAVE
    # -----------------------------------------------------------------

    async def save_accepted(
        self,
        run_id: str,
        *,
        run_config: RunConfig,
        started_at: datetime,
        provider_execution_policy: ProviderExecutionPolicy,
        default_model_authority_snapshot: DefaultModelAuthoritySnapshot | None = None,
    ) -> None:
        """T02.4 -- grava o registro mínimo de aceite ANTES de qualquer
        chamada ao `CouncilRunner` (contrato de `CouncilExecutionService`).
        `run_id`/`started_at` são autoritativos desde aqui -- nenhum
        estágio posterior (runner, terminal save) minta substituto.

        `provider_execution_policy` (T02.2): obrigatório, nunca `None` --
        um NOVO accepted run SEMPRE tem um snapshot concreto (é assim
        que este campo fica `None` só pra linhas legadas, nunca pra
        escritas novas). Persistido verbatim, nunca recalculado.

        `default_model_authority_snapshot` (Provider Default-Model
        Snapshot Provenance V1): OPCIONAL neste nível de repositório
        (default `None`) -- diferente de `provider_execution_policy`,
        deliberadamente, pra não forçar todo chamador direto de
        `save_accepted` em teste (que não exercita esta provenance
        específica) a passar um valor. O único chamador de PRODUÇÃO
        (`CouncilExecutionService.run()`) SEMPRE constrói e passa um
        valor concreto pra toda execução nova -- `None` aqui só ocorre
        em chamadores de teste que não passam pelo service, nunca no
        caminho de produção real."""
        async with session_scope(self._session_factory) as session:
            session.add(
                AcceptedRunRow(
                    id=run_id,
                    status="running",
                    started_at=dt_to_naive_utc(started_at),
                    run_config_json=run_config.model_dump(mode="json"),
                    failed_at=None,
                    failure_classification=None,
                    failure_message=None,
                    provider_execution_policy_json=provider_execution_policy.model_dump(
                        mode="json"
                    ),
                    default_model_authority_snapshot_json=(
                        default_model_authority_snapshot.model_dump(mode="json")
                        if default_model_authority_snapshot is not None
                        else None
                    ),
                )
            )

    async def save_unexpected_failure(
        self,
        run_id: str,
        *,
        failed_at: datetime,
        failure_classification: str,
        failure_message: str,
    ) -> None:
        """T02.4 -- transição terminal FAILED da MESMA linha de aceite
        (nunca cria um registro novo/paralelo). `failure_classification`/
        `failure_message` já chegam sanitizados (ver
        `CouncilExecutionService._sanitize_unexpected_failure`) -- esta
        camada nunca examina/reformata o que recebe, só persiste."""
        async with session_scope(self._session_factory) as session:
            row = await session.get(AcceptedRunRow, run_id)
            assert row is not None, (
                f"save_unexpected_failure chamado sem um accepted_runs prévio: {run_id!r}"
            )
            row.status = "failed"
            row.failed_at = dt_to_naive_utc(failed_at)
            row.failure_classification = failure_classification
            row.failure_message = failure_message

    async def save_success(self, result: CouncilRunResult) -> None:
        """Persiste um `CouncilRunResult` completo numa única transação.

        Ordem de `session.add()` é topológica de propósito (Claim referencia
        ModelResponse, ClaimAssessment referencia Claim+JudgeVerdict,
        FinalAnswer referencia JudgeVerdict) -- SQLite verifica cada
        FOREIGN KEY imediatamente, não espera o fim da transação, então
        uma linha que referencia outra ainda não inserida falha na hora,
        mesmo dentro do mesmo commit.

        T02.2: `provider_execution_policy_json` é COPIADO verbatim do
        `accepted_runs` correspondente (se existir -- chamadores diretos
        em teste, sem passar por `save_accepted` antes, legitimamente
        não têm um, e o campo fica `None`), lido AQUI, na MESMA
        transação, ANTES do DELETE que finaliza a linha de aceite -- a
        linha de aceite é a fonte de provenance, nunca `Settings`
        atual/um valor recalculado.

        Cross-Channel Reconciliation V1, Repair #4 (revisão adversarial)
        -- `result.reconciliation=None` é rejeitado ANTES de qualquer
        `session.add()` (nenhuma transação chega a abrir): é reservado
        EXCLUSIVAMENTE pra reconstrução de execuções históricas
        persistidas antes deste slice existir (ver docstring de
        `SourceJudgeReconciliationResult`) -- uma escrita NOVA sem
        reconciliação concreta ficaria indistinguível de histórico
        genuíno na leitura. Um teste que precisa modelar estado
        histórico deve criar as linhas diretamente no nível de
        storage/schema (ver `tests/storage/fixtures.py`), nunca através
        deste caminho. `validate_reconciliation_coherence` (mesmo
        validador chamado em `app/council/runner.py`, logo após a
        construção) roda ANTES de qualquer escrita, pela mesma razão --
        um `CouncilRunResult` montado manualmente/incoerente nunca vira
        estado canônico persistido."""
        debate = result.debate_result
        source_analysis = result.source_analysis_result
        judge = result.judge_result
        editor = result.editor_result

        if result.reconciliation is None:
            raise ReconciliationError(
                "save_success recusa persistir uma execução NOVA com "
                "reconciliation=None -- reconciliação concreta é obrigatória "
                "pra toda escrita nova (None é reservado exclusivamente pra "
                "reconstrução de execuções históricas persistidas antes deste "
                "slice existir)"
            )
        validate_reconciliation_coherence(
            result.reconciliation, get_current_claims(debate.claims), judge, source_analysis
        )
        # Primary Answer -- MESMA regra única aplicada na reconstrução (validator
        # de `CouncilRunResult`); aqui cobre objetos montados sem validação
        # (ex. `model_copy`) antes de qualquer escrita.
        validate_primary_answer_coherence(debate, judge, editor)
        # Natural Answer -- MESMA regra única aplicada na reconstrução
        # (validator de `CouncilRunResult`); aqui cobre objetos montados
        # sem validação (ex. `model_copy`) antes de qualquer escrita.
        validate_natural_answer_coherence(editor.final_answer)
        # Closure repair (adversarial review) -- LinguisticRealization é a
        # ÚNICA camada aqui que é OPCIONAL/presentacional por design (ver
        # docstring de `_preflight_linguistic_realization`): diferente das
        # checagens acima (que continuam abortando o save inteiro se
        # falharem -- indicam bug/adulteração no núcleo determinístico),
        # uma falha ESPECÍFICA desta camada só derruba ela mesma. Isto
        # roda ANTES da transação (nunca toca o banco) -- pode substituir
        # `result`/`editor` por uma versão degradada, nunca abre/aborta a
        # sessão sozinha.
        result = _preflight_linguistic_realization(result)
        editor = result.editor_result

        async with session_scope(self._session_factory) as session:
            accepted_row = await session.get(AcceptedRunRow, result.id)
            provider_execution_policy_json = (
                accepted_row.provider_execution_policy_json if accepted_row is not None else None
            )
            default_model_authority_snapshot_json = (
                accepted_row.default_model_authority_snapshot_json
                if accepted_row is not None
                else None
            )
            session.add(
                CouncilRunRow(
                    id=result.id,
                    status="completed",
                    started_at=dt_to_naive_utc(result.started_at),
                    completed_at=dt_to_naive_utc(result.completed_at),
                    run_config_json=result.run_config.model_dump(mode="json"),
                    provider_execution_policy_json=provider_execution_policy_json,
                    default_model_authority_snapshot_json=default_model_authority_snapshot_json,
                    claim_processor_provider=debate.claim_processor_provider,
                    debate_skipped_reason=debate.debate_skipped_reason,
                    debate_cumulative_budget_exceeded=debate.cumulative_budget_exceeded,
                    initial_insufficient_data_for_consensus=(
                        debate.initial_result.insufficient_data_for_consensus
                    ),
                    initial_budget_exceeded=debate.initial_result.budget_exceeded,
                    judge_provider=judge.judge_provider,
                    judge_verdict_unavailable_reason=judge.verdict_unavailable_reason,
                    judge_cumulative_budget_exceeded=judge.cumulative_budget_exceeded,
                    editor_provider=editor.editor_provider,
                    editor_fallback_reason=editor.fallback_reason,
                    editor_cumulative_budget_exceeded=editor.cumulative_budget_exceeded,
                    editor_primary_answer_fallback_reason=editor.primary_answer_fallback_reason,
                    editor_natural_answer_fallback_reason=editor.natural_answer_fallback_reason,
                    editor_linguistic_realization_fallback_reason=(
                        editor.linguistic_realization_fallback_reason
                    ),
                    editor_linguistic_semantic_review_provider=(
                        editor.linguistic_semantic_review_provider
                    ),
                    source_analyzer_provider=(
                        source_analysis.source_analyzer_provider
                        if source_analysis is not None
                        else None
                    ),
                    source_analysis_skipped_reason=(
                        source_analysis.skipped_reason if source_analysis is not None else None
                    ),
                    source_analysis_cumulative_budget_exceeded=(
                        source_analysis.cumulative_budget_exceeded
                        if source_analysis is not None
                        else None
                    ),
                )
            )
            await session.flush()

            for position, response in enumerate(debate.initial_result.responses):
                session.add(
                    model_response_to_row(response, council_run_id=result.id, position=position)
                )
            if debate.critique_round is not None:
                for position, response in enumerate(debate.critique_round.round_result.responses):
                    session.add(
                        model_response_to_row(
                            response, council_run_id=result.id, position=position
                        )
                    )
            await session.flush()

            for position, claim in enumerate(debate.claims):
                session.add(claim_to_row(claim, council_run_id=result.id, position=position))
            await session.flush()

            for claim in debate.claims:
                session.add_all(claim_merge_rows(claim))
                session.add_all(claim_support_rows(claim))

            for position, attempt in enumerate(debate.claim_processing_attempts):
                session.add(
                    claim_processing_attempt_to_row(
                        attempt, council_run_id=result.id, position=position
                    )
                )

            for position, attempt in enumerate(debate.numeric_verification_attempts):
                session.add(
                    deterministic_verification_attempt_to_row(
                        attempt, council_run_id=result.id, position=position
                    )
                )

            if source_analysis is not None:
                for attempt_position, attempt in enumerate(source_analysis.attempts):
                    session.add(
                        source_analysis_attempt_to_row(
                            attempt, council_run_id=result.id, position=attempt_position
                        )
                    )
                await session.flush()
                # claim_results só existem quando houve um attempt aceito
                # -- amarrados a ele por id (útil pra distinguir tentativas
                # em runs com retry, mesmo que v1 só produza resultados a
                # partir da última tentativa aceita).
                accepted_attempt = next(
                    (a for a in source_analysis.attempts if a.parse_status == "accepted"), None
                )
                if accepted_attempt is not None:
                    for result_position, claim_result in enumerate(source_analysis.claim_results):
                        session.add(
                            source_claim_analysis_result_to_row(
                                claim_result,
                                council_run_id=result.id,
                                source_analysis_attempt_id=accepted_attempt.id,
                                position=result_position,
                            )
                        )

            if judge.verdict is not None:
                session.add(judge_verdict_to_row(judge.verdict, council_run_id=result.id))
                await session.flush()
                session.add_all(claim_assessment_rows(judge.verdict))

            for attempt in judge.attempts:
                session.add(judge_attempt_to_row(attempt, council_run_id=result.id))

            session.add(final_answer_to_row(editor.final_answer, council_run_id=result.id))

            for attempt in editor.attempts:
                session.add(editor_attempt_to_row(attempt, council_run_id=result.id))
            for attempt in editor.primary_answer_attempts:
                session.add(
                    editor_attempt_to_row(
                        attempt, council_run_id=result.id, purpose=_PRIMARY_ANSWER_PURPOSE
                    )
                )
            for attempt in editor.linguistic_realization_attempts:
                session.add(
                    editor_attempt_to_row(
                        attempt,
                        council_run_id=result.id,
                        purpose=_LINGUISTIC_REALIZATION_PURPOSE,
                    )
                )
            for attempt in editor.linguistic_semantic_review_attempts:
                session.add(
                    editor_attempt_to_row(
                        attempt,
                        council_run_id=result.id,
                        purpose=_LINGUISTIC_SEMANTIC_REVIEW_PURPOSE,
                    )
                )

            # Cross-Channel Reconciliation V1 -- `result.reconciliation` é
            # garantidamente concreto aqui (checado/validado no topo
            # deste método, ANTES da transação abrir -- ver Repair #4).
            # Ordem topológica: raiz -> outcomes -> links de
            # source-result, mesma FK-imediata do resto do método.
            reconciliation_row, outcome_rows, source_result_rows = (
                source_judge_reconciliation_rows(
                    result.reconciliation, council_run_id=result.id
                )
            )
            session.add(reconciliation_row)
            await session.flush()
            session.add_all(outcome_rows)
            await session.flush()
            session.add_all(source_result_rows)

            # T02.4 -- finaliza a MESMA transação atômica que grava o
            # terminal "completed": a linha de aceite (se existir --
            # chamadores diretos de save_success em teste, sem passar
            # por save_accepted antes, legitimamente não têm uma) deixa
            # de ser necessária, porque CouncilRunRow agora É a fonte
            # canônica única (principio 9). Se o commit desta transação
            # falhar, o DELETE rola de volta junto -- a linha de aceite
            # permanece intacta, exatamente a garantia que o item 7 do
            # contrato exige.
            await session.execute(delete(AcceptedRunRow).where(AcceptedRunRow.id == result.id))

    async def save_quorum_failure(
        self,
        exc: InsufficientQuorumError,
        *,
        run_config: RunConfig,
        started_at: datetime,
        failed_at: datetime,
        run_id: str | None = None,
    ) -> str:
        """Persiste uma `InsufficientQuorumError` capturada, junto com o
        `RoundResult` real que ela carrega (Decision Delta §3).

        T02.4: `run_id` é opcional -- quando fornecido (fluxo real via
        `CouncilExecutionService`, que já mintou/persistiu um aceite
        antes de chamar o runner), esta falha finaliza a MESMA identidade
        aceita, nunca uma paralela (principio 3: autoridade única de
        run-id). Quando omitido (chamadores diretos em teste, exercitando
        só a mecânica de persistência de quórum, sem lifecycle de
        aceite), um id novo é mintado aqui, como sempre foi.

        T02.2: `provider_execution_policy_json` é COPIADO verbatim do
        `accepted_runs` correspondente (só existe quando `run_id` foi
        fornecido -- chamadores diretos em teste sem lifecycle de
        aceite não têm nenhum, e o campo fica `None`), lido ANTES do
        DELETE que finaliza a linha de aceite, na MESMA transação."""
        failure_id = run_id if run_id is not None else _new_id()
        round_result = exc.round_result

        async with session_scope(self._session_factory) as session:
            accepted_row = (
                await session.get(AcceptedRunRow, failure_id) if run_id is not None else None
            )
            provider_execution_policy_json = (
                accepted_row.provider_execution_policy_json if accepted_row is not None else None
            )
            default_model_authority_snapshot_json = (
                accepted_row.default_model_authority_snapshot_json
                if accepted_row is not None
                else None
            )
            session.add(
                QuorumFailureRow(
                    id=failure_id,
                    status="insufficient_quorum",
                    started_at=dt_to_naive_utc(started_at),
                    failed_at=dt_to_naive_utc(failed_at),
                    run_config_json=run_config.model_dump(mode="json"),
                    successful_count=exc.successful_count,
                    total_providers=exc.total_providers,
                    min_to_return=exc.min_to_return,
                    round_number=round_result.round_number,
                    provider_execution_policy_json=provider_execution_policy_json,
                    default_model_authority_snapshot_json=default_model_authority_snapshot_json,
                )
            )
            await session.flush()
            for position, response in enumerate(round_result.responses):
                session.add(
                    model_response_to_row(
                        response, quorum_failure_id=failure_id, position=position
                    )
                )

            # T02.4 -- mesma disciplina de save_success: finaliza (nunca
            # duplica) a linha de aceite, na MESMA transação atômica.
            await session.execute(delete(AcceptedRunRow).where(AcceptedRunRow.id == failure_id))

        return failure_id

    # -----------------------------------------------------------------
    # LOAD
    # -----------------------------------------------------------------

    async def get_run(
        self, run_id: str
    ) -> CompletedRunRecord | QuorumFailureRecord | AcceptedRunRecord | None:
        async with self._session_factory() as session:
            run_row = await session.get(CouncilRunRow, run_id)
            if run_row is not None:
                return await self._reconstruct_completed(session, run_row)

            failure_row = await session.get(QuorumFailureRow, run_id)
            if failure_row is not None:
                return await self._reconstruct_quorum_failure(session, failure_row)

            accepted_row = await session.get(AcceptedRunRow, run_id)
            if accepted_row is not None:
                return _reconstruct_accepted(accepted_row)

        return None

    async def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunSummary]:
        """`completed`/`quorum_failures` são duas tabelas independentes
        sem UNION SQL real entre elas — pra que `offset` funcione
        corretamente sobre o resultado COMBINADO e ordenado (não sobre
        cada tabela isoladamente), buscamos `offset + limit` de cada uma
        (o pior caso onde os itens mais recentes vêm todos de uma tabela
        só), fundimos, ordenamos por `started_at DESC` e só então
        cortamos a página exata em Python. Simples e correto para o
        volume do MVP — não é uma solução que escale pra offsets muito
        grandes, e não precisa ser (sem cursor pagination, por decisão
        explícita).

        T02.4: `accepted_runs` (running/failed) entra na mesma fusão,
        pelo mesmo motivo -- item 9 do contrato exige que list/detail
        sejam derivados exclusivamente dos fatos persistidos canônicos,
        nunca só das duas tabelas terminais históricas."""
        fetch_count = offset + limit
        async with self._session_factory() as session:
            completed = (
                (
                    await session.execute(
                        select(CouncilRunRow)
                        .order_by(CouncilRunRow.started_at.desc())
                        .limit(fetch_count)
                    )
                )
                .scalars()
                .all()
            )
            failures = (
                (
                    await session.execute(
                        select(QuorumFailureRow)
                        .order_by(QuorumFailureRow.started_at.desc())
                        .limit(fetch_count)
                    )
                )
                .scalars()
                .all()
            )
            accepted = (
                (
                    await session.execute(
                        select(AcceptedRunRow)
                        .order_by(AcceptedRunRow.started_at.desc())
                        .limit(fetch_count)
                    )
                )
                .scalars()
                .all()
            )

        summaries = (
            [
                RunSummary(
                    id=row.id,
                    status="completed",
                    started_at=dt_from_naive_utc(row.started_at),
                    ended_at=dt_from_naive_utc(row.completed_at),
                    question=row.run_config_json["question"],
                )
                for row in completed
            ]
            + [
                RunSummary(
                    id=row.id,
                    status="insufficient_quorum",
                    started_at=dt_from_naive_utc(row.started_at),
                    ended_at=dt_from_naive_utc(row.failed_at),
                    question=row.run_config_json["question"],
                )
                for row in failures
            ]
            + [
                RunSummary(
                    id=row.id,
                    status=row.status,  # type: ignore[arg-type]  # "running" | "failed"
                    started_at=dt_from_naive_utc(row.started_at),
                    ended_at=(
                        dt_from_naive_utc(row.failed_at) if row.failed_at is not None else None
                    ),
                    question=row.run_config_json["question"],
                )
                for row in accepted
            ]
        )
        summaries.sort(key=lambda s: s.started_at, reverse=True)
        return summaries[offset : offset + limit]

    # -----------------------------------------------------------------
    # Reconstrução — completed
    # -----------------------------------------------------------------

    async def _reconstruct_completed(
        self, session: AsyncSession, row: CouncilRunRow
    ) -> CompletedRunRecord:
        model_response_rows = (
            (
                await session.execute(
                    select(ModelResponseRow)
                    .where(ModelResponseRow.council_run_id == row.id)
                    .order_by(ModelResponseRow.round_number, ModelResponseRow.position)
                )
            )
            .scalars()
            .all()
        )
        round1 = [model_response_from_row(r) for r in model_response_rows if r.round_number == 1]
        round2 = [model_response_from_row(r) for r in model_response_rows if r.round_number == 2]

        initial_result = _build_initial_responses_result(
            round1,
            insufficient_data_for_consensus=row.initial_insufficient_data_for_consensus,
            budget_exceeded=row.initial_budget_exceeded,
        )
        critique_round = (
            CritiqueResult(round_result=_build_round_result(round2, round_number=2))
            if round2
            else None
        )

        claim_rows = (
            (
                await session.execute(
                    select(ClaimRow)
                    .where(ClaimRow.council_run_id == row.id)
                    .order_by(ClaimRow.position)
                )
            )
            .scalars()
            .all()
        )
        claim_merge_rows_by_claim: dict[str, list[str]] = defaultdict(list)
        if claim_rows:
            merge_rows = (
                (
                    await session.execute(
                        select(ClaimMergeRow)
                        .where(ClaimMergeRow.claim_id.in_([c.id for c in claim_rows]))
                        .order_by(ClaimMergeRow.claim_id, ClaimMergeRow.position)
                    )
                )
                .scalars()
                .all()
            )
            for m in merge_rows:
                claim_merge_rows_by_claim[m.claim_id].append(m.source_claim_id)

            support_rows = (
                (
                    await session.execute(
                        select(ClaimSupportRow)
                        .where(ClaimSupportRow.claim_id.in_([c.id for c in claim_rows]))
                        .order_by(ClaimSupportRow.claim_id, ClaimSupportRow.position)
                    )
                )
                .scalars()
                .all()
            )
            supports_by_claim: dict[str, list[ClaimSupport]] = defaultdict(list)
            for s in support_rows:
                supports_by_claim[s.claim_id].append(claim_support_from_row(s))
        else:
            supports_by_claim = {}

        claims = [
            claim_from_row(
                c,
                merged_from_claim_ids=claim_merge_rows_by_claim.get(c.id, []),
                supports=supports_by_claim.get(c.id, []),
            )
            for c in claim_rows
        ]

        processing_attempt_rows = (
            (
                await session.execute(
                    select(ClaimProcessingAttemptRow)
                    .where(ClaimProcessingAttemptRow.council_run_id == row.id)
                    .order_by(ClaimProcessingAttemptRow.position)
                )
            )
            .scalars()
            .all()
        )
        claim_processing_attempts = [
            claim_processing_attempt_from_row(a) for a in processing_attempt_rows
        ]

        verification_attempt_rows = (
            (
                await session.execute(
                    select(DeterministicVerificationAttemptRow)
                    .where(DeterministicVerificationAttemptRow.council_run_id == row.id)
                    .order_by(DeterministicVerificationAttemptRow.position)
                )
            )
            .scalars()
            .all()
        )
        numeric_verification_attempts = [
            deterministic_verification_attempt_from_row(a) for a in verification_attempt_rows
        ]

        debate_result = DebateResult(
            initial_result=initial_result,
            critique_round=critique_round,
            claims=claims,
            claim_processing_attempts=claim_processing_attempts,
            numeric_verification_attempts=numeric_verification_attempts,
            claim_processor_provider=row.claim_processor_provider,
            debate_skipped_reason=row.debate_skipped_reason,
            cumulative_budget_exceeded=row.debate_cumulative_budget_exceeded,
        )

        # Etapa 16 -- None SÓ quando source_analyzer_provider is None
        # (nenhuma fonte foi fornecida nesta Run: a coluna nunca é
        # preenchida nesse caso, ver save_success). Quando preenchida, o
        # objeto sempre existe, mesmo que attempts esteja vazio
        # (skipped_reason cobre esse caso).
        source_analysis_result = None
        if row.source_analyzer_provider is not None:
            sa_attempt_rows = (
                (
                    await session.execute(
                        select(SourceAnalysisAttemptRow)
                        .where(SourceAnalysisAttemptRow.council_run_id == row.id)
                        .order_by(SourceAnalysisAttemptRow.position)
                    )
                )
                .scalars()
                .all()
            )
            source_analysis_attempts = [
                source_analysis_attempt_from_row(a) for a in sa_attempt_rows
            ]

            sa_result_rows = (
                (
                    await session.execute(
                        select(SourceClaimAnalysisResultRow)
                        .where(SourceClaimAnalysisResultRow.council_run_id == row.id)
                        .order_by(SourceClaimAnalysisResultRow.position)
                    )
                )
                .scalars()
                .all()
            )
            source_claim_results = [
                source_claim_analysis_result_from_row(r) for r in sa_result_rows
            ]

            source_analysis_result = SourceAnalysisResult(
                attempts=source_analysis_attempts,
                claim_results=source_claim_results,
                skipped_reason=row.source_analysis_skipped_reason,
                source_analyzer_provider=row.source_analyzer_provider,
                cumulative_budget_exceeded=row.source_analysis_cumulative_budget_exceeded,
            )

        verdict = None
        verdict_row = (
            await session.execute(
                select(JudgeVerdictRow).where(JudgeVerdictRow.council_run_id == row.id)
            )
        ).scalar_one_or_none()
        if verdict_row is not None:
            assessment_rows = (
                (
                    await session.execute(
                        select(ClaimAssessmentRow)
                        .where(ClaimAssessmentRow.judge_verdict_id == verdict_row.id)
                        .order_by(ClaimAssessmentRow.position)
                    )
                )
                .scalars()
                .all()
            )
            assessments = [
                ClaimAssessment(claim_id=a.claim_id, verdict=a.verdict, explanation=a.explanation)
                for a in assessment_rows
            ]
            verdict = judge_verdict_from_row(verdict_row, claim_assessments=assessments)

        # attempt_number é uma chave de ordem real (não coincidência): o
        # loop de retry de SingleJudge/Editor sempre incrementa
        # sequencialmente, nunca em paralelo/fora de ordem -- ver
        # app/judge/single_judge.py -- então ORDER BY attempt_number
        # reconstrói a ordem original com garantia de contrato, sem
        # precisar de uma coluna position artificial aqui.
        judge_attempt_rows = (
            (
                await session.execute(
                    select(JudgeAttemptRow)
                    .where(JudgeAttemptRow.council_run_id == row.id)
                    .order_by(JudgeAttemptRow.attempt_number)
                )
            )
            .scalars()
            .all()
        )
        judge_result = JudgeResult(
            verdict=verdict,
            attempts=[judge_attempt_from_row(a) for a in judge_attempt_rows],
            verdict_unavailable_reason=row.judge_verdict_unavailable_reason,
            judge_provider=row.judge_provider,
            cumulative_budget_exceeded=row.judge_cumulative_budget_exceeded,
        )

        final_answer_row = (
            await session.execute(
                select(FinalAnswerRow).where(FinalAnswerRow.council_run_id == row.id)
            )
        ).scalar_one()
        final_answer = final_answer_from_row(final_answer_row)

        editor_attempt_rows = (
            (
                await session.execute(
                    select(EditorAttemptRow)
                    .where(EditorAttemptRow.council_run_id == row.id)
                    .order_by(EditorAttemptRow.attempt_number)
                )
            )
            .scalars()
            .all()
        )
        # `purpose` NULL = plano de estilo (todo registro anterior à coluna).
        editor_result = EditorResult(
            final_answer=final_answer,
            attempts=[
                editor_attempt_from_row(a)
                for a in editor_attempt_rows
                if a.purpose is None or a.purpose == "style_plan"
            ],
            primary_answer_attempts=[
                editor_attempt_from_row(a)
                for a in editor_attempt_rows
                if a.purpose == _PRIMARY_ANSWER_PURPOSE
            ],
            linguistic_realization_attempts=[
                editor_attempt_from_row(a)
                for a in editor_attempt_rows
                if a.purpose == _LINGUISTIC_REALIZATION_PURPOSE
            ],
            linguistic_semantic_review_attempts=[
                editor_attempt_from_row(a)
                for a in editor_attempt_rows
                if a.purpose == _LINGUISTIC_SEMANTIC_REVIEW_PURPOSE
            ],
            fallback_reason=row.editor_fallback_reason,
            primary_answer_fallback_reason=row.editor_primary_answer_fallback_reason,
            natural_answer_fallback_reason=row.editor_natural_answer_fallback_reason,
            linguistic_realization_fallback_reason=(
                row.editor_linguistic_realization_fallback_reason
            ),
            linguistic_semantic_review_provider=(
                row.editor_linguistic_semantic_review_provider
            ),
            editor_provider=row.editor_provider,
            cumulative_budget_exceeded=row.editor_cumulative_budget_exceeded,
        )

        # Cross-Channel Reconciliation V1 -- `None` SÓ quando nenhuma
        # linha `source_judge_reconciliations` existe pra este
        # `council_run_id` (execução persistida ANTES deste slice
        # existir). NUNCA reinterpretado como "not_comparable" (ver
        # docstring de SourceJudgeReconciliationResult) -- ausência
        # histórica de reconciliação estruturada é um fato diferente de
        # "os canais foram comparados e achados não-comparáveis".
        reconciliation = None
        reconciliation_row = (
            await session.execute(
                select(SourceJudgeReconciliationRow).where(
                    SourceJudgeReconciliationRow.council_run_id == row.id
                )
            )
        ).scalar_one_or_none()
        if reconciliation_row is not None:
            outcome_rows = (
                (
                    await session.execute(
                        select(ClaimReconciliationOutcomeRow)
                        .where(
                            ClaimReconciliationOutcomeRow.reconciliation_id
                            == reconciliation_row.id
                        )
                        .order_by(ClaimReconciliationOutcomeRow.position)
                    )
                )
                .scalars()
                .all()
            )
            source_result_link_rows = (
                (
                    await session.execute(
                        select(ClaimReconciliationSourceResultRow)
                        .where(
                            ClaimReconciliationSourceResultRow.outcome_id.in_(
                                [o.id for o in outcome_rows]
                            )
                        )
                        .order_by(
                            ClaimReconciliationSourceResultRow.outcome_id,
                            ClaimReconciliationSourceResultRow.position,
                        )
                    )
                )
                .scalars()
                .all()
            ) if outcome_rows else []
            source_result_ids_by_outcome_id: dict[str, list[str]] = defaultdict(list)
            for link in source_result_link_rows:
                source_result_ids_by_outcome_id[link.outcome_id].append(
                    link.source_claim_result_id
                )
            reconciliation = source_judge_reconciliation_from_rows(
                reconciliation_row, outcome_rows, source_result_ids_by_outcome_id
            )

        council_run_result = CouncilRunResult(
            id=row.id,
            run_config=_run_config_from_json(row.run_config_json),
            debate_result=debate_result,
            source_analysis_result=source_analysis_result,
            judge_result=judge_result,
            editor_result=editor_result,
            reconciliation=reconciliation,
            started_at=dt_from_naive_utc(row.started_at),
            completed_at=dt_from_naive_utc(row.completed_at),
        )
        return CompletedRunRecord(
            council_run_result=council_run_result,
            provider_execution_policy=_policy_from_json(row.provider_execution_policy_json),
            default_model_authority_snapshot=_default_model_authority_snapshot_from_json(
                row.default_model_authority_snapshot_json
            ),
        )

    async def _reconstruct_quorum_failure(
        self, session: AsyncSession, row: QuorumFailureRow
    ) -> QuorumFailureRecord:
        response_rows = (
            (
                await session.execute(
                    select(ModelResponseRow)
                    .where(ModelResponseRow.quorum_failure_id == row.id)
                    .order_by(ModelResponseRow.position)
                )
            )
            .scalars()
            .all()
        )
        responses = [model_response_from_row(r) for r in response_rows]
        round_result = _build_round_result(responses, round_number=row.round_number)

        return QuorumFailureRecord(
            id=row.id,
            started_at=dt_from_naive_utc(row.started_at),
            failed_at=dt_from_naive_utc(row.failed_at),
            run_config=_run_config_from_json(row.run_config_json),
            successful_count=row.successful_count,
            total_providers=row.total_providers,
            min_to_return=row.min_to_return,
            round_result=round_result,
            provider_execution_policy=_policy_from_json(row.provider_execution_policy_json),
            default_model_authority_snapshot=_default_model_authority_snapshot_from_json(
                row.default_model_authority_snapshot_json
            ),
        )


# ---------------------------------------------------------------------------
# Helpers de reconstrução de agregados derivados — recalculados via
# sum_usage_and_cost, NUNCA via coluna própria (RoundResult/
# InitialResponsesResult não têm tabela — ver docstring de models.py).
# ---------------------------------------------------------------------------


def _reconstruct_accepted(row: AcceptedRunRow) -> AcceptedRunRecord:
    """T02.4 -- reconstrução direta, sem sub-consultas: `accepted_runs`
    nunca tem tabelas filhas (ver docstring de `AcceptedRunRow`), então
    não há árvore nenhuma pra remontar além dos campos da própria
    linha."""
    return AcceptedRunRecord(
        status=row.status,  # type: ignore[arg-type]  # "running" | "failed"
        id=row.id,
        started_at=dt_from_naive_utc(row.started_at),
        run_config=_run_config_from_json(row.run_config_json),
        failed_at=dt_from_naive_utc(row.failed_at) if row.failed_at is not None else None,
        failure_classification=row.failure_classification,
        failure_message=row.failure_message,
        provider_execution_policy=_policy_from_json(row.provider_execution_policy_json),
        default_model_authority_snapshot=_default_model_authority_snapshot_from_json(
            row.default_model_authority_snapshot_json
        ),
    )


def _build_round_result(responses: list, *, round_number: int) -> RoundResult:
    total_input, total_output, total_cost, has_unknown = sum_usage_and_cost(responses)
    successful_count = sum(1 for r in responses if r.status == "success")
    return RoundResult(
        round_number=round_number,
        responses=responses,
        successful_count=successful_count,
        total_participants=len(responses),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost_usd=total_cost,
        has_unknown_accounting_components=has_unknown,
    )


def _build_initial_responses_result(
    responses: list, *, insufficient_data_for_consensus: bool, budget_exceeded: bool
) -> InitialResponsesResult:
    total_input, total_output, total_cost, has_unknown = sum_usage_and_cost(responses)
    successful_count = sum(1 for r in responses if r.status == "success")
    return InitialResponsesResult(
        responses=responses,
        successful_count=successful_count,
        total_providers=len(responses),
        insufficient_data_for_consensus=insufficient_data_for_consensus,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost_usd=total_cost,
        has_unknown_accounting_components=has_unknown,
        budget_exceeded=budget_exceeded,
    )
