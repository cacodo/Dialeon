from __future__ import annotations

import pytest

from app.application.errors import UnknownProviderError
from app.application.service import CouncilExecutionService
from app.council.runner import CouncilRunner
from tests.council.fakes import FakeSourceAnalyzer, FakeDebateEngine, FakeEditor, FakeJudge
from tests.council.fixtures import judge_result, model_response, run_config, verdict
from tests.storage.fixtures import full_council_run_result, quorum_failure_exception


@pytest.mark.asyncio
async def test_success_persists_and_returns_result(repo):
    result_to_return = full_council_run_result()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=result_to_return.debate_result),
        judge=FakeJudge(result=result_to_return.judge_result),
        editor=FakeEditor(result=result_to_return.editor_result),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repo, known_providers={"openai", "anthropic"}
    )

    returned = await service.run(run_config())

    assert returned.debate_result is result_to_return.debate_result
    loaded = await repo.get_run(returned.id)
    assert loaded is not None
    assert loaded.status == "completed"


@pytest.mark.asyncio
async def test_quorum_failure_persists_and_reraises(repo):
    exc = quorum_failure_exception()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(exc=exc),
        judge=FakeJudge(result=None),
        editor=FakeEditor(result=None),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repo, known_providers={"openai", "anthropic"}
    )

    with pytest.raises(type(exc)) as exc_info:
        await service.run(run_config())

    assert exc_info.value is exc  # a MESMA exceção, não uma reembalada

    summaries = await repo.list_runs()
    assert len(summaries) == 1
    assert summaries[0].status == "insufficient_quorum"

    loaded = await repo.get_run(summaries[0].id)
    assert loaded.round_result.successful_count == exc.successful_count


@pytest.mark.asyncio
async def test_quorum_failure_does_not_call_judge_or_editor(repo):
    exc = quorum_failure_exception()
    judge = FakeJudge(result=None)
    editor = FakeEditor(result=None)
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(exc=exc), judge=judge, editor=editor,
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repo, known_providers={"openai", "anthropic"}
    )

    with pytest.raises(type(exc)):
        await service.run(run_config())

    assert judge.calls == []
    assert editor.calls == []


@pytest.mark.asyncio
async def test_unrelated_exception_propagates_without_persisting(repo):
    """Erro de configuração/bug (fora do escopo desta etapa) propaga
    intacto -- nada é persistido, nenhuma tentativa de tratamento
    genérico."""
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(exc=ValueError("provider desconhecido")),
        judge=FakeJudge(result=None),
        editor=FakeEditor(result=None),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repo, known_providers={"openai", "anthropic"}
    )

    with pytest.raises(ValueError, match="provider desconhecido"):
        await service.run(run_config())

    summaries = await repo.list_runs()
    assert summaries == []


@pytest.mark.asyncio
async def test_unknown_provider_raises_before_calling_runner(repo):
    """Etapa 14 (T19A.1): a validação de enabled_providers desconhecidos
    mora AQUI -- CouncilExecutionService.run() -- não em app/api/routes.py.
    Esta é a boundary reutilizável de verdade: tanto o handler HTTP
    (POST /runs) quanto qualquer comando de CLI chamam exatamente este
    mesmo `service.run(run_config)`, então os dois compartilham a MESMA
    regra por construção -- nenhuma cópia da lista/checagem em dois
    lugares."""
    debate_engine = FakeDebateEngine(exc=AssertionError("nunca deveria ser chamado"))
    runner = CouncilRunner(
        debate_engine=debate_engine, judge=FakeJudge(result=None), editor=FakeEditor(result=None),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repo, known_providers={"openai", "anthropic"}
    )
    rc = run_config(enabled_providers=["openai", "provider-fake"])

    with pytest.raises(UnknownProviderError) as exc_info:
        await service.run(rc)

    assert exc_info.value.unknown_providers == ["provider-fake"]
    assert exc_info.value.known_providers == ["anthropic", "openai"]
    assert debate_engine.calls == []  # levantado ANTES de qualquer chamada ao runner

    summaries = await repo.list_runs()
    assert summaries == []  # nada persistido -- nem started_at foi capturado


@pytest.mark.asyncio
async def test_service_does_not_mint_run_id_before_calling_runner(repo):
    """Decision Delta §5: a boundary não minta run_id antes de chamar
    CouncilRunner -- pra sucesso, o id É o que CouncilRunner.run() produz
    (via CouncilRunResult.id, default_factory interno), nunca um id
    pré-mintado pela boundary. Confirmamos isso indiretamente: o id
    devolvido é exatamente o mesmo persistido no repo, sem nenhum id
    "paralelo" criado pelo service."""
    result_to_return = full_council_run_result()
    runner = CouncilRunner(
        debate_engine=FakeDebateEngine(result=result_to_return.debate_result),
        judge=FakeJudge(result=result_to_return.judge_result),
        editor=FakeEditor(result=result_to_return.editor_result),
        source_analyzer=FakeSourceAnalyzer(result=None),
    )
    service = CouncilExecutionService(
        runner=runner, repository=repo, known_providers={"openai", "anthropic"}
    )

    returned = await service.run(run_config())

    summaries = await repo.list_runs()
    assert len(summaries) == 1
    assert summaries[0].id == returned.id
