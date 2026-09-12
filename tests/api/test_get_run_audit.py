from __future__ import annotations

from app.models.provider_models import TokenUsage
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, model_response, now, quorum_failure_exception, run_config


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _seed_success(components, result) -> str:
    await components.repository.save_success(result)
    return result.id


async def _seed_quorum_failure(components, exc) -> str:
    return await components.repository.save_quorum_failure(
        exc, run_config=run_config(), started_at=now(), failed_at=now()
    )


def test_get_run_audit_completed_full_detail():
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["initial_round"]["responses"]) == 2
    assert body["critique_round"] is not None
    assert len(body["claims"]) == 1
    assert len(body["claim_processing_attempts"]) == 1
    assert body["judge_verdict"] is not None
    assert len(body["judge_attempts"]) == 1
    assert len(body["editor_attempts"]) == 1
    assert body["final_answer"]["answer_text"] == result.final_answer.answer_text


def test_get_run_audit_raw_response_text_preserved():
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    original_texts = {r.response_text for r in result.debate_result.initial_result.responses}
    audited_texts = {r["response_text"] for r in resp.json()["initial_round"]["responses"]}
    assert audited_texts == original_texts


def test_get_run_audit_pricing_provenance_preserved():
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    response_audit = resp.json()["initial_round"]["responses"][0]
    original = result.debate_result.initial_result.responses[0]
    assert response_audit["pricing_provenance"]["source_id"] == original.pricing_provenance.source_id
    assert response_audit["pricing_provenance"]["tier"] == original.pricing_provenance.tier


def test_get_run_audit_token_usage_none_none_preserved():
    result = full_council_run_result()
    mr_unknown_usage = model_response(
        "gemini",
        model="gemini-3.7-flash",
        usage=TokenUsage(input_tokens=None, output_tokens=None),
        cost_usd=None,
        pricing_provenance=None,
    )
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, mr_unknown_usage]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    audited = next(
        r
        for r in resp.json()["initial_round"]["responses"]
        if r["id"] == mr_unknown_usage.id
    )
    assert audited["usage"] is not None
    assert audited["usage"]["input_tokens"] is None
    assert audited["usage"]["output_tokens"] is None


def test_get_run_audit_claims_order_preserved():
    result = full_council_run_result()
    base_claim = result.debate_result.claims[0]
    second_claim = base_claim.model_copy(update={"id": "second-claim", "text": "Segunda claim."})
    third_claim = base_claim.model_copy(update={"id": "third-claim", "text": "Terceira claim."})
    debate = result.debate_result.model_copy(
        update={"claims": [base_claim, second_claim, third_claim]}
    )
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    audited_ids = [c["id"] for c in resp.json()["claims"]]
    assert audited_ids == [base_claim.id, "second-claim", "third-claim"]


def test_get_run_audit_quorum_failure_full_detail():
    exc = quorum_failure_exception()
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_quorum_failure, app.state.components, exc)
        resp = client.get(f"/runs/{run_id}/audit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "insufficient_quorum"
    assert len(body["round_result"]["responses"]) == len(exc.round_result.responses)
    assert body["round_result"]["accounting"]["has_unknown_accounting_components"] == (
        exc.round_result.has_unknown_accounting_components
    )


def test_get_run_audit_not_found_returns_404():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        resp = client.get("/runs/id-que-nao-existe/audit")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"
