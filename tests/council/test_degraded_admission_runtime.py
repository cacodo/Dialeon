"""Council Local Execution Readiness & Admission V1 -- execução degradada
RECONHECIDA no pipeline REAL (DebateEngine, SourceAnalyzer, SingleJudge,
Editor, CouncilExecutionService, CouncilRepository): o reconhecimento só
registra a decisão; a execução continua registrando tentativas e fallbacks
honestos, sem nenhuma troca de provider ou de modelo e sem chamada de rede
pro provider sem configuração local."""

from __future__ import annotations

import pytest

from app.application.service import CouncilExecutionService
from app.council.readiness import CouncilAdmissionRequest, CouncilExecutionDependencies
from app.council.runner import CouncilRunner
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.models.provider_models import ProviderErrorType, ProviderExecutionPolicy
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.source_analysis.analyzer import SourceAnalyzer
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import CompletedRunRecord
from app.storage.repository import CouncilRepository
from tests.council.test_interpretation_failure_audit import _CallLog, _RoutedProvider


class _LocallyMissingProvider(_RoutedProvider):
    """Mesmo provider roteirizado, mas com pré-requisito local ausente: o
    `LLMProvider.complete()` real bloqueia a chamada antes de `_call_api`."""

    def local_prerequisite_state(self):
        return "missing"


@pytest.mark.asyncio
async def test_acknowledged_degradation_runs_with_honest_local_failures_and_no_substitution():
    log = _CallLog()
    providers = {
        "openai": _RoutedProvider("openai", log, {}, ""),
        "gemini": _RoutedProvider("gemini", log, {}, ""),
        "anthropic": _LocallyMissingProvider("anthropic", log, {}, ""),
    }
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    service = CouncilExecutionService(
        runner=CouncilRunner(
            debate_engine=DebateEngine(providers),
            source_analyzer=SourceAnalyzer(providers),
            judge=SingleJudge(providers),
            editor=Editor(providers),
        ),
        repository=repository,
        providers=providers,
        provider_execution_policy=ProviderExecutionPolicy(
            attempt_timeout_seconds=30.0, max_transport_attempts_per_completion=1
        ),
    )
    config = RunConfig(
        question="Qual é a capital da França?",
        enabled_providers=["openai", "gemini"],
        max_cost_usd=1.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=4096,
        max_output_tokens_grouping=8192,
        max_output_tokens_judge=8192,
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        round_dispatch_timeout_seconds=30.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )

    shown = service.preview_readiness(CouncilExecutionDependencies.from_run_config(config))
    result = await service.run(
        config,
        admission=CouncilAdmissionRequest(
            acknowledge_known_degradation=True,
            acknowledged_degradation_fingerprint=shown.known_degradation_fingerprint,
        ),
    )
    record = await repository.get_run(result.id)
    await engine.dispose()

    assert isinstance(record, CompletedRunRecord)
    run = record.council_run_result

    # o reconhecimento fica registrado com a prontidão que foi reconhecida
    assert record.council_admission.known_degradation_acknowledged is True
    assert record.council_admission.acknowledged_degradation_fingerprint == shown.known_degradation_fingerprint
    assert record.council_admission.readiness.summary == "some_missing"

    # só os participantes com configuração local chegaram a `_call_api`; nada chegou à anthropic
    assert sorted(log.calls) == [("gemini", "participant"), ("openai", "participant")]

    # a extração foi tentada com o provider/modelo CONFIGURADO e falhou localmente
    attempts = run.debate_result.claim_processing_attempts
    assert attempts
    assert {(a.provider, a.requested_model) for a in attempts} == {("anthropic", "anthropic-requested")}
    assert {a.transport_status for a in attempts} == {"error"}
    assert {a.transport_error.type for a in attempts} == {ProviderErrorType.AUTH}
    assert {a.transport_attempts for a in attempts} == {0}  # nenhuma tentativa de rede

    # as etapas seguintes registram os fallbacks honestos -- nunca um Judge/Editor substituto
    assert run.debate_result.claims == []
    assert run.judge_result.verdict is None
    assert run.judge_result.verdict_unavailable_reason == "claim_extraction_failed"
    assert run.judge_result.judge_provider == "anthropic"
    assert run.judge_result.attempts == []
    assert run.editor_result.editor_provider == "anthropic"
    assert run.editor_result.fallback_reason == "judge_verdict_unavailable"
    assert run.editor_result.final_answer.status == "deterministic_no_verdict"
