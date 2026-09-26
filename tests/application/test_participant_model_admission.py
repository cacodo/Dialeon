"""Council Accepted Effective Participant Model Choice V1 -- boundary
autoritativa (`CouncilExecutionService`): a mesma resolução na prévia e no
aceite, rejeição de entrada inválida sem efeito colateral, mapa congelado no
aceite, identidade de degradação que distingue o modelo planejado, e leitura
honesta das avaliações v1 (v1.4.0)."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.application.errors import (
    CouncilDegradationChangedError,
    InvalidParticipantModelOverrideError,
)
from app.council.readiness import (
    CouncilAdmission,
    CouncilAdmissionRequest,
    CouncilDependencyReadiness,
    CouncilExecutionDependencies,
    CouncilReadiness,
)
from app.orchestrator.participant_models import resolve_participant_models
from app.storage.records import CompletedRunRecord
from tests.application.test_council_admission import (
    STRICT,
    _config,
    _harness,
    _no_provider_was_called,
    _providers,
)


def _preview(service, config, overrides=None):
    return service.preview_readiness(
        CouncilExecutionDependencies.from_run_config(config), participant_model_overrides=overrides
    )


def _participant(readiness, provider):
    return next(d for d in readiness.dependencies if d.role == "participant" and d.provider == provider)


# ---------------------------------------------------------------------------
# Resolução no aceite e congelamento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_freezes_override_and_defaults_for_every_participant():
    h = await _harness(_providers())

    result = await h.service.run(_config(), participant_model_overrides={"openai": "gpt-explicit"})
    record = await h.repository.get_run(result.id)

    assert isinstance(record, CompletedRunRecord)
    assert [(c.provider, c.requested_model, c.origin) for c in record.council_run_result.run_config.participant_models] == [
        ("openai", "gpt-explicit", "run_override"),
        ("anthropic", "anthropic-configured", "configured_default"),
    ]
    # o runner recebeu o RunConfig ACEITO (com o mapa), não o de entrada
    assert h.debate_engine.calls[0].participant_models == record.council_run_result.run_config.participant_models
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_a_later_default_change_never_rewrites_an_accepted_run():
    providers = _providers()
    h = await _harness(providers)
    first = await h.service.run(_config())

    # o deployment muda o padrão depois do aceite (mesmos objetos de provider)
    providers["anthropic"]._default_model_name = "anthropic-reconfigured"
    first_record = await h.repository.get_run(first.id)
    later = await _harness(providers)  # outro banco: o fake devolve sempre o mesmo resultado
    second = await later.service.run(_config())
    second_record = await later.repository.get_run(second.id)

    assert first_record.council_run_result.run_config.requested_model_for("anthropic") == "anthropic-configured"
    assert second_record.council_run_result.run_config.requested_model_for("anthropic") == "anthropic-reconfigured"
    await h.engine.dispose()
    await later.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"gemini": "gemini-x"}, "não selecionado"),  # gemini não é participante aqui
        ({"openai": ""}, "vazio"),
        ({"openai": "gpt x"}, "espaço"),
        ({"openai": "g" * 257}, "máximo"),
        ({"openai": "gpt\u202ex"}, "U\\+202E"),  # bidi override (Cf)
        ({"openai": "gpt\x9bx"}, "U\\+009B"),  # C1 (Cc)
        ({"openai": "gpt\u034fx"}, "U\\+034F"),  # COMBINING GRAPHEME JOINER (Mn, ignorável)
    ],
)
async def test_invalid_overrides_are_rejected_before_any_side_effect(overrides, fragment):
    h = await _harness(_providers(anthropic="missing"))

    with pytest.raises(InvalidParticipantModelOverrideError, match=fragment):
        await h.service.run(_config(), admission=STRICT, participant_model_overrides=overrides)
    with pytest.raises(InvalidParticipantModelOverrideError, match=fragment):
        _preview(h.service, _config(), overrides)

    assert await h.repository.list_runs() == []
    assert h.debate_engine.calls == []
    assert _no_provider_was_called(h.providers)
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_a_pre_resolved_run_config_is_refused_the_map_is_resolved_only_at_acceptance():
    h = await _harness(_providers())
    pre_resolved = _config().with_participant_models(
        resolve_participant_models(("openai", "anthropic"), None, {"openai": "a", "anthropic": "b"})
    )

    with pytest.raises(ValueError, match="resolvido no aceite"):
        await h.service.run(pre_resolved)
    assert await h.repository.list_runs() == []
    await h.engine.dispose()


# ---------------------------------------------------------------------------
# Prontidão: mesma resolução, modelo planejado só como pedido local
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [None, {"openai": "gpt-explicit"}, {"openai": "x", "anthropic": "y"}])
async def test_preview_and_acceptance_resolve_the_same_planned_models(overrides):
    h = await _harness(_providers(gemini="missing"))

    preview = _preview(h.service, _config(), overrides)
    result = await h.service.run(_config(), participant_model_overrides=overrides)
    record = await h.repository.get_run(result.id)

    assert record.council_admission.readiness == preview
    assert preview.contract_version == "council_local_readiness_v2"
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_readiness_shows_configured_default_and_planned_override_separately():
    h = await _harness(_providers())

    readiness = _preview(h.service, _config(), {"openai": "gpt-explicit"})

    openai = _participant(readiness, "openai")
    assert (openai.configured_default_model, openai.planned_model, openai.planned_model_origin) == (
        "openai-configured",
        "gpt-explicit",
        "run_override",
    )
    anthropic = _participant(readiness, "anthropic")
    assert (anthropic.planned_model, anthropic.planned_model_origin) == ("anthropic-configured", "configured_default")
    # papéis internos: nunca têm modelo planejado (a escolha não os atinge)
    assert {d.planned_model for d in readiness.dependencies if d.role != "participant"} == {None}
    await h.engine.dispose()


# ---------------------------------------------------------------------------
# Identidade da degradação: o modelo planejado de um participante ausente é material
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledging_a_missing_participant_with_one_model_never_covers_another_model():
    h = await _harness(_providers(anthropic="missing"))
    shown = _preview(h.service, _config(), {"anthropic": "claude-x"})
    ack = CouncilAdmissionRequest(
        acknowledge_known_degradation=True, acknowledged_degradation_fingerprint=shown.known_degradation_fingerprint
    )

    other = _preview(h.service, _config(), {"anthropic": "claude-y"})
    assert other.known_degradation_fingerprint != shown.known_degradation_fingerprint
    with pytest.raises(CouncilDegradationChangedError):
        await h.service.run(_config(), admission=ack, participant_model_overrides={"anthropic": "claude-y"})
    assert await h.repository.list_runs() == []

    result = await h.service.run(_config(), admission=ack, participant_model_overrides={"anthropic": "claude-x"})
    record = await h.repository.get_run(result.id)
    assert record.council_admission.acknowledged_degradation_fingerprint == shown.known_degradation_fingerprint
    await h.engine.dispose()


@pytest.mark.asyncio
async def test_a_missing_internal_role_degradation_does_not_depend_on_participant_models():
    h = await _harness(_providers(gemini="missing"))  # gemini = papéis internos

    a = _preview(h.service, _config(), None)
    b = _preview(h.service, _config(), {"openai": "gpt-other"})

    assert a.known_degradation_fingerprint == b.known_degradation_fingerprint
    await h.engine.dispose()


def test_v1_assessments_keep_the_v140_fingerprint_domain_and_stay_readable():
    """Avaliações gravadas pela v1.4.0 (v1, sem modelo planejado) continuam
    com a identidade calculada pelo algoritmo da v1.4.0 -- um reconhecimento
    gravado então continua válido na leitura."""
    deps = (
        CouncilDependencyReadiness(
            role="participant", provider="openai", configured_default_model="gpt-d",
            local_prerequisite="met", applicability="selected",
        ),
        CouncilDependencyReadiness(
            role="judge", provider="anthropic", configured_default_model="claude-d",
            local_prerequisite="missing", applicability="potential",
        ),
    )
    v1 = CouncilReadiness(contract_version="council_local_readiness_v1", dependencies=deps)
    canonical = json.dumps(
        {"contract": "council_known_degradation_v1", "missing": [["judge", "anthropic", "claude-d"]]},
        separators=(",", ":"),
        ensure_ascii=False,
    )

    assert v1.known_degradation_fingerprint == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    admission = CouncilAdmission(
        mode="standard",
        known_degradation_acknowledged=True,
        acknowledged_degradation_fingerprint=v1.known_degradation_fingerprint,
        readiness=v1,
    )
    assert CouncilAdmission.model_validate(admission.model_dump(mode="json")) == admission
    # v2 exige o modelo planejado de cada participante; v1 nunca o tem
    with pytest.raises(ValueError):
        CouncilReadiness(contract_version="council_local_readiness_v2", dependencies=deps)
