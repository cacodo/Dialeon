"""Direct Accepted Effective Model Choice V1 -- service, persistência,
leitura histórica e proveniência.

Uma run direta pode pedir UM modelo explícito pro provider escolhido. Sem
escolha, nada muda (o padrão configurado, `configured_default`); com
escolha, o identificador validado é congelado no aceite (`run_override`) e
é o único modelo pedido ao provider, em toda tentativa. Os providers passam
pelo `LLMProvider.complete()` REAL (só `_call_api` é roteirizado).
"""

from __future__ import annotations

from typing import get_args

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text

from app.application.errors import InvalidDirectModelError, LocalPrerequisitesMissingError
from app.config import Settings
from app.direct.models import (
    DIRECT_ANSWER_CONTRACT_VERSION,
    DirectModelOrigin,
    DirectRunConfig,
    build_direct_request,
)
from app.models.provider_models import ModelIdentitySource, ProviderExecutionPolicy
from app.models.request_provenance import build_request_provenance, compute_request_digest
from app.orchestrator.participant_models import MAX_MODEL_IDENTIFIER_CHARACTERS, ParticipantModelOrigin
from app.presentation.mappers import direct_accepted_run_response, direct_run_response
from app.providers import base as provider_base
from app.providers.errors import ProviderAPIError, ProviderTimeoutError
from app.providers.pricing import ModelRate, PricingRegistry
from app.storage.records import DirectAcceptedRunRecord, DirectRunRecord
from tests.api.helpers import build_test_components
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import full_council_run_result, now

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


async def run_direct(components, requested_model=None, provider="openai", max_tokens=256):
    return await components.direct_service.run(
        question="Qual a capital do Brasil?",
        provider=provider,
        max_output_tokens=max_tokens,
        requested_model=requested_model,
    )


async def _stored_config_json(components, table, run_id):
    async with components.engine.connect() as conn:
        return (
            await conn.execute(
                text(f"SELECT run_config_json FROM {table} WHERE id = :id"), {"id": run_id}
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Omissão: o comportamento de sempre
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_omitted_model_freezes_the_configured_default_exactly_as_before():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)

    result = await run_direct(components)

    assert result.config.requested_model == "gpt-conf"
    assert result.config.requested_model_origin == "configured_default"
    assert [r.model for r in provider.requests] == ["gpt-conf"]
    assert result.response.requested_model == "gpt-conf"


# ---------------------------------------------------------------------------
# Escolha explícita
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_model_is_frozen_as_a_run_override_and_is_the_only_model_requested():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)

    result = await run_direct(components, "gpt-explicit")
    record = await components.repository.get_run(result.id)

    assert (record.config.requested_model, record.config.requested_model_origin) == (
        "gpt-explicit",
        "run_override",
    )
    assert [r.model for r in provider.requests] == ["gpt-explicit"]
    assert record.response.requested_model == "gpt-explicit"


@pytest.mark.asyncio
async def test_typing_the_configured_default_explicitly_is_still_a_run_override():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)

    result = await run_direct(components, "gpt-conf")

    assert (result.config.requested_model, result.config.requested_model_origin) == ("gpt-conf", "run_override")


@pytest.mark.asyncio
async def test_the_explicit_identifier_is_used_verbatim():
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    result = await run_direct(components, "modèle-é:v2")

    assert result.config.requested_model == "modèle-é:v2"
    assert provider.requests[0].model == "modèle-é:v2"


class _InspectingProvider(ScriptedApiProvider):
    """Olha o registro de aceite DURANTE a chamada: o modelo e a origem já
    precisam estar congelados e persistidos antes do provider ser chamado."""

    repository = None
    seen_during_call = None

    async def _call_api(self, request):
        [summary] = await self.repository.list_runs()
        self.seen_during_call = await self.repository.get_run(summary.id)
        return await super()._call_api(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested_model, expected",
    [(None, ("gpt-conf", "configured_default")), ("gpt-explicit", ("gpt-explicit", "run_override"))],
)
async def test_model_and_origin_are_persisted_in_the_acceptance_before_the_provider_call(requested_model, expected):
    provider = _InspectingProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)
    provider.repository = components.repository

    await run_direct(components, requested_model)

    seen = provider.seen_during_call
    assert isinstance(seen, DirectAcceptedRunRecord) and seen.status == "running"
    assert (seen.config.requested_model, seen.config.requested_model_origin) == expected


@pytest.mark.asyncio
async def test_a_later_default_change_never_rewrites_an_accepted_override():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")
    components = await components_with(openai=provider)
    run_id = (await run_direct(components, "gpt-explicit")).id

    provider._default_model_name = "gpt-new-default"  # deployment mudou depois
    record = await components.repository.get_run(run_id)

    assert (record.config.requested_model, record.config.requested_model_origin) == ("gpt-explicit", "run_override")
    assert record.response.requested_model == "gpt-explicit"


class _DefaultChangesMidRun(ScriptedApiProvider):
    """A configuração do provider muda entre a 1ª e a 2ª tentativa."""

    async def _call_api(self, request):
        self._default_model_name = f"changed-{len(self.requests)}"
        return await super()._call_api(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested_model, frozen", [("gpt-explicit", "gpt-explicit"), (None, "gpt-conf")]
)
async def test_every_retry_sends_the_frozen_model_even_if_the_default_changes_in_between(
    monkeypatch, requested_model, frozen
):
    monkeypatch.setattr(provider_base, "_backoff_delay", lambda attempt: 0)
    provider = _DefaultChangesMidRun(
        "openai", [ProviderTimeoutError("timeout"), ok("depois do retry")], default_model="gpt-conf", max_retries=1
    )
    components = await components_with(openai=provider)

    result = await run_direct(components, requested_model)

    assert result.status == "completed"
    assert result.response.attempts == 2
    assert [r.model for r in provider.requests] == [frozen, frozen]
    assert result.response.requested_model == frozen
    assert result.config.requested_model == frozen


# ---------------------------------------------------------------------------
# Validação estrita da entrada nova, sem efeito colateral
# ---------------------------------------------------------------------------


_INVALID = [
    "",
    "   ",
    " gpt-x",
    "gpt-x ",
    "gpt x",
    "gpt\tx",
    "gpt\x00x",
    "gpt\x85-x",  # C1 (NEL)
    "gpt\x9b-x",  # C1 (CSI)
    "gpt‮-x",  # RIGHT-TO-LEFT OVERRIDE (Cf)
    "gpt​-x",  # ZERO WIDTH SPACE (Cf)
    "gpt x",  # NO-BREAK SPACE (Zs)
    "gpt͏x",  # COMBINING GRAPHEME JOINER (Default_Ignorable, Mn)
    "gpt️x",  # VARIATION SELECTOR-16 (Default_Ignorable, Mn)
    "gptㅤx",  # HANGUL FILLER (Default_Ignorable, Lo)
    "g" * (MAX_MODEL_IDENTIFIER_CHARACTERS + 1),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", _INVALID)
async def test_malformed_explicit_models_are_rejected_before_acceptance_and_dispatch(value):
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    with pytest.raises(InvalidDirectModelError) as caught:
        await run_direct(components, value)

    assert await components.repository.list_runs() == []
    assert provider.requests == []
    invisible = [c for c in value if not c.isprintable()]
    assert not any(c in caught.value.reason for c in invisible)  # nunca ecoado cru


@pytest.mark.asyncio
async def test_the_length_cap_is_inclusive():
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    result = await run_direct(components, "g" * MAX_MODEL_IDENTIFIER_CHARACTERS)

    assert result.config.requested_model_origin == "run_override"


@pytest.mark.asyncio
async def test_an_explicit_model_does_not_bypass_the_local_prerequisite_rejection():
    provider = ScriptedApiProvider("openai", [ok()], api_key=None)
    components = await components_with(openai=provider)

    with pytest.raises(LocalPrerequisitesMissingError):
        await run_direct(components, "gpt-explicit")

    assert await components.repository.list_runs() == []
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Recusa remota, identidade e custo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_remote_rejection_of_the_chosen_model_is_a_normal_failure_without_substitution():
    openai = ScriptedApiProvider(
        "openai",
        [ProviderAPIError("openai: status=404: model not found", retryable=False), ok("nunca")],
        default_model="gpt-conf",
        max_retries=2,
    )
    anthropic = ScriptedApiProvider("anthropic", [ok()])
    components = await components_with(openai=openai, anthropic=anthropic)

    result = await run_direct(components, "gpt-does-not-exist")
    record = await components.repository.get_run(result.id)
    public = direct_run_response(record)

    assert record.status == "failed"
    assert record.response.error.type.value == "api_error"
    assert [r.model for r in openai.requests] == ["gpt-does-not-exist"]  # uma chamada, nenhum 2º modelo
    assert anthropic.requests == []  # nenhum outro provider
    assert (record.config.requested_model, record.config.requested_model_origin) == (
        "gpt-does-not-exist",
        "run_override",
    )
    assert record.response.requested_model == "gpt-does-not-exist"
    assert record.response.cost_usd is None  # a chamada saiu: custo desconhecido, nunca zero
    assert public.accounting.has_unknown_accounting_components is True
    assert public.failure_reason == "api_error"


@pytest.mark.asyncio
async def test_the_reported_identity_stays_separate_from_the_chosen_model():
    provider = ScriptedApiProvider("openai", [ok(observed_model="gpt-explicit-2026-09-01")])
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components, "gpt-explicit")).id)

    assert record.config.requested_model == "gpt-explicit"
    assert record.response.requested_model == "gpt-explicit"
    assert record.response.model == "gpt-explicit-2026-09-01"
    assert record.response.model_identity_source is ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_an_unpriced_chosen_model_has_unknown_cost_never_zero():
    pricing = PricingRegistry(
        {("openai", "gpt-conf"): ModelRate(input_usd_per_million_tokens=1, output_usd_per_million_tokens=1)}
    )
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-conf", pricing=pricing)
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components, "gpt-sem-preco")).id)
    public = direct_run_response(record)

    assert record.response.cost_usd is None
    assert record.response.pricing_provenance is None
    assert public.accounting.has_unknown_accounting_components is True


# ---------------------------------------------------------------------------
# A resposta direta continua sendo resposta direta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_explicit_model_keeps_direct_a_single_completion_outside_the_council():
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)
    runner = components.service._runner

    await run_direct(components, "gpt-explicit")

    assert len(provider.requests) == 1
    assert runner._debate_engine.calls == []
    assert runner._source_analyzer.calls == []
    assert runner._judge.calls == []
    assert runner._editor.calls == []
    assert [s.kind for s in await components.repository.list_runs()] == ["direct"]
    async with components.session_factory() as session:
        for table in ("council_runs", "model_responses", "claims", "judge_verdicts", "final_answers"):
            count = (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
            assert count == 0, table


# ---------------------------------------------------------------------------
# Gravação nova e leitura histórica
# ---------------------------------------------------------------------------


def test_a_new_direct_config_cannot_be_built_without_its_origin():
    with pytest.raises(ValidationError):
        DirectRunConfig(question="q", provider="openai", requested_model="m", max_output_tokens=10)


def test_the_direct_origin_vocabulary_matches_the_council_participant_vocabulary():
    assert set(get_args(DirectModelOrigin)) == set(get_args(ParticipantModelOrigin)) == {
        "configured_default",
        "run_override",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_model, origin", [(None, "configured_default"), ("gpt-explicit", "run_override")])
async def test_every_new_write_persists_the_origin(requested_model, origin):
    components = await components_with(openai=ScriptedApiProvider("openai", [ok()]))

    run_id = (await run_direct(components, requested_model)).id

    stored = await _stored_config_json(components, "direct_runs", run_id)
    assert '"requested_model_origin"' in stored
    assert f'"{origin}"' in stored


async def _legacy_rows(components):
    """Uma linha terminal e uma de aceite escritas como antes deste campo."""
    completed_id = (await run_direct(components)).id
    config = DirectRunConfig(
        question="q", provider="openai", requested_model="gpt-then", requested_model_origin="configured_default",
        max_output_tokens=10,
    )
    await components.repository.save_direct_accepted(
        "aceita-legada", config=config, started_at=now(), provider_execution_policy=POLICY
    )
    async with components.engine.begin() as conn:
        for table, run_id in (("direct_runs", completed_id), ("accepted_runs", "aceita-legada")):
            await conn.execute(
                text(
                    f"UPDATE {table} SET run_config_json = "
                    "json_remove(run_config_json, '$.requested_model_origin') WHERE id = :id"
                ),
                {"id": run_id},
            )
    return completed_id, "aceita-legada"


@pytest.mark.asyncio
async def test_a_pre_slice_row_reads_as_configured_default_with_its_model_and_is_never_rewritten():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-then")
    components = await components_with(openai=provider)
    completed_id, accepted_id = await _legacy_rows(components)
    provider._default_model_name = "gpt-now"  # a configuração atual nunca é consultada

    completed = await components.repository.get_run(completed_id)
    accepted = await components.repository.get_run(accepted_id)

    assert isinstance(completed, DirectRunRecord) and isinstance(accepted, DirectAcceptedRunRecord)
    for record in (completed, accepted):
        assert (record.config.requested_model, record.config.requested_model_origin) == (
            "gpt-then",
            "configured_default",
        )
    assert direct_run_response(completed).config.requested_model_origin == "configured_default"
    assert direct_accepted_run_response(accepted).config.requested_model_origin == "configured_default"
    # nada foi regravado
    assert "requested_model_origin" not in await _stored_config_json(components, "direct_runs", completed_id)
    assert "requested_model_origin" not in await _stored_config_json(components, "accepted_runs", accepted_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["null", "'automatic'", "'Run_Override'", "''"])
@pytest.mark.parametrize("table", ["direct_runs", "accepted_runs"])
async def test_a_present_but_malformed_origin_fails_closed_instead_of_becoming_the_default(stored, table):
    components = await components_with(openai=ScriptedApiProvider("openai", [ok()]))
    completed_id, accepted_id = await _legacy_rows(components)
    run_id = completed_id if table == "direct_runs" else accepted_id
    async with components.engine.begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {table} SET run_config_json = "
                f"json_set(run_config_json, '$.requested_model_origin', {stored}) WHERE id = :id"
            ),
            {"id": run_id},
        )
    # a chave está presente (com valor malformado), não ausente
    assert "requested_model_origin" in await _stored_config_json(components, table, run_id)

    with pytest.raises(ValidationError):
        await components.repository.get_run(run_id)


@pytest.mark.asyncio
async def test_a_stored_chosen_model_is_read_leniently():
    """Só a entrada nova é estrita: um valor já gravado continua legível."""
    components = await components_with(openai=ScriptedApiProvider("openai", [ok()]))
    run_id = (await run_direct(components, "gpt-x")).id
    async with components.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE direct_runs SET run_config_json = "
                "json_set(run_config_json, '$.requested_model', :model) WHERE id = :id"
            ),
            {"model": "gpt͏x", "id": run_id},
        )

    record = await components.repository.get_run(run_id)

    assert record.config.requested_model == "gpt͏x"


# ---------------------------------------------------------------------------
# Proveniência do request: direct_answer_v1, sem mudança
# ---------------------------------------------------------------------------


def _config(model, origin):
    return DirectRunConfig(
        question="Qual a capital?", provider="openai", requested_model=model, requested_model_origin=origin,
        max_output_tokens=64,
    )


def test_the_origin_alone_never_changes_the_request_or_its_digest():
    default = build_direct_request(_config("gpt-x", "configured_default"))
    override = build_direct_request(_config("gpt-x", "run_override"))

    assert default == override
    assert compute_request_digest(default) == compute_request_digest(override)
    assert build_request_provenance(DIRECT_ANSWER_CONTRACT_VERSION, default) == build_request_provenance(
        DIRECT_ANSWER_CONTRACT_VERSION, override
    )
    assert DIRECT_ANSWER_CONTRACT_VERSION == "direct_answer_v1"


def test_a_different_requested_model_changes_the_digest():
    x = build_direct_request(_config("gpt-x", "run_override"))
    y = build_direct_request(_config("gpt-y", "run_override"))

    assert compute_request_digest(x) != compute_request_digest(y)


@pytest.mark.asyncio
async def test_the_persisted_provenance_digests_the_exact_request_sent_with_the_chosen_model():
    provider = ScriptedApiProvider("openai", [ok()])
    components = await components_with(openai=provider)

    record = await components.repository.get_run((await run_direct(components, "gpt-explicit", max_tokens=64)).id)

    provenance = record.response.request_provenance
    assert provenance.contract_version == "direct_answer_v1"
    assert provenance.request_digest == compute_request_digest(provider.requests[0])
    assert provenance.request_digest == compute_request_digest(build_direct_request(record.config))
