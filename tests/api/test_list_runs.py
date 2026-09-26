from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, now, quorum_failure_exception, run_config


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _seed(components, n_completed: int, n_failures: int) -> None:
    for _ in range(n_completed):
        await components.repository.save_success(full_council_run_result())
    for _ in range(n_failures):
        exc = quorum_failure_exception()
        await components.repository.save_quorum_failure(
            exc, run_config=run_config(), started_at=now(), failed_at=now()
        )


def test_list_runs_empty():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        resp = client.get("/runs")

    assert resp.status_code == 200
    body = resp.json()
    assert body["runs"] == []
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_runs_returns_both_kinds_sorted_started_at_desc():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        client.portal.call(_seed, app.state.components, 2, 1)
        resp = client.get("/runs")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["runs"]) == 3
    statuses = {r["status"] for r in body["runs"]}
    assert statuses == {"completed", "insufficient_quorum"}
    started_ats = [r["started_at"] for r in body["runs"]]
    assert started_ats == sorted(started_ats, reverse=True)


def test_list_runs_includes_running_and_failed():
    """T02.4, teste I -- list/detail derivam exclusivamente dos fatos
    persistidos canônicos: um run "running" (aceito, sem desfecho) e um
    "failed" (exceção inesperada) aparecem em GET /runs igual a
    completed/insufficient_quorum, com ended_at=null pro running."""

    async def seed_lifecycle(components) -> None:
        await components.repository.save_accepted(
            "run-list-running",
            run_config=run_config(),
            started_at=now(),
            provider_execution_policy=components.provider_execution_policy,
        )
        await components.repository.save_accepted(
            "run-list-failed",
            run_config=run_config(),
            started_at=now(),
            provider_execution_policy=components.provider_execution_policy,
        )
        await components.repository.save_unexpected_failure(
            "run-list-failed",
            failed_at=now(),
            failure_classification="WeirdBug",
            failure_message="Erro interno inesperado durante a execução.",
        )

    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        client.portal.call(seed_lifecycle, app.state.components)
        resp = client.get("/runs")

    body = resp.json()
    by_id = {r["id"]: r for r in body["runs"]}
    assert by_id["run-list-running"]["status"] == "running"
    assert by_id["run-list-running"]["ended_at"] is None
    assert by_id["run-list-failed"]["status"] == "failed"
    assert by_id["run-list-failed"]["ended_at"] is not None


def test_list_runs_respects_limit():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        client.portal.call(_seed, app.state.components, 5, 0)
        resp = client.get("/runs", params={"limit": 2})

    body = resp.json()
    assert len(body["runs"]) == 2
    assert body["limit"] == 2


def test_list_runs_respects_offset_no_overlap_with_first_page():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        client.portal.call(_seed, app.state.components, 5, 0)
        page1 = client.get("/runs", params={"limit": 2, "offset": 0}).json()
        page2 = client.get("/runs", params={"limit": 2, "offset": 2}).json()

    ids_page1 = {r["id"] for r in page1["runs"]}
    ids_page2 = {r["id"] for r in page2["runs"]}
    assert len(page1["runs"]) == 2
    assert len(page2["runs"]) == 2
    assert ids_page1.isdisjoint(ids_page2)


def test_list_runs_limit_validation_rejects_out_of_range():
    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        too_high = client.get("/runs", params={"limit": 101})
        too_low = client.get("/runs", params={"limit": 0})
        negative_offset = client.get("/runs", params={"offset": -1})

    assert too_high.status_code == 422
    assert too_low.status_code == 422
    assert negative_offset.status_code == 422


def test_list_runs_does_not_load_full_audit_tree():
    """History Investigation-Identity V1 -- `question` é ADITIVO ao
    contrato leve de listagem, nunca uma porta de entrada pra reconstruir
    a árvore de audit inteira: o valor já está no MESMO blob JSON que
    `list_runs` já carregava (`run_config_json`), então adicionar o campo
    nunca introduz N+1 nem qualquer nova consulta."""
    result = full_council_run_result()
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    app = create_app(settings=_settings(), components_factory=factory)

    with TestClient(app) as client:
        client.post(
            "/runs", json={"question": "pergunta", "enabled_providers": ["openai", "anthropic"]}
        )
        resp = client.get("/runs")

    run = resp.json()["runs"][0]
    assert set(run.keys()) == {"id", "status", "started_at", "ended_at", "question", "kind"}
    assert run["question"] == "pergunta"
    assert run["kind"] == "council"  # Direct Answer Execution V1: aditivo, run do Conselho


def test_list_runs_exposes_exact_canonical_question_for_every_lifecycle_status():
    """History Investigation-Identity V1 -- os 4 lifecycle roots
    (completed/insufficient_quorum/running/failed) expõem a pergunta
    canônica EXATA persistida em `run_config_json["question"]`, nunca
    truncada/reescrita/normalizada -- cada uma com um texto DISTINTO pra
    provar que não há vazamento entre registros."""

    async def seed(components) -> None:
        await components.repository.save_success(
            full_council_run_result(run_config=run_config(question="Pergunta da run completada?"))
        )
        exc = quorum_failure_exception()
        await components.repository.save_quorum_failure(
            exc,
            run_config=run_config(question="Pergunta da run de quórum insuficiente?"),
            started_at=now(),
            failed_at=now(),
        )
        await components.repository.save_accepted(
            "run-question-running",
            run_config=run_config(question="Pergunta da run em andamento?"),
            started_at=now(),
            provider_execution_policy=components.provider_execution_policy,
        )
        await components.repository.save_accepted(
            "run-question-failed",
            run_config=run_config(question="Pergunta da run que falhou?"),
            started_at=now(),
            provider_execution_policy=components.provider_execution_policy,
        )
        await components.repository.save_unexpected_failure(
            "run-question-failed",
            failed_at=now(),
            failure_classification="WeirdBug",
            failure_message="Erro interno inesperado durante a execução.",
        )

    app = create_app(settings=_settings(), components_factory=make_components_factory())

    with TestClient(app) as client:
        client.portal.call(seed, app.state.components)
        resp = client.get("/runs")

    body = resp.json()
    questions_by_status = {r["status"]: r["question"] for r in body["runs"]}
    assert questions_by_status == {
        "completed": "Pergunta da run completada?",
        "insufficient_quorum": "Pergunta da run de quórum insuficiente?",
        "running": "Pergunta da run em andamento?",
        "failed": "Pergunta da run que falhou?",
    }
