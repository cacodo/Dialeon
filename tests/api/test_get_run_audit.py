from __future__ import annotations

from app.models.provider_models import TokenUsage
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from app.models.domain import ClaimAssessment
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import (
    full_council_run_result,
    model_response,
    now,
    quorum_failure_exception,
    run_config,
    very_rich_council_run_result,
    with_recomputed_reconciliation,
)


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _seed_success(components, result) -> str:
    await components.repository.save_success(result)
    return result.id


async def _seed_quorum_failure(components, exc) -> str:
    return await components.repository.save_quorum_failure(
        exc, run_config=run_config(), started_at=now(), failed_at=now()
    )


async def _seed_accepted(components, run_id: str) -> str:
    await components.repository.save_accepted(
        run_id,
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )
    return run_id


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


def test_get_run_audit_exposes_request_provenance_copied_never_regenerated():
    """A superfície de audit precisa expor request_provenance persistida
    tal como salva -- valor concreto pra uma resposta nova, null pra uma
    histórica -- nunca regenerada a partir dos builders atuais (ver
    seção 18 do contrato desta slice)."""
    result = full_council_run_result()
    concrete = RequestProvenance(
        contract_version="initial_response_v1",
        request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
    )
    mr1 = result.debate_result.initial_result.responses[0].model_copy(
        update={"request_provenance": concrete}
    )
    mr2 = result.debate_result.initial_result.responses[1].model_copy(
        update={"request_provenance": None}
    )
    initial = result.debate_result.initial_result.model_copy(update={"responses": [mr1, mr2]})
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    responses_by_id = {r["id"]: r for r in body["initial_round"]["responses"]}
    assert responses_by_id[mr1.id]["request_provenance"] == {
        "contract_version": "initial_response_v1",
        "request_digest": REQUEST_DIGEST_PREFIX + "a" * 64,
    }
    assert responses_by_id[mr2.id]["request_provenance"] is None

    # atributo persistido nas 3 outras famílias de attempt também
    # aparece na superfície de audit -- não só em model_responses.
    assert "request_provenance" in body["claim_processing_attempts"][0]
    assert "request_provenance" in body["judge_attempts"][0]
    assert "request_provenance" in body["editor_attempts"][0]


def test_get_run_audit_exposes_exact_distinct_request_provenance_for_all_five_families():
    """F3 (review de independência -- REPARO) -- prova de VALOR EXATO,
    não só presença, pras 5 famílias que carregam request_provenance,
    incluindo Source Analysis (ausente da cobertura anterior). Cada
    família recebe um `RequestProvenance` DISTINTO (contract_version E
    digest diferentes entre si) -- se o mapeamento persistência->audit
    cruzasse valores entre famílias por engano (ex.: Judge recebendo a
    provenance do Editor), esta asserção pegaria isso; uma provenance
    igual repetida em todo lugar não pegaria."""
    result = very_rich_council_run_result()

    mr_provenance = RequestProvenance(
        contract_version="initial_response_v1", request_digest=REQUEST_DIGEST_PREFIX + "1" * 64
    )
    proc_provenance = RequestProvenance(
        contract_version="claim_extraction_v1", request_digest=REQUEST_DIGEST_PREFIX + "2" * 64
    )
    source_provenance = RequestProvenance(
        contract_version="source_analysis_v1", request_digest=REQUEST_DIGEST_PREFIX + "3" * 64
    )
    judge_provenance = RequestProvenance(
        contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "4" * 64
    )
    editor_provenance = RequestProvenance(
        contract_version="editor_v1", request_digest=REQUEST_DIGEST_PREFIX + "5" * 64
    )

    mr1 = result.debate_result.initial_result.responses[0].model_copy(
        update={"request_provenance": mr_provenance}
    )
    responses = [mr1] + result.debate_result.initial_result.responses[1:]
    initial_result = result.debate_result.initial_result.model_copy(
        update={"responses": responses}
    )

    proc_attempt = result.debate_result.claim_processing_attempts[0].model_copy(
        update={"request_provenance": proc_provenance}
    )
    processing_attempts = [proc_attempt] + result.debate_result.claim_processing_attempts[1:]
    debate_result = result.debate_result.model_copy(
        update={"initial_result": initial_result, "claim_processing_attempts": processing_attempts}
    )

    source_attempt = result.source_analysis_result.attempts[0].model_copy(
        update={"request_provenance": source_provenance}
    )
    source_analysis_result = result.source_analysis_result.model_copy(
        update={"attempts": [source_attempt]}
    )

    judge_attempt = result.judge_result.attempts[0].model_copy(
        update={"request_provenance": judge_provenance}
    )
    judge_result = result.judge_result.model_copy(
        update={"attempts": [judge_attempt] + result.judge_result.attempts[1:]}
    )

    editor_attempt = result.editor_result.attempts[0].model_copy(
        update={"request_provenance": editor_provenance}
    )
    editor_result = result.editor_result.model_copy(
        update={"attempts": [editor_attempt] + result.editor_result.attempts[1:]}
    )

    result = result.model_copy(
        update={
            "debate_result": debate_result,
            "source_analysis_result": source_analysis_result,
            "judge_result": judge_result,
            "editor_result": editor_result,
        }
    )
    result = with_recomputed_reconciliation(result)

    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    assert resp.status_code == 200
    body = resp.json()

    def _public(provenance: RequestProvenance) -> dict:
        return {
            "contract_version": provenance.contract_version,
            "request_digest": provenance.request_digest,
        }

    responses_by_id = {r["id"]: r for r in body["initial_round"]["responses"]}
    assert responses_by_id[mr1.id]["request_provenance"] == _public(mr_provenance)

    proc_by_id = {a["id"]: a for a in body["claim_processing_attempts"]}
    assert proc_by_id[proc_attempt.id]["request_provenance"] == _public(proc_provenance)

    source_by_id = {a["id"]: a for a in body["source_analysis"]["attempts"]}
    assert source_by_id[source_attempt.id]["request_provenance"] == _public(source_provenance)

    judge_by_id = {a["id"]: a for a in body["judge_attempts"]}
    assert judge_by_id[judge_attempt.id]["request_provenance"] == _public(judge_provenance)

    editor_by_id = {a["id"]: a for a in body["editor_attempts"]}
    assert editor_by_id[editor_attempt.id]["request_provenance"] == _public(editor_provenance)

    # nenhum cross-copy entre famílias -- os 5 valores permanecem
    # distintos na superfície pública, na mesma ordem em que foram
    # atribuídos.
    all_digests = {
        responses_by_id[mr1.id]["request_provenance"]["request_digest"],
        proc_by_id[proc_attempt.id]["request_provenance"]["request_digest"],
        source_by_id[source_attempt.id]["request_provenance"]["request_digest"],
        judge_by_id[judge_attempt.id]["request_provenance"]["request_digest"],
        editor_by_id[editor_attempt.id]["request_provenance"]["request_digest"],
    }
    assert len(all_digests) == 5

    # cobertura histórica barata na mesma fixture/teste -- o SEGUNDO
    # attempt de cada família (nunca tocado acima) permanece com
    # request_provenance=None na superfície de audit, nunca inferido.
    assert proc_by_id[processing_attempts[1].id]["request_provenance"] is None
    assert judge_by_id[judge_result.attempts[1].id]["request_provenance"] is None
    assert editor_by_id[editor_result.attempts[1].id]["request_provenance"] is None


def test_get_run_audit_model_identity_source_serialized_for_all_three_states():
    """Provenance de identidade de modelo -- provider_reported,
    requested_fallback e None (histórico) precisam ser distinguíveis na
    superfície pública de audit, nunca colapsados entre si."""
    result = full_council_run_result()
    mr1 = result.debate_result.initial_result.responses[0]
    from app.models.provider_models import ModelIdentitySource

    diverged = mr1.model_copy(update={"model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK})
    mr_historical = result.debate_result.initial_result.responses[1].model_copy(
        update={"model_identity_source": None}
    )
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [diverged, mr_historical]}
    )
    # H -- ClaimSupport carrega sua PRÓPRIA provenance, não apenas
    # ModelResponse -- os 3 estados também precisam sobreviver aqui,
    # independentemente uns dos outros.
    original_claim = result.debate_result.claims[0]
    support_reported = original_claim.supporting_model_response_ids[0].model_copy(
        update={"model_identity_source": ModelIdentitySource.PROVIDER_REPORTED}
    )
    support_fallback = original_claim.supporting_model_response_ids[1].model_copy(
        update={"model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK}
    )
    updated_claim = original_claim.model_copy(
        update={"supporting_model_response_ids": [support_reported, support_fallback]}
    )
    debate = result.debate_result.model_copy(
        update={"initial_result": initial, "claims": [updated_claim]}
    )
    verdict = result.judge_result.verdict.model_copy(
        update={"judge_model_identity_source": ModelIdentitySource.PROVIDER_REPORTED}
    )
    judge = result.judge_result.model_copy(update={"verdict": verdict})
    final_answer = result.editor_result.final_answer.model_copy(
        update={"editor_model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    result = result.model_copy(
        update={"debate_result": debate, "judge_result": judge, "editor_result": editor}
    )

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    responses_by_id = {r["id"]: r for r in body["initial_round"]["responses"]}
    assert responses_by_id[diverged.id]["model_identity_source"] == "requested_fallback"
    assert responses_by_id[mr_historical.id]["model_identity_source"] is None
    # requested_model e model continuam campos distintos e ambos presentes
    # -- provenance não colapsa/oculta nenhum dos dois.
    assert responses_by_id[diverged.id]["requested_model"] == diverged.requested_model
    assert responses_by_id[diverged.id]["model"] == diverged.model
    assert body["judge_verdict"]["judge_model_identity_source"] == "provider_reported"
    assert body["final_answer"]["editor_model_identity_source"] == "requested_fallback"

    supports_by_response_id = {
        s["model_response_id"]: s
        for s in body["claims"][0]["supporting_model_response_ids"]
    }
    assert supports_by_response_id[support_reported.model_response_id]["model_identity_source"] == (
        "provider_reported"
    )
    assert supports_by_response_id[support_fallback.model_response_id]["model_identity_source"] == (
        "requested_fallback"
    )


def test_get_run_audit_claim_supporting_models_deduplicated():
    """Correção pós-revisão independente (HIGH 2) -- `ClaimPublic.supporting_models`
    (nova projeção pública, ver app/presentation/schemas.py) precisa
    chegar deduplicada por provider/model através da API real -- nunca
    igual a `len(supporting_model_response_ids)` quando o mesmo
    provider/model aparece mais de uma vez (ex.: mesmo modelo
    respondendo em Round 1 e Round 2, reconciliação cross-round)."""
    from app.models.domain import ClaimSupport

    result = full_council_run_result()
    base_claim = result.debate_result.claims[0]
    mr1 = result.debate_result.initial_result.responses[0]
    critique_mr = result.debate_result.critique_round.round_result.responses[0]
    # 2 ClaimSupport com model_response_id DIFERENTES (2 responses reais
    # -- Round 1 + Round 2, ver mr1/critique_mr) mas o MESMO provider/model
    # -- exatamente o cenário que support_scope_model_count/reconciliação
    # cross-round produz (mesmo modelo respondendo nas duas rodadas).
    duplicated_support_claim = base_claim.model_copy(
        update={
            "id": "duplicated-support-claim-id",
            "supporting_model_response_ids": [
                ClaimSupport(model_response_id=mr1.id, provider="openai", model="gpt-5.5"),
                ClaimSupport(model_response_id=critique_mr.id, provider="openai", model="gpt-5.5"),
            ],
        }
    )
    debate_result = result.debate_result.model_copy(
        update={"claims": [base_claim, duplicated_support_claim]}
    )
    # duplicated_support_claim é uma claim corrente nova (não superseded) --
    # reconciliação exige avaliação do Judge pra ela também.
    verdict = result.judge_result.verdict.model_copy(
        update={
            "claim_assessments": list(result.judge_result.verdict.claim_assessments)
            + [
                ClaimAssessment(
                    claim_id="duplicated-support-claim-id",
                    verdict="supported",
                    explanation="ok",
                )
            ]
        }
    )
    judge_result = result.judge_result.model_copy(update={"verdict": verdict})
    result = with_recomputed_reconciliation(
        result.model_copy(
            update={"debate_result": debate_result, "judge_result": judge_result}
        )
    )

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    body = resp.json()
    claim_json = next(c for c in body["claims"] if c["id"] == "duplicated-support-claim-id")
    assert len(claim_json["supporting_model_response_ids"]) == 2  # bruto, nunca deduplicado
    assert claim_json["supporting_models"] == ["openai/gpt-5.5"]  # deduplicado, 1 único


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
    verdict = result.judge_result.verdict.model_copy(
        update={
            "claim_assessments": list(result.judge_result.verdict.claim_assessments)
            + [
                ClaimAssessment(claim_id="second-claim", verdict="supported", explanation="ok"),
                ClaimAssessment(claim_id="third-claim", verdict="supported", explanation="ok"),
            ]
        }
    )
    judge_result = result.judge_result.model_copy(update={"verdict": verdict})
    result = with_recomputed_reconciliation(
        result.model_copy(update={"debate_result": debate, "judge_result": judge_result})
    )

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


def test_get_run_audit_running_never_invents_detail():
    """T02.4, teste I -- audit de um run "running" reusa exatamente o
    mesmo shape do detail (identidade + config aceita), NUNCA inventa
    claims/attempts/verdict que não existem/não foram persistidos."""
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        run_id = client.portal.call(_seed_accepted, app.state.components, "run-audit-running-1")
        resp = client.get(f"/runs/{run_id}/audit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert set(body.keys()) == {
        "status", "id", "started_at", "config", "provider_execution_policy",
        "default_model_authority_snapshot",
    }


def test_get_run_audit_not_found_returns_404():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        resp = client.get("/runs/id-que-nao-existe/audit")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"
