"""
Primary Answer -- persistência aditiva, leitura de registros anteriores e
superfícies públicas (API / CLI humano / CLI JSON). Nenhum provider real.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.api.app import create_app
from app.cli import commands, output
from app.config import Settings
from app.debate.claims import get_current_claims
from app.editor.primary_answer import PrimaryAnswerPlan, render_primary_answer, validate_plan
from app.editor.result import EditorResult
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from app.presentation.mappers import completed_run_response
from app.presentation.schemas import CompletedRunAudit, CompletedRunResponse
from app.storage.database import create_engine, init_db
from tests.api.helpers import build_test_components, make_components_factory
from tests.storage.fixtures import full_council_run_result, with_recomputed_reconciliation


def _with_primary_answer(result, *, fallback_reason=None, primary_attempts=True):
    verdict = result.judge_result.verdict
    claims = result.debate_result.claims
    plan = PrimaryAnswerPlan.model_validate({"central_conclusion": [verdict.claim_assessments[0].claim_id]})
    selection = validate_plan(
        plan, verdict=verdict, current_claims=get_current_claims(claims), all_claims=claims
    )
    primary = render_primary_answer(
        selection,
        based_on_verdict_id=verdict.id,
        limitations=tuple(result.editor_result.final_answer.limitations),
    )
    style_attempts = result.editor_result.attempts
    primary_attempt = style_attempts[0].model_copy(
        update={
            "id": "primary-attempt-1",
            "attempt_number": 1,
            "request_provenance": RequestProvenance(
                contract_version="primary_answer_plan_v1",
                request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
            ),
        }
    )
    final_answer = result.editor_result.final_answer.model_copy(
        update={"primary_answer": primary, "status": "llm_planned"}
    )
    editor = EditorResult(
        final_answer=final_answer,
        attempts=style_attempts,
        fallback_reason=result.editor_result.fallback_reason,
        editor_provider=result.editor_result.editor_provider,
        cumulative_budget_exceeded=result.editor_result.cumulative_budget_exceeded,
        primary_answer_attempts=[primary_attempt] if primary_attempts else [],
        primary_answer_fallback_reason=fallback_reason,
    )
    return result.model_copy(update={"editor_result": editor}), primary


@pytest.mark.asyncio
async def test_primary_answer_and_its_attempts_roundtrip_without_touching_the_style_attempts(repo, engine):
    result, primary = _with_primary_answer(full_council_run_result())

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer == primary
    assert loaded.editor_result.final_answer.answer_text == result.editor_result.final_answer.answer_text
    assert [a.id for a in loaded.editor_result.attempts] == [a.id for a in result.editor_result.attempts]
    assert [a.id for a in loaded.editor_result.primary_answer_attempts] == ["primary-attempt-1"]
    assert loaded.editor_result.primary_answer_attempts[0].request_provenance.contract_version == "primary_answer_plan_v1"
    async with engine.connect() as conn:
        purposes = (await conn.execute(text("SELECT id, purpose FROM editor_attempts ORDER BY id"))).all()
    assert ("primary-attempt-1", "primary_answer_plan") in [tuple(p) for p in purposes]
    assert all(purpose is None for attempt_id, purpose in purposes if attempt_id != "primary-attempt-1")
    # o accounting persistido soma as duas listas (mesmo mecanismo)
    assert loaded.editor_result.editor_input_tokens == result.editor_result.editor_input_tokens


@pytest.mark.asyncio
async def test_a_run_without_primary_answer_reads_back_with_none_and_nothing_fabricated(repo):
    result = full_council_run_result()

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer is None
    assert loaded.editor_result.primary_answer_attempts == []
    assert loaded.editor_result.primary_answer_fallback_reason is None


@pytest.mark.asyncio
async def test_fallback_reason_roundtrips(repo):
    result, _ = _with_primary_answer(full_council_run_result())
    editor = result.editor_result.model_copy(
        update={
            "final_answer": result.editor_result.final_answer.model_copy(update={"primary_answer": None}),
            "primary_answer_attempts": [],
            "primary_answer_fallback_reason": "budget_exhausted_before_primary_answer",
        }
    )
    result = result.model_copy(update={"editor_result": editor})

    await repo.save_success(with_recomputed_reconciliation(result))
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.primary_answer_fallback_reason == "budget_exhausted_before_primary_answer"
    assert loaded.editor_result.final_answer.primary_answer is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: d.update(rendered_text=d["rendered_text"] + " Tudo comprovado."),
        lambda d: d.update(scope_note="x"),
        lambda d: d["sections"][0]["items"][0].update(verdict_label="rejeitada pelo juiz com base no debate disponível"),
    ],
)
async def test_tampered_persisted_primary_answer_fails_closed_on_load(repo, engine, tamper):
    result, _ = _with_primary_answer(full_council_run_result())
    await repo.save_success(result)
    async with engine.connect() as conn:
        (raw,) = (await conn.execute(text("SELECT primary_answer_json FROM final_answers"))).one()
    data = json.loads(raw) if isinstance(raw, str) else raw
    tamper(data)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE final_answers SET primary_answer_json = :j"), {"j": json.dumps(data)})

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


@pytest.mark.asyncio
async def test_pre_primary_answer_database_gets_three_additive_columns_and_null_values(tmp_path):
    from app.storage.models import Base  # noqa: F401  (garante o metadata carregado)

    db_path = str(tmp_path / "pre_primary.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE final_answers DROP COLUMN primary_answer_json")
    conn.execute("ALTER TABLE editor_attempts DROP COLUMN purpose")
    conn.execute("ALTER TABLE council_runs DROP COLUMN editor_primary_answer_fallback_reason")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await init_db(engine)  # idempotente
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns = {
        table: {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        for table in ("final_answers", "editor_attempts", "council_runs")
    }
    conn.close()
    assert "primary_answer_json" in columns["final_answers"]
    assert "purpose" in columns["editor_attempts"]
    assert "editor_primary_answer_fallback_reason" in columns["council_runs"]


# ---------------------------------------------------------------------------
# API / CLI
# ---------------------------------------------------------------------------


def _client_with(result):
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    return create_app(settings=Settings(_env_file=None), components_factory=factory)


def test_api_exposes_the_primary_answer_additively_and_keeps_the_complete_answer():
    result, primary = _with_primary_answer(full_council_run_result())
    app = _client_with(result)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        fetched = client.get(f"/runs/{created.json()['id']}")
        audit = client.get(f"/runs/{created.json()['id']}/audit")

    assert created.status_code == 201
    for body in (created.json(), fetched.json()):
        final = CompletedRunResponse.model_validate(body).final_answer
        assert final.primary_answer is not None
        assert final.primary_answer.rendered_text == primary.rendered_text
        assert final.answer_text == result.editor_result.final_answer.answer_text  # inalterado
        assert final.primary_answer.sections[0].items[0].claim_id == primary.sections[0].items[0].claim_id
    audit_body = CompletedRunAudit.model_validate(audit.json())
    assert [a.attempt_number for a in audit_body.primary_answer_attempts] == [1]
    assert audit_body.editor_outcome.primary_answer_fallback_reason is None
    assert audit_body.final_answer.primary_answer is not None


def test_api_old_shape_run_has_the_optional_field_as_null_and_no_extra_attempts():
    result = full_council_run_result()
    app = _client_with(result)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        audit = client.get(f"/runs/{created.json()['id']}/audit")

    assert created.json()["final_answer"]["primary_answer"] is None
    assert audit.json()["primary_answer_attempts"] == []
    assert audit.json()["editor_outcome"]["primary_answer_fallback_reason"] is None


def test_openapi_documents_primary_answer_as_an_optional_response_field():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    schemas = app.openapi()["components"]["schemas"]

    final = schemas["FinalAnswerPublic"]
    assert "primary_answer" in final["properties"]
    assert "primary_answer" not in final.get("required", [])
    assert "PrimaryAnswerPublic" in schemas
    assert "primary_answer_attempts" not in schemas["CompletedRunAudit"].get("required", [])


@pytest.mark.asyncio
async def test_cli_human_output_shows_the_primary_answer_first_then_the_complete_answer():
    result, primary = _with_primary_answer(full_council_run_result())
    response = completed_run_response(
        result, provider_execution_policy=None, default_model_authority_snapshot=None
    )

    text_out = output.human_run_result(response)

    assert "resposta principal:" in text_out and "avaliação completa:" in text_out
    assert text_out.index("resposta principal:") < text_out.index("avaliação completa:")
    assert primary.sections[0].items[0].claim_text in text_out
    assert result.editor_result.final_answer.answer_text.splitlines()[0] in text_out


@pytest.mark.asyncio
async def test_cli_human_output_is_unchanged_without_a_primary_answer():
    response = completed_run_response(
        full_council_run_result(), provider_execution_policy=None, default_model_authority_snapshot=None
    )

    text_out = output.human_run_result(response)

    assert "resposta principal:" not in text_out and "avaliação completa:" not in text_out


@pytest.mark.asyncio
async def test_cli_human_output_neutralizes_terminal_control_characters_in_primary_text():
    result, _ = _with_primary_answer(full_council_run_result())
    evil = result.editor_result.final_answer.primary_answer.model_copy(deep=True)
    response = completed_run_response(
        result, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    hostile = response.final_answer.primary_answer.model_copy(
        update={"rendered_text": "linha\x1b[31mvermelha\x1b[0m"}
    )
    response = response.model_copy(
        update={"final_answer": response.final_answer.model_copy(update={"primary_answer": hostile})}
    )

    text_out = output.human_run_result(response)

    assert "\x1b" not in text_out
    assert evil is not None


@pytest.mark.asyncio
async def test_cli_json_get_and_audit_expose_the_primary_answer(capsys):
    result, primary = _with_primary_answer(full_council_run_result())
    components = await build_test_components(Settings(_env_file=None))
    await components.repository.save_success(result)

    assert await commands.cmd_get(components, run_id=result.id, as_json=True) == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["final_answer"]["primary_answer"]["rendered_text"] == primary.rendered_text

    assert await commands.cmd_audit(components, run_id=result.id, as_json=True) == commands.EXIT_OK
    audit = json.loads(capsys.readouterr().out)
    assert len(audit["primary_answer_attempts"]) == 1
    assert audit["final_answer"]["primary_answer"] is not None

    assert await commands.cmd_audit(components, run_id=result.id, as_json=False) == commands.EXIT_OK
    assert "resposta_principal: presente" in capsys.readouterr().out
