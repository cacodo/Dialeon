"""Council Accepted Effective Participant Model Choice V1 -- execução no
pipeline REAL (DebateEngine, SourceAnalyzer, SingleJudge, Editor,
CouncilExecutionService, CouncilRepository) com providers fake que só
roteirizam `_call_api`: o modelo congelado no aceite vai explícito na resposta
inicial E na crítica de cada participante; os papéis internos não herdam a
escolha; pedido e reportado continuam fatos distintos; nada é inventado."""

from __future__ import annotations

import pytest

from app.application.service import CouncilExecutionService
from app.council.runner import CouncilRunner
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.models.provider_models import ModelIdentitySource, ProviderExecutionPolicy
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.providers.errors import ProviderAPIError
from app.source_analysis.analyzer import SourceAnalyzer
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import CompletedRunRecord
from app.storage.repository import CouncilRepository
from tests.council.test_interpretation_failure_audit import _CallLog, _RoutedProvider, _stage_of

PROVIDERS = ("openai", "anthropic", "gemini")


class _RecordingProvider(_RoutedProvider):
    """O `_RoutedProvider` do pipeline real, gravando o `request.model` que
    CHEGOU a `_call_api` por estágio. `hide_reported_model`: o adapter não
    expõe identidade reportada (alias sem snapshot conhecido).
    `reject_models`: o fornecedor recusa esses modelos (erro de API)."""

    def __init__(self, name, log, *, hide_reported_model=False, reject_models=()):
        super().__init__(name, log, {}, "")
        self.seen: list[tuple[str, str | None]] = []
        self._hide = hide_reported_model
        self._reject = set(reject_models)

    async def _call_api(self, request):
        self.seen.append((_stage_of(request), request.model))
        if request.model in self._reject:
            raise ProviderAPIError(f"model not found: {request.model}", retryable=False)
        text, usage, reported, finish = await super()._call_api(request)
        return text, usage, (None if self._hide else reported), finish


def _config() -> RunConfig:
    return RunConfig(
        question="Qual é a capital da França?",
        enabled_providers=PROVIDERS,
        max_cost_usd=1.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=4096,
        max_output_tokens_grouping=8192,
        max_output_tokens_judge=8192,
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        round_dispatch_timeout_seconds=30.0,
        # todos os papéis internos no MESMO provider de um participante com escolha
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )


async def _execute(providers, overrides):
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
    result = await service.run(_config(), participant_model_overrides=overrides)
    record = await repository.get_run(result.id)
    await engine.dispose()
    assert isinstance(record, CompletedRunRecord)
    return record.council_run_result


def _responses(run, provider):
    initial = [r for r in run.debate_result.initial_result.responses if r.provider == provider]
    critique = [r for r in run.debate_result.critique_round.round_result.responses if r.provider == provider]
    return initial, critique


@pytest.mark.asyncio
async def test_initial_and_critique_use_the_frozen_model_and_internal_roles_keep_their_default():
    log = _CallLog()
    providers = {name: _RecordingProvider(name, log) for name in PROVIDERS}

    run = await _execute(providers, {"anthropic": "claude-explicit"})

    # mapa congelado no aceite, persistido no RunConfig da run
    assert [(c.provider, c.requested_model, c.origin) for c in run.run_config.participant_models] == [
        ("openai", "openai-requested", "configured_default"),
        ("anthropic", "claude-explicit", "run_override"),
        ("gemini", "gemini-requested", "configured_default"),
    ]
    anthropic_calls = providers["anthropic"].seen
    # participante: resposta inicial E crítica com o modelo escolhido
    assert [m for stage, m in anthropic_calls if stage == "participant"] == ["claude-explicit"]
    assert [m for stage, m in anthropic_calls if stage == "critique"] == ["claude-explicit"]
    # papéis internos do MESMO provider: nunca herdam a escolha (sem modelo no request)
    internal = [(stage, m) for stage, m in anthropic_calls if stage not in ("participant", "critique")]
    assert internal and {m for _, m in internal} == {None}
    # os demais participantes: o padrão configurado, explícito nas duas rodadas
    assert [m for stage, m in providers["openai"].seen] == ["openai-requested", "openai-requested"]

    initial, critique = _responses(run, "anthropic")
    for response in (*initial, *critique):
        # pedido e reportado são fatos distintos, os dois preservados
        assert response.requested_model == "claude-explicit"
        assert response.model == "anthropic-reported"
        assert response.model_identity_source is ModelIdentitySource.PROVIDER_REPORTED
    assert initial[0].request_provenance.contract_version == "initial_response_v2"
    assert critique[0].request_provenance.contract_version == "critique_v2"
    # papéis internos registram o padrão do provider como pedido
    assert {a.requested_model for a in run.debate_result.claim_processing_attempts} == {"anthropic-requested"}
    assert {a.requested_model for a in run.judge_result.attempts} == {"anthropic-requested"}


@pytest.mark.asyncio
async def test_alias_without_reported_identity_fabricates_nothing_and_unpriced_cost_stays_unknown():
    log = _CallLog()
    providers = {name: _RecordingProvider(name, log) for name in PROVIDERS}
    providers["openai"] = _RecordingProvider("openai", log, hide_reported_model=True)

    run = await _execute(providers, {"openai": "gpt-alias"})

    initial, critique = _responses(run, "openai")
    for response in (*initial, *critique):
        assert response.requested_model == "gpt-alias"
        # sem identidade reportada: só o fallback declarado, nunca um snapshot/versão inventado
        assert response.model == "gpt-alias"
        assert response.model_identity_source is ModelIdentitySource.REQUESTED_FALLBACK
        # sem preço conhecido pra esse identificador: custo DESCONHECIDO, nunca zero
        assert response.usage is not None
        assert response.cost_usd is None
        assert response.pricing_provenance is None
    assert run.debate_result.initial_result.has_unknown_accounting_components is True


@pytest.mark.asyncio
async def test_a_model_rejected_by_the_provider_is_a_recorded_runtime_failure_without_substitution():
    log = _CallLog()
    providers = {name: _RecordingProvider(name, log) for name in PROVIDERS}
    providers["gemini"] = _RecordingProvider("gemini", log, reject_models={"gemini-nonexistent"})

    run = await _execute(providers, {"gemini": "gemini-nonexistent"})

    [initial] = _responses(run, "gemini")[0]
    assert initial.status == "error"
    assert initial.requested_model == "gemini-nonexistent"
    assert initial.error.message == "model not found: gemini-nonexistent"
    # nenhuma outra tentativa com outro modelo (sem troca/fallback)
    assert providers["gemini"].seen == [("participant", "gemini-nonexistent")]
    # a execução segue com os demais participantes (crítica sem o gemini)
    assert _responses(run, "gemini")[1] == []
    assert run.editor_result.final_answer is not None
