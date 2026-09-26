"""Council Local Execution Readiness & Admission V1 -- a boundary
autoritativa (`CouncilExecutionService`): prévia e admissão usam o MESMO
avaliador; a admissão estrita recusa antes de qualquer registro ou chamada;
sem pedido de admissão, o aceite é o da v1.3.0; os fatos de aceite
sobrevivem à releitura e nunca são reescritos pela configuração atual."""

from __future__ import annotations

import pytest

from app.application.errors import CouncilPrerequisitesMissingError, UnknownProviderError
from app.application.service import CouncilExecutionService
from app.council.readiness import CouncilAdmissionRequest, CouncilExecutionDependencies
from app.council.runner import CouncilRunner
from app.models.provider_models import ProviderExecutionPolicy
from app.orchestrator.errors import InsufficientQuorumError
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import AcceptedRunRecord, CompletedRunRecord, QuorumFailureRecord
from app.storage.repository import CouncilRepository
from tests.council.fakes import FakeDebateEngine, FakeEditor, FakeJudge, FakeSourceAnalyzer
from tests.direct.fakes import ScriptedApiProvider
from tests.storage.fixtures import full_council_run_result, quorum_failure_exception, run_config

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
STRICT = CouncilAdmissionRequest(mode="strict")


def _providers(**states):
    """openai/anthropic participam; gemini é o provider dos papéis internos
    nos testes (ver `_config`). Todo provider é um `LLMProvider` real (só
    `_call_api` roteirizado): qualquer chamada apareceria em `.requests`."""
    return {
        name: ScriptedApiProvider(name, [], default_model=f"{name}-configured", prerequisite=states.get(name))
        for name in ("openai", "anthropic", "gemini")
    }


def _config(**overrides):
    fields = dict(
        enabled_providers=["openai", "anthropic"],
        claim_processor_provider="gemini",
        judge_provider="gemini",
        editor_provider="gemini",
        source_analyzer_provider="gemini",
    )
    fields.update(overrides)
    return run_config(**fields)


class _Harness:
    def __init__(self, service, repository, debate_engine, engine, providers, result):
        self.service = service
        self.result = result
        self.repository = repository
        self.debate_engine = debate_engine
        self.engine = engine
        self.providers = providers


async def _harness(providers, *, debate_exc: Exception | None = None) -> _Harness:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    result = full_council_run_result()
    debate_engine = FakeDebateEngine(result=result.debate_result, exc=debate_exc)
    runner = CouncilRunner(
        debate_engine=debate_engine,
        source_analyzer=FakeSourceAnalyzer(result=None),
        judge=FakeJudge(result=result.judge_result),
        editor=FakeEditor(result=result.editor_result),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repository, providers=providers, provider_execution_policy=POLICY
    )
    return _Harness(service, repository, debate_engine, engine, providers, result)


def _no_provider_was_called(providers) -> bool:
    return all(p.requests == [] for p in providers.values())


# ---------------------------------------------------------------------------
# Admissão estrita
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_rejects_a_known_missing_internal_role_before_persistence_and_calls():
    h = await _harness(_providers(gemini="missing"))

    with pytest.raises(CouncilPrerequisitesMissingError) as caught:
        await h.service.run(_config(), admission=STRICT)

    assert {d.role for d in caught.value.readiness.known_missing} == {
        "claim_extraction",
        "judge",
        "editor",
        "semantic_review",
    }
    assert await h.repository.list_runs() == []  # nenhum registro de aceite
    assert h.debate_engine.calls == []  # o runner nunca foi chamado
    assert _no_provider_was_called(h.providers)
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_strict_rejects_a_known_missing_participant():
    h = await _harness(_providers(anthropic="missing"))

    with pytest.raises(CouncilPrerequisitesMissingError) as caught:
        await h.service.run(_config(), admission=STRICT)

    assert [(d.role, d.provider) for d in caught.value.readiness.known_missing] == [
        ("participant", "anthropic")
    ]
    assert await h.repository.list_runs() == []
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_strict_admits_unknown_and_records_it_as_unknown_never_missing():
    h = await _harness(_providers(gemini="unknown"))

    result = await h.service.run(_config(), admission=STRICT)
    record = await h.repository.get_run(result.id)

    assert isinstance(record, CompletedRunRecord)
    admission = record.council_admission
    assert admission.mode == "strict"
    assert admission.readiness.summary == "some_unknown"
    assert admission.readiness.known_missing == ()
    assert {d.local_prerequisite for d in admission.readiness.dependencies if d.provider == "gemini"} == {"unknown"}
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_strict_with_everything_met_is_accepted():
    h = await _harness(_providers())

    result = await h.service.run(_config(), admission=STRICT)
    record = await h.repository.get_run(result.id)

    assert record.council_admission.mode == "strict"
    assert record.council_admission.known_degradation_acknowledged is False
    assert record.council_admission.readiness.summary == "all_met"
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_strict_ignores_a_missing_source_analyzer_when_no_source_was_given():
    h = await _harness(_providers(gemini="missing"))
    config = _config(claim_processor_provider="openai", judge_provider="openai", editor_provider="openai")

    result = await h.service.run(config, admission=STRICT)
    record = await h.repository.get_run(result.id)

    source = [d for d in record.council_admission.readiness.dependencies if d.role == "source_analysis"]
    assert [(d.provider, d.local_prerequisite, d.applicability) for d in source] == [
        ("gemini", "missing", "not_applicable")
    ]
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_strict_blocks_a_missing_source_analyzer_when_a_source_was_given():
    h = await _harness(_providers(gemini="missing"))
    config = _config(
        claim_processor_provider="openai",
        judge_provider="openai",
        editor_provider="openai",
        source_text="um texto de referência",
    )

    with pytest.raises(CouncilPrerequisitesMissingError) as caught:
        await h.service.run(config, admission=STRICT)

    assert [d.role for d in caught.value.readiness.known_missing] == ["source_analysis"]
    assert await h.repository.list_runs() == []
    await h.engine.dispose()


# ---------------------------------------------------------------------------
# Aceite padrão (v1.3.0) e reconhecimento de degradação
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_admission_request_the_v130_acceptance_is_unchanged_and_facts_are_recorded():
    h = await _harness(_providers(gemini="missing"))

    result = await h.service.run(_config())  # chamada de sempre, sem admissão
    record = await h.repository.get_run(result.id)

    assert isinstance(record, CompletedRunRecord)
    assert len(h.debate_engine.calls) == 1  # executou mesmo com degradação conhecida
    assert record.council_admission.mode == "standard"
    assert record.council_admission.known_degradation_acknowledged is False
    assert record.council_admission.readiness.summary == "some_missing"
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_acknowledged_degradation_is_recorded_with_the_readiness_that_was_acknowledged():
    h = await _harness(_providers(gemini="missing"))

    result = await h.service.run(
        _config(), admission=CouncilAdmissionRequest(acknowledge_known_degradation=True)
    )
    record = await h.repository.get_run(result.id)

    assert record.council_admission.mode == "standard"
    assert record.council_admission.known_degradation_acknowledged is True
    assert {d.role for d in record.council_admission.readiness.known_missing} == {
        "claim_extraction",
        "judge",
        "editor",
        "semantic_review",
    }
    await h.engine.dispose()


# ---------------------------------------------------------------------------
# Prévia == admissão
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("states", [{}, {"gemini": "missing"}, {"openai": "unknown", "gemini": "missing"}])
@pytest.mark.parametrize("source_text", [None, "fonte"])
async def test_preview_and_acceptance_produce_the_same_readiness(states, source_text):
    h = await _harness(_providers(**states))
    config = _config(source_text=source_text)

    preview = h.service.preview_readiness(CouncilExecutionDependencies.from_run_config(config))
    result = await h.service.run(config)
    record = await h.repository.get_run(result.id)

    assert record.council_admission.readiness == preview
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_preview_creates_nothing_and_calls_nothing():
    h = await _harness(_providers(gemini="missing"))

    readiness = h.service.preview_readiness(CouncilExecutionDependencies.from_run_config(_config()))

    assert readiness.summary == "some_missing"
    assert await h.repository.list_runs() == []
    assert h.debate_engine.calls == []
    assert _no_provider_was_called(h.providers)
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_preview_rejects_an_unknown_provider_exactly_like_creation():
    h = await _harness(_providers())

    with pytest.raises(UnknownProviderError) as preview_error:
        h.service.preview_readiness(
            CouncilExecutionDependencies.from_run_config(_config(enabled_providers=["openai", "nope"]))
        )
    with pytest.raises(UnknownProviderError) as run_error:
        await h.service.run(_config(enabled_providers=["openai", "nope"]), admission=STRICT)

    assert preview_error.value.unknown_providers == run_error.value.unknown_providers == ["nope"]
    await h.engine.dispose()


# ---------------------------------------------------------------------------
# Persistência: releitura, configuração posterior, outros desfechos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_time_facts_are_never_rewritten_by_a_later_configuration():
    providers = _providers(gemini="missing")
    h = await _harness(providers)
    result = await h.service.run(_config(), admission=CouncilAdmissionRequest(acknowledge_known_degradation=True))

    # a configuração muda depois do aceite (ex.: chave adicionada e reinício)
    providers["gemini"]._prerequisite = "met"
    providers["gemini"]._default_model_name = "gemini-reconfigured"
    later_preview = h.service.preview_readiness(CouncilExecutionDependencies.from_run_config(_config()))
    record = await h.repository.get_run(result.id)

    assert later_preview.summary == "all_met"
    assert record.council_admission.readiness.summary == "some_missing"
    assert {
        d.configured_default_model for d in record.council_admission.readiness.dependencies if d.provider == "gemini"
    } == {"gemini-configured"}
    assert record.council_admission.known_degradation_acknowledged is True
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_quorum_failure_carries_the_acceptance_facts():
    h = await _harness(_providers(gemini="unknown"), debate_exc=quorum_failure_exception())

    with pytest.raises(InsufficientQuorumError) as caught:
        await h.service.run(_config(), admission=STRICT)
    record = await h.repository.get_run(caught.value.persisted_failure_id)

    assert isinstance(record, QuorumFailureRecord)
    assert record.council_admission.mode == "strict"
    assert record.council_admission.readiness.summary == "some_unknown"
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_unexpected_failure_keeps_the_acceptance_facts_on_the_failed_record():
    h = await _harness(_providers(gemini="missing"), debate_exc=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await h.service.run(_config(), admission=CouncilAdmissionRequest(acknowledge_known_degradation=True))
    [summary] = await h.repository.list_runs()
    record = await h.repository.get_run(summary.id)

    assert isinstance(record, AcceptedRunRecord)
    assert record.status == "failed"
    assert record.council_admission.known_degradation_acknowledged is True
    assert record.council_admission.readiness.summary == "some_missing"
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_running_record_carries_the_acceptance_facts_before_any_outcome():
    h = await _harness(_providers())
    seen = {}

    class InspectingDebateEngine(FakeDebateEngine):
        async def run(self, run_config):
            [summary] = await h.repository.list_runs()
            seen["record"] = await h.repository.get_run(summary.id)
            return await super().run(run_config)

    h.service._runner._debate_engine = InspectingDebateEngine(result=h.result.debate_result)
    await h.service.run(_config(), admission=STRICT)

    running = seen["record"]
    assert isinstance(running, AcceptedRunRecord) and running.status == "running"
    assert running.council_admission.mode == "strict"
    await h.engine.dispose()
