"""Council Local Execution Readiness & Admission V1 -- avaliador único e
política de admissão estrita (funções puras, sem provider nem banco)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.council.readiness import (
    CouncilAdmission,
    CouncilAdmissionRequest,
    CouncilExecutionDependencies,
    CouncilReadiness,
    acknowledgement_mismatch,
    evaluate_council_readiness,
    strict_admission_blockers,
)
from tests.storage.fixtures import run_config

MODELS = {
    "openai": "gpt-configured",
    "gemini": "gemini-configured",
    "anthropic": "claude-configured",
    "mistral": "mistral-configured",
}


def _deps(
    participants=("openai", "gemini"),
    *,
    claim="anthropic",
    judge="anthropic",
    editor="anthropic",
    source_analyzer="anthropic",
    source=False,
) -> CouncilExecutionDependencies:
    return CouncilExecutionDependencies(
        enabled_providers=participants,
        claim_processor_provider=claim,
        judge_provider=judge,
        editor_provider=editor,
        source_analyzer_provider=source_analyzer,
        source_supplied=source,
    )


def _evaluate(deps, **states) -> CouncilReadiness:
    local = {name: "met" for name in MODELS} | states
    return evaluate_council_readiness(deps, local_prerequisites=local, configured_default_models=MODELS)


def _by_role(readiness):
    return {(d.role, d.provider): d for d in readiness.dependencies}


# ---------------------------------------------------------------------------
# Participantes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["met", "missing", "unknown"])
def test_participant_state_is_reported_verbatim(state):
    readiness = _evaluate(_deps(), openai=state)

    participant = _by_role(readiness)[("participant", "openai")]
    assert participant.local_prerequisite == state
    assert participant.applicability == "selected"
    assert readiness.summary == {"met": "all_met", "missing": "some_missing", "unknown": "some_unknown"}[state]


def test_participants_come_first_in_selection_order_then_internal_roles_in_pipeline_order():
    readiness = _evaluate(_deps(("gemini", "openai")))

    assert [(d.role, d.provider) for d in readiness.dependencies] == [
        ("participant", "gemini"),
        ("participant", "openai"),
        ("claim_extraction", "anthropic"),
        ("source_analysis", "anthropic"),
        ("judge", "anthropic"),
        ("editor", "anthropic"),
        ("semantic_review", "anthropic"),
    ]


# ---------------------------------------------------------------------------
# Papéis internos
# ---------------------------------------------------------------------------


def test_each_internal_role_resolves_its_own_configured_provider_and_model():
    deps = _deps(claim="openai", judge="gemini", editor="mistral", source_analyzer="anthropic", source=True)

    roles = {d.role: d for d in _evaluate(deps).dependencies if d.role != "participant"}

    assert {role: (d.provider, d.configured_default_model) for role, d in roles.items()} == {
        "claim_extraction": ("openai", "gpt-configured"),
        "source_analysis": ("anthropic", "claude-configured"),
        "judge": ("gemini", "gemini-configured"),
        "editor": ("mistral", "mistral-configured"),
        # a revisão semântica da redação usa o provider do Judge no pipeline atual
        "semantic_review": ("gemini", "gemini-configured"),
    }
    assert {d.applicability for d in roles.values()} == {"potential"}


@pytest.mark.parametrize(
    "role, field",
    [
        ("claim_extraction", "claim"),
        ("judge", "judge"),
        ("editor", "editor"),
        ("semantic_review", "judge"),
    ],
)
def test_missing_internal_role_is_known_degradation_and_blocks_strict(role, field):
    deps = _deps(**{field: "mistral"})

    readiness = _evaluate(deps, mistral="missing")

    assert role in {d.role for d in readiness.known_missing}
    assert readiness.summary == "some_missing"
    assert strict_admission_blockers(readiness)


def test_shared_provider_is_listed_once_per_role_with_the_same_state():
    readiness = _evaluate(_deps(), anthropic="missing")

    shared = [d for d in readiness.dependencies if d.provider == "anthropic"]
    assert [d.role for d in shared] == [
        "claim_extraction",
        "source_analysis",
        "judge",
        "editor",
        "semantic_review",
    ]
    assert {d.local_prerequisite for d in shared} == {"missing"}
    # a fonte está ausente: a análise de fonte continua listada, mas não conta
    assert [d.role for d in readiness.known_missing] == [
        "claim_extraction",
        "judge",
        "editor",
        "semantic_review",
    ]


def test_a_participant_can_also_be_an_internal_role():
    readiness = _evaluate(_deps(("openai", "anthropic")), anthropic="unknown")

    assert [(d.role, d.local_prerequisite) for d in readiness.dependencies if d.provider == "anthropic"] == [
        ("participant", "unknown"),
        ("claim_extraction", "unknown"),
        ("source_analysis", "unknown"),
        ("judge", "unknown"),
        ("editor", "unknown"),
        ("semantic_review", "unknown"),
    ]


# ---------------------------------------------------------------------------
# Etapas condicionais: fonte
# ---------------------------------------------------------------------------


def test_source_analysis_is_not_applicable_without_source_and_never_blocks():
    readiness = _evaluate(_deps(source_analyzer="mistral", source=False), mistral="missing")

    source = _by_role(readiness)[("source_analysis", "mistral")]
    assert source.applicability == "not_applicable"
    assert source.local_prerequisite == "missing"  # configurado e registrado, mesmo fora do caminho
    assert readiness.summary == "all_met"
    assert strict_admission_blockers(readiness) == ()


def test_source_analysis_is_potential_with_source_and_its_absence_blocks_strict():
    readiness = _evaluate(_deps(source_analyzer="mistral", source=True), mistral="missing")

    source = _by_role(readiness)[("source_analysis", "mistral")]
    assert source.applicability == "potential"
    assert readiness.summary == "some_missing"
    assert [d.role for d in strict_admission_blockers(readiness)] == ["source_analysis"]


def test_from_run_config_follows_the_normalized_source():
    assert CouncilExecutionDependencies.from_run_config(run_config(source_text=None)).source_supplied is False
    assert CouncilExecutionDependencies.from_run_config(run_config(source_text="   ")).source_supplied is False
    assert CouncilExecutionDependencies.from_run_config(run_config(source_text="texto")).source_supplied is True


def test_from_run_config_covers_exactly_the_run_config_provider_authorities():
    rc = run_config(enabled_providers=["openai", "gemini"])

    assert CouncilExecutionDependencies.from_run_config(rc).all_providers == rc.all_provider_authorities


# ---------------------------------------------------------------------------
# UNKNOWN nunca é MISSING nem MET
# ---------------------------------------------------------------------------


def test_unknown_is_uncertainty_not_failure_and_strict_admits_it():
    readiness = _evaluate(_deps(), anthropic="unknown")

    assert readiness.summary == "some_unknown"
    assert readiness.known_missing == ()
    assert {d.role for d in readiness.unknown} == {"claim_extraction", "judge", "editor", "semantic_review"}
    assert strict_admission_blockers(readiness) == ()


def test_missing_takes_precedence_over_unknown_in_the_summary():
    readiness = _evaluate(_deps(), openai="unknown", anthropic="missing")

    assert readiness.summary == "some_missing"
    assert {d.provider for d in readiness.known_missing} == {"anthropic"}


def test_all_met_summary_and_no_blockers():
    readiness = _evaluate(_deps(source=True))

    assert readiness.summary == "all_met"
    assert strict_admission_blockers(readiness) == ()


# ---------------------------------------------------------------------------
# Fatos obrigatórios e coerência dos modelos
# ---------------------------------------------------------------------------


def test_evaluator_never_invents_a_state_for_a_provider_without_local_facts():
    with pytest.raises(ValueError, match="mistral"):
        evaluate_council_readiness(
            _deps(judge="mistral"),
            local_prerequisites={"openai": "met", "gemini": "met", "anthropic": "met"},
            configured_default_models=MODELS,
        )


def test_participant_applicability_is_structural():
    readiness = _evaluate(_deps())
    data = readiness.model_dump()
    data["dependencies"][0]["applicability"] = "potential"

    with pytest.raises(ValidationError):
        CouncilReadiness.model_validate(data)


def test_admission_request_rejects_strict_with_acknowledgement():
    fingerprint = _evaluate(_deps(), anthropic="missing").known_degradation_fingerprint
    with pytest.raises(ValidationError):
        CouncilAdmissionRequest(
            mode="strict", acknowledge_known_degradation=True, acknowledged_degradation_fingerprint=fingerprint
        )
    with pytest.raises(ValidationError):
        CouncilAdmissionRequest(mode="strict", acknowledged_degradation_fingerprint=fingerprint)
    assert CouncilAdmissionRequest() == CouncilAdmissionRequest(mode="standard", acknowledge_known_degradation=False)


def test_acknowledgement_and_its_identity_always_travel_together():
    fingerprint = _evaluate(_deps(), anthropic="missing").known_degradation_fingerprint
    with pytest.raises(ValidationError, match="identidade"):
        CouncilAdmissionRequest(acknowledge_known_degradation=True)  # reconhecimento sem o que foi visto
    with pytest.raises(ValidationError):
        CouncilAdmissionRequest(acknowledged_degradation_fingerprint=fingerprint)  # identidade sem reconhecimento
    with pytest.raises(ValidationError):
        CouncilAdmissionRequest(acknowledge_known_degradation=True, acknowledged_degradation_fingerprint="sha256:xyz")
    CouncilAdmissionRequest(acknowledge_known_degradation=True, acknowledged_degradation_fingerprint=fingerprint)


# ---------------------------------------------------------------------------
# Identidade canônica da degradação conhecida
# ---------------------------------------------------------------------------


def test_fingerprint_is_none_without_known_missing_degradation():
    assert _evaluate(_deps()).known_degradation_fingerprint is None
    assert _evaluate(_deps(), anthropic="unknown").known_degradation_fingerprint is None
    # ausência fora do caminho pedido (análise da fonte sem fonte) não é degradação conhecida
    assert _evaluate(_deps(source_analyzer="mistral"), mistral="missing").known_degradation_fingerprint is None


def test_fingerprint_is_canonical_and_ordering_stable():
    a = _evaluate(_deps(("openai", "gemini")), openai="missing", anthropic="missing")
    b = _evaluate(_deps(("gemini", "openai")), openai="missing", anthropic="missing")

    assert a.dependencies != b.dependencies  # ordem diferente
    assert a.known_degradation_fingerprint == b.known_degradation_fingerprint
    assert a.known_degradation_fingerprint.startswith("sha256:") and len(a.known_degradation_fingerprint) == 71


def test_fingerprint_ignores_met_and_unknown_changes():
    base = _evaluate(_deps(), anthropic="missing")

    assert _evaluate(_deps(), anthropic="missing", openai="unknown").known_degradation_fingerprint == (
        base.known_degradation_fingerprint
    )
    assert _evaluate(_deps(), anthropic="missing", gemini="unknown").known_degradation_fingerprint == (
        base.known_degradation_fingerprint
    )


@pytest.mark.parametrize(
    "changed",
    [
        # outro provider ausente (conjunto B em vez de A)
        lambda: _evaluate(_deps(), gemini="missing"),
        # mais uma ausência além de A
        lambda: _evaluate(_deps(), anthropic="missing", openai="missing"),
        # mesmos estados, mas um papel a mais (fonte fornecida torna a análise aplicável)
        lambda: _evaluate(_deps(source=True), anthropic="missing"),
        # mesmos papéis/provider, outro modelo configurado
        lambda: evaluate_council_readiness(
            _deps(),
            local_prerequisites={"openai": "met", "gemini": "met", "anthropic": "missing"},
            configured_default_models=MODELS | {"anthropic": "claude-reconfigured"},
        ),
    ],
)
def test_fingerprint_changes_when_material_missing_facts_change(changed):
    base = _evaluate(_deps(), anthropic="missing")

    assert changed().known_degradation_fingerprint not in (None, base.known_degradation_fingerprint)


def test_acknowledgement_mismatch_rule():
    a = _evaluate(_deps(), anthropic="missing")
    b = _evaluate(_deps(), gemini="missing")
    ack_a = CouncilAdmissionRequest(
        acknowledge_known_degradation=True, acknowledged_degradation_fingerprint=a.known_degradation_fingerprint
    )

    assert acknowledgement_mismatch(ack_a, a) is False
    assert acknowledgement_mismatch(ack_a, b) is True
    assert acknowledgement_mismatch(ack_a, _evaluate(_deps())) is True  # nada ausente agora: nada passa adiante
    assert acknowledgement_mismatch(CouncilAdmissionRequest(), b) is False  # sem reconhecimento: aceite de sempre


# ---------------------------------------------------------------------------
# Fatos de aceite persistidos
# ---------------------------------------------------------------------------


def test_persisted_admission_is_coherent_with_the_strict_policy():
    degraded = _evaluate(_deps(), anthropic="missing")
    fingerprint = degraded.known_degradation_fingerprint

    # aceite padrão com degradação conhecida: válido, com ou sem reconhecimento
    CouncilAdmission(
        mode="standard",
        known_degradation_acknowledged=True,
        acknowledged_degradation_fingerprint=fingerprint,
        readiness=degraded,
    )
    CouncilAdmission(mode="standard", known_degradation_acknowledged=False, readiness=degraded)
    # um aceite estrito nunca pode ter ausência conhecida no caminho pedido
    with pytest.raises(ValidationError):
        CouncilAdmission(mode="strict", known_degradation_acknowledged=False, readiness=degraded)


def test_persisted_acknowledgement_must_be_of_the_acceptance_time_degradation():
    degraded = _evaluate(_deps(), anthropic="missing")
    other = _evaluate(_deps(), gemini="missing").known_degradation_fingerprint

    with pytest.raises(ValidationError):  # reconhecimento de OUTRA degradação
        CouncilAdmission(
            mode="standard", known_degradation_acknowledged=True, acknowledged_degradation_fingerprint=other, readiness=degraded
        )
    with pytest.raises(ValidationError):  # reconhecimento sem identidade (v2)
        CouncilAdmission(mode="standard", known_degradation_acknowledged=True, readiness=degraded)
    with pytest.raises(ValidationError):  # reconhecimento sem degradação conhecida
        CouncilAdmission(
            mode="standard",
            known_degradation_acknowledged=True,
            acknowledged_degradation_fingerprint=other,
            readiness=_evaluate(_deps()),
        )


def test_development_v1_admission_without_identity_stays_readable_as_not_captured():
    degraded = _evaluate(_deps(), anthropic="missing")
    v1 = {
        "contract_version": "council_admission_v1",
        "mode": "standard",
        "known_degradation_acknowledged": True,
        "readiness": degraded.model_dump(mode="json"),
    }

    admission = CouncilAdmission.model_validate(v1)

    assert admission.known_degradation_acknowledged is True
    assert admission.acknowledged_degradation_fingerprint is None  # não capturada, nunca inventada
    with pytest.raises(ValidationError):
        CouncilAdmission.model_validate(v1 | {"acknowledged_degradation_fingerprint": degraded.known_degradation_fingerprint})


def test_readiness_round_trips_through_json():
    readiness = _evaluate(_deps(source=True), openai="unknown", anthropic="missing")
    admission = CouncilAdmission(
        mode="standard",
        known_degradation_acknowledged=True,
        acknowledged_degradation_fingerprint=readiness.known_degradation_fingerprint,
        readiness=readiness,
    )

    assert admission.contract_version == "council_admission_v2"
    assert CouncilAdmission.model_validate(admission.model_dump(mode="json")) == admission
