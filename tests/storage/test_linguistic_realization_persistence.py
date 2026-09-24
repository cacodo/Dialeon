"""
Linguistic Realization -- persistência aditiva, coerência entre registros no
caminho real de `save_success`, leitura de execuções históricas/anteriores a
este slice, e superfícies públicas (API/CLI). Nenhum provider real.
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
from app.editor.linguistic_realization import (
    LINGUISTIC_REALIZATION_CONTRACT_VERSION,
    LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION,
    LinguisticRealizationProposal,
    build_linguistic_realization,
    candidate_digest,
    primary_answer_digest,
)
from app.editor.linguistic_realization_coherence import LinguisticRealizationCoherenceError
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from app.presentation.mappers import completed_run_audit, completed_run_response
from app.presentation.schemas import CompletedRunAudit, CompletedRunResponse
from app.storage.database import create_engine, init_db
from tests.api.helpers import build_test_components, make_components_factory
from tests.storage.fixtures import full_council_run_result, with_recomputed_reconciliation
from tests.storage.test_primary_answer_persistence import _with_primary_answer


def _accepted_attempt(style_attempt, *, contract_version, raw_output_text, attempt_id, provider=None):
    update = {
        "id": attempt_id,
        "attempt_number": 1,
        "raw_output_text": raw_output_text,
        "parse_status": "accepted",
        "parse_error_message": None,
        "request_provenance": RequestProvenance(
            contract_version=contract_version,
            request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
        ),
    }
    if provider is not None:
        update["provider"] = provider
    return style_attempt.model_copy(update=update)


def _with_linguistic_realization(result, *, review_provider=None, fallback_reason=None):
    """Constrói uma realização + revisão semântica ACEITAS e coerentes sobre
    o Primary Answer já plantado por `_with_primary_answer` -- ou, quando
    `fallback_reason` é passado, um estado "não elegível" sem nenhuma
    tentativa (mesmo padrão de `_with_primary_and_natural_answer`)."""
    result, primary = _with_primary_answer(result)
    style_attempts = result.editor_result.attempts
    claim_id = primary.sections[0].items[0].claim_id
    review_provider = review_provider or result.editor_result.editor_provider

    if fallback_reason is not None:
        final_answer = result.editor_result.final_answer.model_copy(
            update={"linguistic_realization": None}
        )
        editor = result.editor_result.model_copy(
            update={
                "final_answer": final_answer,
                "linguistic_realization_attempts": [],
                "linguistic_semantic_review_attempts": [],
                "linguistic_semantic_review_provider": None,
                "linguistic_realization_fallback_reason": fallback_reason,
            }
        )
        return result.model_copy(update={"editor_result": editor}), primary, None

    proposal_payload = {"blocks": [{"claim_ids": [claim_id], "text": "Reescrita da claim."}]}
    proposal = LinguisticRealizationProposal.model_validate(proposal_payload)
    digest = candidate_digest(
        proposal, based_on_primary_answer_digest=primary_answer_digest(primary)
    )
    realization = build_linguistic_realization(proposal, primary=primary)

    realization_attempt = _accepted_attempt(
        style_attempts[0],
        contract_version=LINGUISTIC_REALIZATION_CONTRACT_VERSION,
        raw_output_text=json.dumps(proposal_payload),
        attempt_id="realization-1",
    )
    review_attempt = _accepted_attempt(
        style_attempts[0],
        contract_version=LINGUISTIC_SEMANTIC_REVIEW_CONTRACT_VERSION,
        raw_output_text=json.dumps(
            {"candidate_digest": digest, "decision": "accept", "issue_codes": []}
        ),
        attempt_id="review-1",
        provider=review_provider,
    )

    final_answer = result.editor_result.final_answer.model_copy(
        update={"linguistic_realization": realization}
    )
    editor = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "linguistic_realization_attempts": [realization_attempt],
            "linguistic_semantic_review_attempts": [review_attempt],
            "linguistic_semantic_review_provider": review_provider,
            "linguistic_realization_fallback_reason": None,
        }
    )
    return result.model_copy(update={"editor_result": editor}), primary, realization


# ---------------------------------------------------------------------------
# Save / reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_realization_roundtrips_through_save_and_reload(repo):
    result, primary, realization = _with_linguistic_realization(full_council_run_result())

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.linguistic_realization == realization
    assert loaded.editor_result.final_answer.primary_answer == primary
    assert loaded.editor_result.linguistic_realization_fallback_reason is None
    assert loaded.editor_result.linguistic_semantic_review_provider == "anthropic"
    assert [a.id for a in loaded.editor_result.linguistic_realization_attempts] == ["realization-1"]
    assert [a.id for a in loaded.editor_result.linguistic_semantic_review_attempts] == ["review-1"]


@pytest.mark.asyncio
async def test_realization_fallback_reason_roundtrips(repo):
    result, _, _ = _with_linguistic_realization(
        full_council_run_result(), fallback_reason="semantic_review_rejection"
    )

    await repo.save_success(with_recomputed_reconciliation(result))
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.linguistic_realization is None
    assert loaded.editor_result.linguistic_realization_fallback_reason == "semantic_review_rejection"


@pytest.mark.asyncio
async def test_a_run_without_realization_reads_back_with_none_and_nothing_fabricated(repo):
    result, _ = _with_primary_answer(full_council_run_result())  # primary sem realização

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer is not None
    assert loaded.editor_result.final_answer.linguistic_realization is None
    assert loaded.editor_result.linguistic_realization_fallback_reason is None
    assert loaded.editor_result.linguistic_realization_attempts == []
    assert loaded.editor_result.linguistic_semantic_review_attempts == []


@pytest.mark.asyncio
async def test_a_different_review_provider_roundtrips_truthfully(repo):
    result, primary, realization = _with_linguistic_realization(
        full_council_run_result(), review_provider="openai"
    )

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.linguistic_semantic_review_provider == "openai"
    assert loaded.editor_result.editor_provider == "anthropic"
    (review_attempt,) = loaded.editor_result.linguistic_semantic_review_attempts
    assert review_attempt.provider == "openai"


# ---------------------------------------------------------------------------
# Coerência entre registros -- caminho real de save_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_success_rejects_a_realization_that_diverges_from_the_accepted_attempt(repo, engine):
    result, primary, realization = _with_linguistic_realization(full_council_run_result())
    tampered = realization.model_copy(
        update={
            "rendered_text": realization.rendered_text + " Tudo comprovado.",
            "blocks": tuple(
                b.model_copy(update={"text": b.text + " Tudo comprovado."})
                for b in realization.blocks
            ),
        }
    )
    final_answer = result.editor_result.final_answer.model_copy(
        update={"linguistic_realization": tampered}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    tampered_result = result.model_copy(update={"editor_result": editor})

    with pytest.raises(LinguisticRealizationCoherenceError):
        await repo.save_success(tampered_result)

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM council_runs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM final_answers"))).scalar_one() == 0


# ---------------------------------------------------------------------------
# Tampering entre registros falha fechado -- pós-leitura do storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: d.update(rendered_text=d["rendered_text"] + " Tudo comprovado."),
        lambda d: d.update(based_on_primary_answer_digest="f" * 64),
    ],
)
async def test_tampered_persisted_realization_fails_closed_on_load(repo, engine, tamper):
    result, _, realization = _with_linguistic_realization(full_council_run_result())
    await repo.save_success(result)
    async with engine.connect() as conn:
        (raw,) = (
            await conn.execute(text("SELECT linguistic_realization_json FROM final_answers"))
        ).one()
    data = json.loads(raw) if isinstance(raw, str) else raw
    tamper(data)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE final_answers SET linguistic_realization_json = :j"),
            {"j": json.dumps(data)},
        )

    with pytest.raises((ValidationError, LinguisticRealizationCoherenceError)):
        await repo.get_run(result.id)


@pytest.mark.asyncio
async def test_persisted_unknown_realization_contract_version_fails_closed_on_load(repo, engine):
    result, _, realization = _with_linguistic_realization(full_council_run_result())
    await repo.save_success(result)
    async with engine.connect() as conn:
        (raw,) = (
            await conn.execute(text("SELECT linguistic_realization_json FROM final_answers"))
        ).one()
    data = json.loads(raw) if isinstance(raw, str) else raw
    data["contract_version"] = "linguistic_realization_v99"
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE final_answers SET linguistic_realization_json = :j"),
            {"j": json.dumps(data)},
        )

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


# ---------------------------------------------------------------------------
# Leitura histórica -- banco/registro anterior a este slice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_linguistic_realization_database_gets_additive_columns_and_null_values(tmp_path):
    from app.storage.models import Base  # noqa: F401  (garante o metadata carregado)

    db_path = str(tmp_path / "pre_realization.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE final_answers DROP COLUMN linguistic_realization_json")
    conn.execute("ALTER TABLE council_runs DROP COLUMN editor_linguistic_realization_fallback_reason")
    conn.execute("ALTER TABLE council_runs DROP COLUMN editor_linguistic_semantic_review_provider")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await init_db(engine)  # idempotente
    await engine.dispose()

    conn = sqlite3.connect(db_path)
    columns = {
        table: {c[1] for c in conn.execute(f"PRAGMA table_info({table})")}
        for table in ("final_answers", "council_runs")
    }
    conn.close()
    assert "linguistic_realization_json" in columns["final_answers"]
    assert "editor_linguistic_realization_fallback_reason" in columns["council_runs"]
    assert "editor_linguistic_semantic_review_provider" in columns["council_runs"]


def test_historical_final_answer_row_without_realization_columns_still_reads():
    """Espelha um registro histórico anterior a este slice (sem
    linguistic_realization_json/colunas relacionadas) -- a leitura nunca
    fabrica uma LinguisticRealization, e nunca falha."""
    from datetime import datetime, timezone

    from app.storage.models import FinalAnswerRow
    from app.storage.serializers import final_answer_from_row

    row = FinalAnswerRow(
        id="fa-historico-realizacao-1",
        council_run_id="run-historico-realizacao-1",
        answer_text="Resposta histórica anterior a esta camada.",
        limitations_json=["limitação histórica"],
        status="llm_planned",
        editor_model="claude-legacy",
        based_on_verdict_id="verdict-historico-1",
        judge_confidence=0.6,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    final_answer = final_answer_from_row(row)

    assert final_answer.status == "llm_planned"
    assert final_answer.linguistic_realization is None


# ---------------------------------------------------------------------------
# API -- compatibilidade aditiva
# ---------------------------------------------------------------------------


def _client_with(result):
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    return create_app(settings=Settings(_env_file=None), components_factory=factory)


def test_api_exposes_linguistic_realization_additively_and_keeps_earlier_layers():
    result, primary, realization = _with_linguistic_realization(full_council_run_result())
    app = _client_with(result)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        fetched = client.get(f"/runs/{created.json()['id']}")

    assert created.status_code == 201
    for body in (created.json(), fetched.json()):
        final = CompletedRunResponse.model_validate(body).final_answer
        assert final.linguistic_realization is not None
        assert final.linguistic_realization.rendered_text == realization.rendered_text
        assert final.linguistic_realization_presentation_eligible is True
        assert final.primary_answer is not None  # nunca substituído
        assert final.answer_text == result.editor_result.final_answer.answer_text  # inalterado


def test_api_old_shape_run_has_linguistic_realization_as_null_without_breaking_old_clients():
    result = full_council_run_result()  # sem primary_answer nem linguistic_realization
    app = _client_with(result)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})

    assert created.json()["final_answer"]["linguistic_realization"] is None
    assert created.json()["final_answer"]["linguistic_realization_presentation_eligible"] is False
    assert created.json()["final_answer"]["answer_text"]


def test_openapi_documents_linguistic_realization_as_an_optional_response_field():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    schemas = app.openapi()["components"]["schemas"]

    final = schemas["FinalAnswerPublic"]
    assert "linguistic_realization" in final["properties"]
    assert "linguistic_realization" not in final.get("required", [])
    assert "linguistic_realization_presentation_eligible" in final["properties"]
    assert "LinguisticRealizationPublic" in schemas


# ---------------------------------------------------------------------------
# CLI -- ordem de fallback / apresentação humana / auditoria
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_human_output_prefers_linguistic_realization_over_everything_else():
    result, primary, realization = _with_linguistic_realization(full_council_run_result())
    response = completed_run_response(
        result, provider_execution_policy=None, default_model_authority_snapshot=None
    )

    text_out = output.human_run_result(response)
    lines = text_out.splitlines()

    assert "resposta:" in lines
    assert "resposta principal (estruturada):" in lines
    assert "avaliação completa:" in lines
    assert (
        lines.index("resposta:")
        < lines.index("resposta principal (estruturada):")
        < lines.index("avaliação completa:")
    )
    assert realization.rendered_text.split("\n")[0] in text_out
    assert "não é verificação externa" in text_out


@pytest.mark.asyncio
async def test_cli_human_output_falls_back_to_primary_without_realization_or_natural():
    result, primary = _with_primary_answer(full_council_run_result())
    response = completed_run_response(
        result, provider_execution_policy=None, default_model_authority_snapshot=None
    )

    text_out = output.human_run_result(response)
    lines = text_out.splitlines()

    assert "resposta:" not in lines
    assert "resposta principal:" in lines
    assert primary.sections[0].items[0].claim_text in text_out


@pytest.mark.asyncio
async def test_cli_json_get_and_audit_expose_linguistic_realization(capsys):
    result, primary, realization = _with_linguistic_realization(full_council_run_result())
    components = await build_test_components(Settings(_env_file=None))
    await components.repository.save_success(result)

    assert await commands.cmd_get(components, run_id=result.id, as_json=True) == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["final_answer"]["linguistic_realization"]["rendered_text"] == realization.rendered_text

    assert await commands.cmd_audit(components, run_id=result.id, as_json=False) == commands.EXIT_OK
    audit_text = capsys.readouterr().out
    assert "realização_linguística: presente" in audit_text


@pytest.mark.asyncio
async def test_cli_audit_reports_the_fallback_reason_when_absent(capsys):
    result, _, _ = _with_linguistic_realization(
        full_council_run_result(), fallback_reason="semantic_review_rejection"
    )
    components = await build_test_components(Settings(_env_file=None))
    await components.repository.save_success(with_recomputed_reconciliation(result))

    assert await commands.cmd_audit(components, run_id=result.id, as_json=False) == commands.EXIT_OK
    audit_text = capsys.readouterr().out
    assert "realização_linguística: ausente (semantic_review_rejection)" in audit_text
