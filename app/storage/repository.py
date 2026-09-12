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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.council.result import CouncilRunResult
from app.debate.result import CritiqueResult, DebateResult
from app.editor.result import EditorResult
from app.judge.result import JudgeResult
from app.source_analysis.result import SourceAnalysisResult
from app.models.domain import ClaimAssessment, ClaimSupport
from app.orchestrator.budget import sum_usage_and_cost
from app.orchestrator.config import RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.storage.database import session_scope
from app.storage.models import (
    ClaimAssessmentRow,
    ClaimMergeRow,
    ClaimProcessingAttemptRow,
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
)
from app.storage.records import CompletedRunRecord, QuorumFailureRecord, RunSummary
from app.storage.serializers import (
    dt_from_naive_utc,
    dt_to_naive_utc,
    claim_assessment_rows,
    claim_from_row,
    claim_merge_rows,
    claim_processing_attempt_from_row,
    claim_processing_attempt_to_row,
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
)


def _new_id() -> str:
    return str(uuid4())


def _run_config_from_json(data: dict) -> RunConfig:
    """Reconstrói `RunConfig` a partir do JSON persistido -- Etapa 17A.2
    adicionou `max_output_tokens_grouping`/`max_output_tokens_judge`
    (campos obrigatórios, sem default) a `RunConfig`, então um run
    persistido ANTES desta etapa não tem essas chaves no blob salvo.

    Backfill honesto, não um default arbitrário inventado agora: antes
    da Etapa 17A.2, agrupamento e Judge de fato usavam
    `max_output_tokens_per_call` (o único teto que existia) -- então
    reconstruir um run antigo com `max_output_tokens_grouping`/
    `max_output_tokens_judge` iguais ao `max_output_tokens_per_call`
    DAQUELE MESMO run reflete exatamente o que aconteceu de verdade
    naquela execução, nunca o novo default global (8192) de runs
    futuros, que não tem relação com o que essa execução histórica
    realmente usou. Um blob que já tem as chaves (run nativo da Etapa
    17A.2 em diante) nunca é alterado por este backfill."""
    if "max_output_tokens_grouping" in data and "max_output_tokens_judge" in data:
        return RunConfig(**data)
    data = dict(data)
    data.setdefault("max_output_tokens_grouping", data["max_output_tokens_per_call"])
    data.setdefault("max_output_tokens_judge", data["max_output_tokens_per_call"])
    return RunConfig(**data)


class CouncilRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # -----------------------------------------------------------------
    # SAVE
    # -----------------------------------------------------------------

    async def save_success(self, result: CouncilRunResult) -> None:
        """Persiste um `CouncilRunResult` completo numa única transação.

        Ordem de `session.add()` é topológica de propósito (Claim referencia
        ModelResponse, ClaimAssessment referencia Claim+JudgeVerdict,
        FinalAnswer referencia JudgeVerdict) -- SQLite verifica cada
        FOREIGN KEY imediatamente, não espera o fim da transação, então
        uma linha que referencia outra ainda não inserida falha na hora,
        mesmo dentro do mesmo commit."""
        debate = result.debate_result
        source_analysis = result.source_analysis_result
        judge = result.judge_result
        editor = result.editor_result

        async with session_scope(self._session_factory) as session:
            session.add(
                CouncilRunRow(
                    id=result.id,
                    status="completed",
                    started_at=dt_to_naive_utc(result.started_at),
                    completed_at=dt_to_naive_utc(result.completed_at),
                    run_config_json=result.run_config.model_dump(mode="json"),
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

    async def save_quorum_failure(
        self,
        exc: InsufficientQuorumError,
        *,
        run_config: RunConfig,
        started_at: datetime,
        failed_at: datetime,
    ) -> str:
        """Persiste uma `InsufficientQuorumError` capturada, junto com o
        `RoundResult` real que ela carrega (Decision Delta §3). Gera seu
        próprio id -- a exceção não tem run_id, nunca teve (o Council nunca
        chegou perto de mintar um)."""
        failure_id = _new_id()
        round_result = exc.round_result

        async with session_scope(self._session_factory) as session:
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
                )
            )
            await session.flush()
            for position, response in enumerate(round_result.responses):
                session.add(
                    model_response_to_row(
                        response, quorum_failure_id=failure_id, position=position
                    )
                )

        return failure_id

    # -----------------------------------------------------------------
    # LOAD
    # -----------------------------------------------------------------

    async def get_run(self, run_id: str) -> CompletedRunRecord | QuorumFailureRecord | None:
        async with self._session_factory() as session:
            run_row = await session.get(CouncilRunRow, run_id)
            if run_row is not None:
                return await self._reconstruct_completed(session, run_row)

            failure_row = await session.get(QuorumFailureRow, run_id)
            if failure_row is not None:
                return await self._reconstruct_quorum_failure(session, failure_row)

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
        explícita)."""
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

        summaries = [
            RunSummary(
                id=row.id,
                status="completed",
                started_at=dt_from_naive_utc(row.started_at),
                ended_at=dt_from_naive_utc(row.completed_at),
            )
            for row in completed
        ] + [
            RunSummary(
                id=row.id,
                status="insufficient_quorum",
                started_at=dt_from_naive_utc(row.started_at),
                ended_at=dt_from_naive_utc(row.failed_at),
            )
            for row in failures
        ]
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
                supports_by_claim[s.claim_id].append(
                    ClaimSupport(
                        model_response_id=s.model_response_id, provider=s.provider, model=s.model
                    )
                )
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
        editor_result = EditorResult(
            final_answer=final_answer,
            attempts=[editor_attempt_from_row(a) for a in editor_attempt_rows],
            fallback_reason=row.editor_fallback_reason,
            editor_provider=row.editor_provider,
            cumulative_budget_exceeded=row.editor_cumulative_budget_exceeded,
        )

        council_run_result = CouncilRunResult(
            id=row.id,
            run_config=_run_config_from_json(row.run_config_json),
            debate_result=debate_result,
            source_analysis_result=source_analysis_result,
            judge_result=judge_result,
            editor_result=editor_result,
            started_at=dt_from_naive_utc(row.started_at),
            completed_at=dt_from_naive_utc(row.completed_at),
        )
        return CompletedRunRecord(council_run_result=council_run_result)

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
        )


# ---------------------------------------------------------------------------
# Helpers de reconstrução de agregados derivados — recalculados via
# sum_usage_and_cost, NUNCA via coluna própria (RoundResult/
# InitialResponsesResult não têm tabela — ver docstring de models.py).
# ---------------------------------------------------------------------------


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
