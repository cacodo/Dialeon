"""Repair M2 -- falha GENUÍNA da persistência terminal (`save_success`)
nunca é classificada como falha de execução/provider, nunca é mascarada
por uma segunda falha, e nunca afirma ter preservado o que não preservou.

A falha injetada é real e não tem relação com fragmentos de auditoria:
uma tabela terminal some do SQLite antes do `save_success` REAL rodar,
então o INSERT falha dentro da transação atômica verdadeira.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.application.service import CouncilExecutionService
from app.council.runner import CouncilRunner
from app.storage.records import AcceptedRunRecord
from tests.api.helpers import TEST_PROVIDER_EXECUTION_POLICY, _FakeRegistryProvider
from tests.council.fakes import FakeDebateEngine, FakeEditor, FakeJudge, FakeSourceAnalyzer
from tests.council.fixtures import run_config
from tests.storage.fixtures import full_council_run_result

_MESSAGE = (
    "Falha ao persistir o resultado terminal da execução; o histórico "
    "detalhado da execução não foi preservado."
)


def _service(repo, *, debate_exc: Exception | None = None) -> CouncilExecutionService:
    result = full_council_run_result()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=result.debate_result, exc=debate_exc),
        judge=FakeJudge(result=result.judge_result),
        editor=FakeEditor(result=result.editor_result),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    return CouncilExecutionService(
        runner=runner,
        repository=repo,
        providers={name: _FakeRegistryProvider(f"{name}-fake-model") for name in ("openai", "anthropic")},
        provider_execution_policy=TEST_PROVIDER_EXECUTION_POLICY,
    )


def _break_terminal_storage(repo, engine) -> None:
    real_save_success = repo.save_success

    async def save_success_on_broken_storage(result):
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE final_answers"))
        await real_save_success(result)

    repo.save_success = save_success_on_broken_storage


async def test_genuine_terminal_storage_failure_is_recorded_as_persistence_failure(repo, engine, caplog):
    _break_terminal_storage(repo, engine)
    service = _service(repo)

    with caplog.at_level(logging.ERROR, logger="app.application.service"):
        with pytest.raises(OperationalError) as exc_info:
            await service.run(run_config())

    exc = exc_info.value
    assert "no such table" in str(exc.orig)  # a falha REAL do SQLite, não reembalada
    summaries = await repo.list_runs()
    assert [s.status for s in summaries] == ["failed"]
    record = await repo.get_run(summaries[0].id)
    assert isinstance(record, AcceptedRunRecord)  # nenhum histórico detalhado foi fabricado
    assert record.status == "failed"
    assert record.failure_stage == "terminal_persistence"
    assert record.failure_classification == "OperationalError"
    assert record.failure_message == _MESSAGE
    assert any("registrada como failed (failure_stage=terminal_persistence)" in n for n in exc.__notes__)
    # nada do payload/parâmetros da exceção vaza pra log ou banco
    assert "INSERT" not in caplog.text and "final_answers" not in caplog.text
    assert "OperationalError" in caplog.text


async def test_fallback_failure_never_masks_the_original_nor_claims_a_terminal_state(repo, engine, caplog):
    _break_terminal_storage(repo, engine)
    fallback_error = RuntimeError("o fallback também falhou")

    async def failing_fallback(*args, **kwargs):
        raise fallback_error

    repo.save_unexpected_failure = failing_fallback
    service = _service(repo)

    with caplog.at_level(logging.ERROR, logger="app.application.service"):
        with pytest.raises(OperationalError) as exc_info:
            await service.run(run_config())

    exc = exc_info.value
    assert exc.__context__ is not fallback_error
    assert exc.__cause__ is not fallback_error
    assert any("permanece 'running'" in n for n in exc.__notes__)
    assert not any("registrada como failed" in n for n in exc.__notes__)
    assert "RuntimeError" in caplog.text
    assert "o fallback também falhou" not in caplog.text
    summaries = await repo.list_runs()
    assert [s.status for s in summaries] == ["running"]
    record = await repo.get_run(summaries[0].id)
    assert record.status == "running"
    assert record.failure_stage is None


async def test_execution_failure_is_recorded_with_execution_stage(repo):
    class WeirdBug(RuntimeError):
        pass

    service = _service(repo, debate_exc=WeirdBug("bug"))

    with pytest.raises(WeirdBug):
        await service.run(run_config())

    [summary] = await repo.list_runs()
    record = await repo.get_run(summary.id)
    assert record.failure_stage == "execution"
    assert record.failure_classification == "WeirdBug"
    assert record.failure_message == "Erro interno inesperado durante a execução."


async def test_successful_save_is_still_a_single_atomic_terminal_transition(repo):
    service = _service(repo)

    returned = await service.run(run_config())

    [summary] = await repo.list_runs()
    assert (summary.id, summary.status) == (returned.id, "completed")
