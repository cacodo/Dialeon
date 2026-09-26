"""Direct Answer Execution V1 -- service, persistência e reconstrução.

Os providers aqui passam pelo `LLMProvider.complete()` REAL (só `_call_api`
é roteirizado): retry/timeout de transporte, bloqueio local por
pré-requisito, precificação e identidade de modelo são os de produção.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text

from app.application.errors import (
    InvalidQuestionError,
    LocalPrerequisitesMissingError,
    UnknownProviderError,
)
from app.config import Settings
from app.direct.models import DIRECT_ANSWER_CONTRACT_VERSION, DirectRunConfig, build_direct_request
from app.models.provider_models import ModelIdentitySource, ProviderExecutionPolicy, TokenUsage
from app.models.request_provenance import compute_request_digest
from app.providers import base as provider_base
from app.providers.errors import (
    ProviderAPIError,
    ProviderMalformedResponseError,
    ProviderTimeoutError,
)
from app.providers.pricing import ModelRate, PricingRegistry
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import AcceptedRunRecord, DirectAcceptedRunRecord, DirectRunRecord
from app.storage.repository import CouncilRepository
from tests.api.helpers import build_test_components
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import full_council_run_result, now, run_config

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
_CREATED = []


@pytest_asyncio.fixture(autouse=True)
async def _dispose():
    yield
    while _CREATED:
        await _CREATED.pop().engine.dispose()


async def components_with(**providers):
    components = await build_test_components(
        Settings(_env_file=None),
        provider_instances=providers,
        provider_execution_policy=POLICY,
        debate_result=full_council_run_result().debate_result,
    )
    _CREATED.append(components)
    return components


async def run_direct(components, provider="openai", question="Qual a capital do Brasil?", max_tokens=256):
    return await components.direct_service.run(
        question=question, provider=provider, max_output_tokens=max_tokens
    )


# ---------------------------------------------------------------------------
# Sucesso: UMA completion lógica, modelo configurado congelado, persistência
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_success_is_one_logical_request_with_the_configured_model_and_persists():
    provider = ScriptedApiProvider("openai", [ok("Brasília é a capital.")], default_model="gpt-conf")
    components = await components_with(openai=provider)

    result = await run_direct(components, max_tokens=321)

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.model == "gpt-conf"  # explícito, nunca None
    assert request.max_tokens == 321
    assert [m.content for m in request.messages] == ["Qual a capital do Brasil?"]
    assert request.system_prompt is None

    assert result.status == "completed"
    assert result.config == DirectRunConfig(
        question="Qual a capital do Brasil?", provider="openai", requested_model="gpt-conf", max_output_tokens=321
    )
    record = await components.repository.get_run(result.id)
    assert isinstance(record, DirectRunRecord)
    assert record.status == "completed"
    assert record.response.response_text == "Brasília é a capital."
    assert record.response.provider == "openai"
    assert record.response.round_number == 1
    assert record.provider_execution_policy == POLICY
    assert record == DirectRunRecord(
        status="completed",
        id=result.id,
        started_at=result.started_at,
        ended_at=result.ended_at,
        config=result.config,
        response=result.response,
        provider_execution_policy=POLICY,
    )


@pytest.mark.asyncio
async def test_direct_never_touches_any_council_stage():
    """O runner do Conselho (debate/extração, fonte, Judge, Editor) nunca é
    chamado -- os fakes do Conselho registram qualquer chamada."""
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)
    runner = components.service._runner

    await run_direct(components)

    assert runner._debate_engine.calls == []  # participantes/extração/crítica
    assert runner._source_analyzer.calls == []  # análise de fonte
    assert runner._judge.calls == []  # Judge
    assert runner._editor.calls == []  # Editor/realização linguística
    assert [s.kind for s in await components.repository.list_runs()] == ["direct"]
    # nenhuma tabela do Conselho ganhou linha
    async with components.session_factory() as session:
        for table in ("council_runs", "model_responses", "claims", "judge_verdicts", "final_answers"):
            count = (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
            assert count == 0, table


# ---------------------------------------------------------------------------
# Identidade de modelo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reported_model_divergence_is_preserved_next_to_the_requested_model():
    provider = ScriptedApiProvider(
        "openai", [ok(observed_model="gpt-conf-2026-09-01")], default_model="gpt-conf"
    )
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components)).id)

    assert record.config.requested_model == "gpt-conf"
    assert record.response.requested_model == "gpt-conf"
    assert record.response.model == "gpt-conf-2026-09-01"
    assert record.response.model_identity_source is ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_absent_report_uses_the_requested_fallback_semantics():
    provider = ScriptedApiProvider("openai", [ok(observed_model=None)], default_model="gpt-conf")
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components)).id)

    assert record.response.model == "gpt-conf"
    assert record.response.model_identity_source is ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_later_default_model_change_never_rewrites_an_accepted_direct_run():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)
    run_id = (await run_direct(components)).id

    provider._default_model_name = "gpt-new-default"  # deployment mudou depois
    record = await components.repository.get_run(run_id)

    assert record.config.requested_model == "gpt-conf"
    assert record.response.requested_model == "gpt-conf"


# ---------------------------------------------------------------------------
# Proveniência do request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_provenance_digests_the_exact_direct_request_including_the_model():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components, max_tokens=64)).id)

    provenance = record.response.request_provenance
    assert provenance.contract_version == DIRECT_ANSWER_CONTRACT_VERSION
    assert provenance.request_digest == compute_request_digest(provider.requests[0])
    assert provenance.request_digest == compute_request_digest(build_direct_request(record.config))
    other_model = build_direct_request(record.config.model_copy(update={"requested_model": "outro"}))
    assert provenance.request_digest != compute_request_digest(other_model)


# ---------------------------------------------------------------------------
# Custo / uso
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_cost_and_pricing_provenance():
    pricing = PricingRegistry(
        {("openai", "gpt-conf"): ModelRate(input_usd_per_million_tokens=1_000_000, output_usd_per_million_tokens=2_000_000)},
        source_id="tabela-teste",
    )
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf", pricing=pricing)
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components)).id)

    assert record.response.usage == TokenUsage(input_tokens=12, output_tokens=4)
    assert record.response.cost_usd == pytest.approx(12 + 8)
    assert record.response.pricing_provenance.source_id == "tabela-teste"


@pytest.mark.asyncio
async def test_unknown_pricing_is_unknown_never_zero():
    provider = ScriptedApiProvider("openai", [ok()], default_model="sem-preco")
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components)).id)

    assert record.response.cost_usd is None
    assert record.response.pricing_provenance is None


@pytest.mark.asyncio
async def test_known_zero_pricing_stays_a_known_zero():
    pricing = PricingRegistry(
        {("openai", "gratis"): ModelRate(input_usd_per_million_tokens=0, output_usd_per_million_tokens=0)}
    )
    provider = ScriptedApiProvider("openai", [ok()], default_model="gratis", pricing=pricing)
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components)).id)

    assert record.response.cost_usd == 0.0
    assert record.response.pricing_provenance is not None


@pytest.mark.asyncio
async def test_retry_after_timeout_is_one_logical_call_with_uncertain_earlier_attempt(monkeypatch):
    monkeypatch.setattr(provider_base, "_backoff_delay", lambda attempt: 0)
    provider = ScriptedApiProvider(
        "openai", [ProviderTimeoutError("timeout"), ok("depois do retry")], max_retries=1
    )
    components = await components_with(openai=provider)

    result = await run_direct(components)

    assert result.status == "completed"
    assert result.response.attempts == 2
    assert result.response.had_uncertain_prior_attempts is True
    assert len(provider.requests) == 2  # duas tentativas FÍSICAS, uma completion lógica
    assert provider.requests[0] == provider.requests[1]


# ---------------------------------------------------------------------------
# Validação antes do aceite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["", "   ", "x" * 20_001])
async def test_invalid_question_is_rejected_before_acceptance_and_dispatch(question):
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    with pytest.raises(InvalidQuestionError):
        await run_direct(components, question=question)

    assert provider.requests == []
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected_before_acceptance():
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    with pytest.raises(UnknownProviderError) as exc:
        await run_direct(components, provider="inexistente")

    assert exc.value.unknown_providers == ["inexistente"]
    assert provider.requests == []
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   "])
async def test_missing_prerequisite_is_rejected_before_acceptance_and_never_dispatched(key):
    provider = ScriptedApiProvider("openai", [ok()], api_key=key)
    components = await components_with(openai=provider)
    assert provider.local_prerequisite_state() == "missing"

    with pytest.raises(LocalPrerequisitesMissingError) as exc:
        await run_direct(components)

    assert exc.value.provider == "openai"
    assert provider.requests == []
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_unknown_prerequisite_is_attempted_honestly_without_preflight():
    provider = ScriptedApiProvider("openai", [ok()], prerequisite="unknown")
    components = await components_with(openai=provider)

    result = await run_direct(components)

    assert result.status == "completed"
    assert len(provider.requests) == 1  # a própria pergunta, nenhuma chamada extra de verificação


@pytest.mark.asyncio
async def test_met_prerequisite_is_not_remote_validation_the_provider_can_still_refuse():
    from app.providers.errors import ProviderAuthError

    provider = ScriptedApiProvider("openai", [ProviderAuthError("chave recusada")])
    components = await components_with(openai=provider)
    assert provider.local_prerequisite_state() == "met"

    result = await run_direct(components)

    assert result.status == "failed"
    assert result.response.error.type.value == "auth"


# ---------------------------------------------------------------------------
# Falhas: registro terminal honesto, sem troca de provider, sem Conselho
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, error_type, retryable",
    [
        (ProviderTimeoutError("timeout"), "timeout", True),
        (ProviderAPIError("503", retryable=True), "api_error", True),
        (ProviderMalformedResponseError("sem texto"), "malformed_response", False),
    ],
)
async def test_provider_failure_is_a_terminal_failed_direct_run_with_the_call_record(
    failure, error_type, retryable
):
    provider = ScriptedApiProvider("openai", [failure])
    other = ScriptedApiProvider("anthropic", [ok()])
    components = await components_with(openai=provider, anthropic=other)

    result = await run_direct(components)

    assert result.status == "failed"
    record = await components.repository.get_run(result.id)
    assert isinstance(record, DirectRunRecord)
    assert record.status == "failed"
    assert record.response.status == "error"
    assert record.response.error.type.value == error_type
    assert record.response.response_text is None
    assert record.response.cost_usd is None  # a chamada saiu: custo desconhecido, nunca zero
    assert other.requests == []  # nunca troca de provider
    assert components.service._runner._debate_engine.calls == []  # nunca cai no Conselho


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
async def test_blank_text_is_never_turned_into_an_answer(blank):
    provider = ScriptedApiProvider("openai", [ok(blank)])
    components = await components_with(openai=provider)

    result = await run_direct(components)

    record = await components.repository.get_run(result.id)
    assert isinstance(record, DirectRunRecord)
    assert record.status == "failed"
    assert record.response.response_text is None
    assert record.response.error.type.value == "malformed_response"
    # a chamada aconteceu: uso/identidade continuam registrados
    assert record.response.requested_model == "configured-model"


@pytest.mark.asyncio
async def test_unexpected_exception_marks_the_accepted_run_failed_and_reraises():
    class Boom(RuntimeError):
        pass

    provider = ScriptedApiProvider("openai", [])

    async def explode(request, *, execution_policy=None):
        raise Boom("detalhe interno com segredo")

    provider.complete = explode
    components = await components_with(openai=provider)

    with pytest.raises(Boom):
        await run_direct(components)

    [summary] = await components.repository.list_runs()
    record = await components.repository.get_run(summary.id)
    assert isinstance(record, DirectAcceptedRunRecord)
    assert record.status == "failed"
    assert record.failure_stage == "execution"
    assert record.failure_classification == "Boom"
    assert "segredo" not in record.failure_message


@pytest.mark.asyncio
async def test_terminal_persistence_failure_is_recorded_as_such_and_reraised(monkeypatch):
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    async def broken_save(result):
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(components.repository, "save_direct_result", broken_save)

    with pytest.raises(RuntimeError):
        await run_direct(components)

    [summary] = await components.repository.list_runs()
    record = await components.repository.get_run(summary.id)
    assert isinstance(record, DirectAcceptedRunRecord)
    assert record.failure_stage == "terminal_persistence"
    assert len(provider.requests) == 1  # nenhuma repetição da run


# ---------------------------------------------------------------------------
# Interrupção: aceita e sem desfecho confirmado continua honestamente assim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_but_unconfirmed_direct_run_stays_running_after_restart(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/direct.db"
    engine = create_engine(db_url)
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    config = DirectRunConfig(question="q", provider="openai", requested_model="gpt-conf", max_output_tokens=10)
    await repository.save_direct_accepted("run-interrompida", config=config, started_at=now(), provider_execution_policy=POLICY)
    await engine.dispose()  # "o processo morreu" antes do desfecho

    engine = create_engine(db_url)
    await init_db(engine)
    record = await CouncilRepository(make_session_factory(engine)).get_run("run-interrompida")
    [summary] = await CouncilRepository(make_session_factory(engine)).list_runs()
    await engine.dispose()

    assert isinstance(record, DirectAcceptedRunRecord)
    assert record.status == "running"  # nunca sucesso/falha fabricados
    assert record.config == config
    assert summary.kind == "direct" and summary.status == "running" and summary.ended_at is None


# ---------------------------------------------------------------------------
# Conselho e histórico: nunca reinterpretados como diretos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_provider_council_runs_stay_council():
    components = await build_test_components(Settings(_env_file=None))
    _CREATED.append(components)
    await components.repository.save_accepted(
        "conselho-um-provider",
        run_config=run_config(enabled_providers=["openai"]),
        started_at=now(),
        provider_execution_policy=POLICY,
    )

    record = await components.repository.get_run("conselho-um-provider")
    [summary] = await components.repository.list_runs()

    assert isinstance(record, AcceptedRunRecord)
    assert summary.kind == "council"


@pytest.mark.asyncio
async def test_legacy_database_without_run_kind_is_upgraded_and_reads_as_council(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/legacy.db"
    engine = create_engine(db_url)
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    await repository.save_accepted(
        "run-legada", run_config=run_config(enabled_providers=["openai"]), started_at=now(), provider_execution_policy=POLICY
    )
    await repository.save_success(full_council_run_result())
    # banco anterior a esta versão: sem a coluna e sem a tabela nova
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE accepted_runs DROP COLUMN run_kind"))
        await conn.execute(text("DROP TABLE direct_runs"))
    await engine.dispose()

    engine = create_engine(db_url)
    await init_db(engine)  # upgrade aditivo, sem backfill
    repository = CouncilRepository(make_session_factory(engine))
    legacy = await repository.get_run("run-legada")
    summaries = await repository.list_runs()
    async with engine.connect() as conn:
        kinds = (await conn.execute(text("SELECT run_kind FROM accepted_runs"))).scalars().all()
    await engine.dispose()

    assert isinstance(legacy, AcceptedRunRecord)
    assert kinds == [None]
    assert {s.kind for s in summaries} == {"council"}
    assert {s.status for s in summaries} == {"running", "completed"}


# ---------------------------------------------------------------------------
# `accepted_runs.run_kind` persistido: NULL = Conselho, "direct" = direta,
# qualquer outro valor não nulo = estado malformado (fail closed)
# ---------------------------------------------------------------------------

_DIRECT_CONFIG = DirectRunConfig(
    question="q", provider="openai", requested_model="gpt-conf", max_output_tokens=10
)


async def _repository_with_one_council_and_one_direct_accepted_run():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    repository = CouncilRepository(make_session_factory(engine))
    await repository.save_accepted(
        "conselho", run_config=run_config(enabled_providers=["openai"]), started_at=now(), provider_execution_policy=POLICY
    )
    await repository.save_direct_accepted(
        "direta", config=_DIRECT_CONFIG, started_at=now(), provider_execution_policy=POLICY
    )
    return engine, repository


async def _stored_run_kind(engine, run_id):
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT run_kind FROM accepted_runs WHERE id = :id"), {"id": run_id})
        ).scalar_one()


@pytest.mark.asyncio
async def test_persisted_run_kind_null_is_council_and_direct_is_direct():
    engine, repository = await _repository_with_one_council_and_one_direct_accepted_run()
    try:
        # Uma run do Conselho nova continua gravada exatamente como antes (NULL).
        assert await _stored_run_kind(engine, "conselho") is None
        assert await _stored_run_kind(engine, "direta") == "direct"
        council = await repository.get_run("conselho")
        direct = await repository.get_run("direta")
        kinds = {s.id: s.kind for s in await repository.list_runs()}
    finally:
        await engine.dispose()

    assert isinstance(council, AcceptedRunRecord)
    assert isinstance(direct, DirectAcceptedRunRecord)
    assert kinds == {"conselho": "council", "direta": "direct"}


_MALFORMED_RUN_KINDS = ["dircet", "foo", "council-v2", "council", "Direct", ""]


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_kind", _MALFORMED_RUN_KINDS)
@pytest.mark.parametrize("run_id", ["conselho", "direta"])
async def test_malformed_persisted_run_kind_fails_closed_on_detail_and_list(stored_kind, run_id):
    """Um valor não nulo desconhecido NUNCA é reconstruído como Conselho por
    exclusão -- nem numa linha com `run_config_json` de Conselho válido
    ("conselho"), onde a reconstrução antiga teria "funcionado" em
    silêncio. Detalhe (e auditoria, que lê pelo mesmo `get_run`) e listagem
    falham; a linha gravada não é normalizada nem regravada."""
    engine, repository = await _repository_with_one_council_and_one_direct_accepted_run()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE accepted_runs SET run_kind = :kind WHERE id = :id"),
                {"kind": stored_kind, "id": run_id},
            )

        with pytest.raises(ValidationError):
            await repository.get_run(run_id)
        with pytest.raises(ValidationError):
            await repository.list_runs()
        assert await _stored_run_kind(engine, run_id) == stored_kind
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_malformed_persisted_run_kind_also_fails_closed_for_a_failed_accepted_run():
    engine, repository = await _repository_with_one_council_and_one_direct_accepted_run()
    try:
        await repository.save_unexpected_failure(
            "conselho",
            failed_at=now(),
            failure_classification="RuntimeError",
            failure_message="Erro interno inesperado durante a execução.",
            failure_stage="execution",
        )
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE accepted_runs SET run_kind = 'dircet' WHERE id = 'conselho'"))

        with pytest.raises(ValidationError):
            await repository.get_run("conselho")
        with pytest.raises(ValidationError):
            await repository.list_runs()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_persisted_direct_run_reopens_after_its_provider_leaves_the_deployment():
    """Reabrir usa só o que foi persistido -- nunca o registry atual."""
    from app.presentation.mappers import direct_run_response

    provider = ScriptedApiProvider("openai", [ok("Brasília.")], default_model="gpt-conf")
    components = await components_with(openai=provider)
    run_id = (await run_direct(components)).id

    components.providers.clear()  # o provider saiu da configuração
    record = await components.repository.get_run(run_id)
    public = direct_run_response(record)

    assert public.answer == "Brasília."
    assert public.config.provider == "openai" and public.config.requested_model == "gpt-conf"
