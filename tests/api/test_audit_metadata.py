from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, now, quorum_failure_exception, run_config


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _seed_success(components, result) -> str:
    await components.repository.save_success(result)
    return result.id


async def _seed_quorum_failure(components, exc, rc) -> str:
    return await components.repository.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )


def test_audit_debate_skipped_reason_preserved_when_critique_is_none():
    result = full_council_run_result()
    debate = result.debate_result.model_copy(
        update={"critique_round": None, "debate_skipped_reason": "insufficient_initial_quorum"}
    )
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    assert body["critique_round"] is None
    assert body["debate_outcome"]["skipped_reason"] == "insufficient_initial_quorum"


def test_audit_distinguishes_the_two_skip_reasons():
    result = full_council_run_result()
    debate = result.debate_result.model_copy(
        update={
            "critique_round": None,
            "debate_skipped_reason": "budget_exhausted_before_critique",
        }
    )
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    assert resp.json()["debate_outcome"]["skipped_reason"] == "budget_exhausted_before_critique"


def test_audit_judge_verdict_unavailable_reason_preserved():
    result = full_council_run_result()
    judge = result.judge_result.model_copy(
        update={"verdict": None, "verdict_unavailable_reason": "judge_transport_failed"}
    )
    final_answer = result.editor_result.final_answer.model_copy(
        update={
            "status": "deterministic_no_verdict",
            "editor_model": None,
            "based_on_verdict_id": None,
            "judge_confidence": None,
        }
    )
    editor = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "attempts": [],
            "fallback_reason": "judge_verdict_unavailable",
        }
    )
    result = result.model_copy(update={"judge_result": judge, "editor_result": editor})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    assert body["judge_verdict"] is None
    assert body["judge_outcome"]["verdict_unavailable_reason"] == "judge_transport_failed"


def test_audit_editor_fallback_reason_preserved_for_deterministic_final_answer():
    result = full_council_run_result()
    final_answer = result.editor_result.final_answer.model_copy(
        update={"status": "deterministic_from_verdict", "editor_model": None}
    )
    editor = result.editor_result.model_copy(
        update={"final_answer": final_answer, "fallback_reason": "editor_transport_failed"}
    )
    result = result.model_copy(update={"editor_result": editor})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    assert body["final_answer"]["status"] == "deterministic_from_verdict"
    assert body["editor_outcome"]["fallback_reason"] == "editor_transport_failed"


def test_audit_cumulative_budget_flags_preserved_per_phase():
    result = full_council_run_result()
    debate = result.debate_result.model_copy(update={"cumulative_budget_exceeded": True})
    judge = result.judge_result.model_copy(update={"cumulative_budget_exceeded": True})
    editor = result.editor_result.model_copy(update={"cumulative_budget_exceeded": False})
    result = result.model_copy(
        update={"debate_result": debate, "judge_result": judge, "editor_result": editor}
    )

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    assert body["debate_outcome"]["cumulative_budget_exceeded"] is True
    assert body["judge_outcome"]["cumulative_budget_exceeded"] is True
    assert body["editor_outcome"]["cumulative_budget_exceeded"] is False


def test_audit_run_config_includes_full_execution_relevant_fields():
    result = full_council_run_result(
        run_config=run_config(
            max_output_tokens_per_call=2048,
            max_output_tokens_grouping=4096,
            max_output_tokens_judge=6144,
            round_dispatch_timeout_seconds=42.0,
        )
    )

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    config = resp.json()["config"]
    assert config["max_output_tokens_per_call"] == 2048
    # Etapa 17A.2 -- tetos próprios de agrupamento/Judge, materialmente
    # diferentes do geral, precisam ficar auditáveis também.
    assert config["max_output_tokens_grouping"] == 4096
    assert config["max_output_tokens_judge"] == 6144
    assert config["round_dispatch_timeout_seconds"] == 42.0
    assert config["quorum"]["min_for_debate"] == result.run_config.quorum.min_for_debate
    assert config["quorum"]["min_to_return"] == result.run_config.quorum.min_to_return


def test_run_detail_also_includes_full_config():
    result = full_council_run_result(run_config=run_config(max_output_tokens_per_call=999))
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    assert resp.json()["config"]["max_output_tokens_per_call"] == 999


def test_run_config_values_come_from_persisted_config_not_current_settings():
    settings = _settings()
    assert settings.default_max_output_tokens_per_call != 12345
    result = full_council_run_result(run_config=run_config(max_output_tokens_per_call=12345))

    app = create_app(settings=settings, components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    assert resp.json()["config"]["max_output_tokens_per_call"] == 12345


def test_quorum_failure_audit_has_full_historical_config():
    exc = quorum_failure_exception()
    rc = run_config(max_output_tokens_per_call=777, round_dispatch_timeout_seconds=13.0)

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_quorum_failure, app.state.components, exc, rc)
        resp = client.get(f"/runs/{run_id}/audit")

    config = resp.json()["config"]
    assert config["max_output_tokens_per_call"] == 777
    assert config["round_dispatch_timeout_seconds"] == 13.0
    assert config["quorum"]["min_for_debate"] == rc.quorum.min_for_debate
    assert config["quorum"]["min_to_return"] == rc.quorum.min_to_return


def test_quorum_failure_audit_metadata_correct():
    exc = quorum_failure_exception()
    rc = run_config()

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_quorum_failure, app.state.components, exc, rc)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    assert body["status"] == "insufficient_quorum"
    assert body["successful_count"] == exc.successful_count
    assert body["total_providers"] == exc.total_providers
    assert body["min_to_return"] == exc.min_to_return


# ---------------------------------------------------------------------------
# Etapa 13 (T03.A) — requested_model exposto no contrato HTTP /audit,
# em todo lugar que já expõe `model` (ModelResponse e os 3 tipos de
# Attempt), distinto e nunca igual por acidente à identidade reportada.
# ---------------------------------------------------------------------------


def test_audit_exposes_requested_model_distinct_from_reported_model():
    result = full_council_run_result()

    mr1 = result.debate_result.initial_result.responses[0]
    diverged_mr = mr1.model_copy(
        update={"requested_model": "gpt-5.5-latest", "model": "gpt-5.5-2025-06-15"}
    )
    responses = [diverged_mr] + result.debate_result.initial_result.responses[1:]
    initial = result.debate_result.initial_result.model_copy(update={"responses": responses})

    proc_attempt = result.debate_result.claim_processing_attempts[0].model_copy(
        update={"requested_model": "claude-sonnet-5-latest", "model": "claude-sonnet-5-20250601"}
    )
    debate = result.debate_result.model_copy(
        update={"initial_result": initial, "claim_processing_attempts": [proc_attempt]}
    )

    judge_attempt = result.judge_result.attempts[0].model_copy(
        update={"requested_model": "claude-sonnet-5-latest", "model": "claude-sonnet-5-20250601"}
    )
    judge = result.judge_result.model_copy(update={"attempts": [judge_attempt]})

    editor_attempt = result.editor_result.attempts[0].model_copy(
        update={"requested_model": "claude-sonnet-5-latest", "model": "claude-sonnet-5-20250601"}
    )
    editor = result.editor_result.model_copy(update={"attempts": [editor_attempt]})

    result = result.model_copy(
        update={"debate_result": debate, "judge_result": judge, "editor_result": editor}
    )

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()

    reloaded_mr = next(
        r for r in body["initial_round"]["responses"] if r["id"] == diverged_mr.id
    )
    assert reloaded_mr["requested_model"] == "gpt-5.5-latest"
    assert reloaded_mr["model"] == "gpt-5.5-2025-06-15"

    reloaded_proc = body["claim_processing_attempts"][0]
    assert reloaded_proc["requested_model"] == "claude-sonnet-5-latest"
    assert reloaded_proc["model"] == "claude-sonnet-5-20250601"

    reloaded_judge = body["judge_attempts"][0]
    assert reloaded_judge["requested_model"] == "claude-sonnet-5-latest"
    assert reloaded_judge["model"] == "claude-sonnet-5-20250601"

    reloaded_editor = body["editor_attempts"][0]
    assert reloaded_editor["requested_model"] == "claude-sonnet-5-latest"
    assert reloaded_editor["model"] == "claude-sonnet-5-20250601"


# ---------------------------------------------------------------------------
# Etapa 15 — numeric_verification_attempts exposto no contrato HTTP /audit
# ---------------------------------------------------------------------------


def test_audit_exposes_deterministic_verification_attempts():
    from app.debate.numeric_verification import build_verification_attempt

    result = full_council_run_result()
    claim = result.debate_result.claims[0]
    attempt = build_verification_attempt(
        claim.id, {"left": "2", "operator": "+", "right": "2", "asserted_result": "4"}
    )
    debate = result.debate_result.model_copy(update={"numeric_verification_attempts": [attempt]})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    reloaded = body["numeric_verification_attempts"][0]
    assert reloaded["claim_id"] == claim.id
    assert reloaded["state"] == "supports"
    assert reloaded["assertion"]["left"] == "2"
    assert reloaded["computed_result"] == "4"
