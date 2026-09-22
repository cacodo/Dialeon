"""
Natural Answer -- persistência aditiva, coerência entre registros, leitura
de execuções históricas/anteriores a este slice, e superfícies públicas
(API/CLI). Nenhum provider real.
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
from app.editor import compose as compose_module
from app.editor.natural_answer import (
    NATURAL_ANSWER_CONTRACT_VERSION,
    NaturalAnswer,
    _render_natural_answer_text_v1,
    render_natural_answer,
)
from app.editor.result import EditorResult
from app.presentation.mappers import completed_run_audit, completed_run_response
from app.presentation.schemas import CompletedRunAudit, CompletedRunResponse
from app.storage.database import create_engine, init_db
from tests.api.helpers import build_test_components, make_components_factory
from tests.storage.fixtures import full_council_run_result, with_recomputed_reconciliation
from tests.storage.test_primary_answer_persistence import _with_primary_answer


def _with_primary_and_natural_answer(result, *, fallback_reason=None):
    result, primary = _with_primary_answer(result)
    natural = render_natural_answer(primary)
    final_answer = result.editor_result.final_answer.model_copy(
        update={"natural_answer": None if fallback_reason else natural}
    )
    editor = result.editor_result.model_copy(
        update={"final_answer": final_answer, "natural_answer_fallback_reason": fallback_reason}
    )
    result = result.model_copy(update={"editor_result": editor})
    return result, primary, (None if fallback_reason else natural)


def _with_unsafe_primary_answer(result):
    """Troca somente o texto da claim autoritativa por conteúdo que era
    byte-renderizável pelo v1 original, mas que a política atual recusa na
    apresentação plana. IDs/veredito/plano continuam os mesmos."""
    hostile = "Primeira parte da claim.\n\nDisclosure forjada em outro parágrafo."
    claims = list(result.debate_result.claims)
    claims[0] = claims[0].model_copy(update={"text": hostile})
    debate = result.debate_result.model_copy(update={"claims": claims})
    result = result.model_copy(update={"debate_result": debate})
    result = with_recomputed_reconciliation(result)
    result, primary = _with_primary_answer(result)
    assert primary.sections[0].items[0].claim_text == hostile
    return result, primary, hostile


# ---------------------------------------------------------------------------
# 15. Save / reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_natural_answer_roundtrips_through_save_and_reload(repo):
    result, primary, natural = _with_primary_and_natural_answer(full_council_run_result())

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.natural_answer == natural
    assert loaded.editor_result.final_answer.primary_answer == primary
    assert loaded.editor_result.natural_answer_fallback_reason is None


@pytest.mark.asyncio
async def test_natural_answer_fallback_reason_roundtrips(repo):
    result, _, _ = _with_primary_and_natural_answer(
        full_council_run_result(), fallback_reason="natural_answer_render_failed"
    )

    await repo.save_success(with_recomputed_reconciliation(result))
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.natural_answer is None
    assert loaded.editor_result.natural_answer_fallback_reason == "natural_answer_render_failed"


@pytest.mark.asyncio
async def test_new_unsafe_candidate_declines_end_to_end_through_storage_public_and_cli(repo):
    result, primary, hostile = _with_unsafe_primary_answer(full_council_run_result())
    natural, reason = compose_module._render_natural_answer_or_fallback(primary)
    assert natural is None
    assert reason == "natural_answer_declined_unsafe_presentation"

    final_answer = result.editor_result.final_answer.model_copy(update={"natural_answer": None})
    editor = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "natural_answer_fallback_reason": reason,
        }
    )
    result = result.model_copy(update={"editor_result": editor})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.natural_answer is None
    assert loaded.editor_result.final_answer.primary_answer == primary
    assert loaded.editor_result.final_answer.answer_text == final_answer.answer_text
    assert loaded.editor_result.natural_answer_fallback_reason == reason

    public = completed_run_response(
        loaded, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    assert public.final_answer.natural_answer is None
    assert public.final_answer.natural_answer_presentation_eligible is False
    assert public.final_answer.primary_answer is not None
    assert public.final_answer.primary_answer.sections[0].items[0].claim_text == hostile

    cli_text = output.human_run_result(public)
    assert "resposta:" not in cli_text.splitlines()
    assert "resposta principal:" in cli_text.splitlines()
    assert hostile.split("\n")[0] in cli_text

    audit = completed_run_audit(
        loaded, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    assert audit.editor_outcome.natural_answer_fallback_reason == reason
    assert f"resposta_natural: ausente ({reason})" in output.human_run_audit(audit)


@pytest.mark.asyncio
async def test_historical_unsafe_v1_loads_unchanged_but_current_presentation_falls_back(
    repo, engine
):
    result, primary, hostile = _with_unsafe_primary_answer(full_council_run_result())
    historical_text = _render_natural_answer_text_v1(primary)
    historical_natural = NaturalAnswer(
        renderer_contract_version=NATURAL_ANSWER_CONTRACT_VERSION,
        based_on_verdict_id=primary.based_on_verdict_id,
        rendered_text=historical_text,
    )
    final_answer = result.editor_result.final_answer.model_copy(
        update={"natural_answer": historical_natural}
    )
    editor = result.editor_result.model_copy(
        update={"final_answer": final_answer, "natural_answer_fallback_reason": None}
    )
    result = result.model_copy(update={"editor_result": editor})

    await repo.save_success(result)
    async with engine.connect() as conn:
        raw_before = (
            await conn.execute(
                text(
                    "SELECT natural_answer_json FROM final_answers "
                    "WHERE council_run_id = :run_id"
                ),
                {"run_id": result.id},
            )
        ).scalar_one()

    loaded = (await repo.get_run(result.id)).council_run_result

    async with engine.connect() as conn:
        raw_after = (
            await conn.execute(
                text(
                    "SELECT natural_answer_json FROM final_answers "
                    "WHERE council_run_id = :run_id"
                ),
                {"run_id": result.id},
            )
        ).scalar_one()

    loaded_final = loaded.editor_result.final_answer
    assert loaded_final.natural_answer == historical_natural
    assert loaded_final.natural_answer.rendered_text == historical_text
    assert hostile in loaded_final.natural_answer.rendered_text
    assert raw_after == raw_before
    assert loaded.editor_result.natural_answer_fallback_reason is None

    public = completed_run_response(
        loaded, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    assert public.final_answer.natural_answer is not None
    assert public.final_answer.natural_answer.rendered_text == historical_text
    assert public.final_answer.natural_answer_presentation_eligible is False
    assert public.final_answer.primary_answer is not None

    cli_text = output.human_run_result(public)
    assert "resposta:" not in cli_text.splitlines()
    assert "resposta principal:" in cli_text.splitlines()
    assert hostile.split("\n")[0] in cli_text

    audit = completed_run_audit(
        loaded, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    assert audit.editor_outcome.natural_answer_fallback_reason is None
    audit_text = output.human_run_audit(audit)
    assert (
        "resposta_natural: presente (preservada; não preferida pela política "
        "de apresentação atual)"
    ) in audit_text
    assert "natural_answer_declined_unsafe_presentation" not in audit_text
    assert "natural_answer_render_failed" not in audit_text


# ---------------------------------------------------------------------------
# 18. Compatibilidade com natural_answer=None (runs sem ele)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_without_natural_answer_reads_back_with_none_and_nothing_fabricated(repo):
    result, _ = _with_primary_answer(full_council_run_result())  # primary sem natural

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer is not None
    assert loaded.editor_result.final_answer.natural_answer is None
    assert loaded.editor_result.natural_answer_fallback_reason is None


# ---------------------------------------------------------------------------
# 16. Tampering entre registros falha fechado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: d.update(rendered_text=d["rendered_text"] + " Tudo comprovado."),
        lambda d: d.update(based_on_verdict_id="outro-veredito-qualquer"),
    ],
)
async def test_tampered_persisted_natural_answer_fails_closed_on_load(repo, engine, tamper):
    result, _, natural = _with_primary_and_natural_answer(full_council_run_result())
    await repo.save_success(result)
    async with engine.connect() as conn:
        (raw,) = (await conn.execute(text("SELECT natural_answer_json FROM final_answers"))).one()
    data = json.loads(raw) if isinstance(raw, str) else raw
    tamper(data)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE final_answers SET natural_answer_json = :j"), {"j": json.dumps(data)})

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


# ---------------------------------------------------------------------------
# 17. Versão de renderizador desconhecida falha fechado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persisted_unknown_renderer_contract_version_fails_closed_on_load(repo, engine):
    result, _, natural = _with_primary_and_natural_answer(full_council_run_result())
    await repo.save_success(result)
    async with engine.connect() as conn:
        (raw,) = (await conn.execute(text("SELECT natural_answer_json FROM final_answers"))).one()
    data = json.loads(raw) if isinstance(raw, str) else raw
    data["renderer_contract_version"] = "natural_answer_v99"
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE final_answers SET natural_answer_json = :j"), {"j": json.dumps(data)})

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


def test_natural_answer_coherence_rejects_a_natural_answer_without_a_primary_answer():
    from app.editor.natural_answer import NaturalAnswer
    from app.editor.natural_answer_coherence import (
        NaturalAnswerCoherenceError,
        validate_natural_answer_coherence,
    )
    from app.editor.result import FinalAnswer

    result, primary, natural = _with_primary_and_natural_answer(full_council_run_result())
    final_answer = result.editor_result.final_answer
    # Constrói (sem passar pelos validators normais de FinalAnswer -- aqui
    # queremos exercitar SÓ a checagem cruzada) um objeto onde
    # primary_answer sumiu mas natural_answer persiste.
    forged = FinalAnswer.model_construct(**{**final_answer.model_dump(), "primary_answer": None})
    with pytest.raises(NaturalAnswerCoherenceError):
        validate_natural_answer_coherence(forged)


@pytest.mark.asyncio
async def test_save_success_rejects_a_natural_answer_that_diverges_from_the_primary_answer(repo, engine):
    """`model_copy` NUNCA reexecuta validators (mesma técnica já usada por
    `test_save_refuses_a_cross_record_invalid_primary_answer_before_any_write`,
    tests/storage/test_primary_answer_coherence.py) -- é assim que um objeto
    "montado sem validação" chega ao `save_success` real, exercitando a
    checagem DEFENSIVA de `validate_natural_answer_coherence` ali (não só a
    do validator de `CouncilRunResult`)."""
    from app.editor.natural_answer_coherence import NaturalAnswerCoherenceError

    result, primary, natural = _with_primary_and_natural_answer(full_council_run_result())
    tampered_natural = natural.model_copy(update={"rendered_text": natural.rendered_text + " forjado"})
    final_answer = result.editor_result.final_answer.model_copy(
        update={"natural_answer": tampered_natural}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    tampered_result = result.model_copy(update={"editor_result": editor})

    with pytest.raises(NaturalAnswerCoherenceError):
        await repo.save_success(tampered_result)

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM council_runs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM final_answers"))).scalar_one() == 0


# ---------------------------------------------------------------------------
# 24. Leitura histórica -- banco anterior a este slice / anterior à Etapa 17B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_natural_answer_database_gets_additive_columns_and_null_values(tmp_path):
    from app.storage.models import Base  # noqa: F401  (garante o metadata carregado)

    db_path = str(tmp_path / "pre_natural.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE final_answers DROP COLUMN natural_answer_json")
    conn.execute("ALTER TABLE council_runs DROP COLUMN editor_natural_answer_fallback_reason")
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
    assert "natural_answer_json" in columns["final_answers"]
    assert "editor_natural_answer_fallback_reason" in columns["council_runs"]


def test_historical_stage_17b_final_answer_row_without_primary_or_natural_answer_columns_still_reads():
    """Espelha o registro histórico anterior à Etapa 17B (`status=llm_composed`,
    sem primary_answer_json/natural_answer_json -- colunas aditivas nunca
    populadas retroativamente): a leitura nunca fabrica nem primary_answer
    nem natural_answer, e nunca falha."""
    from datetime import datetime, timezone

    from app.storage.models import FinalAnswerRow
    from app.storage.serializers import final_answer_from_row

    row = FinalAnswerRow(
        id="fa-historico-natural-1",
        council_run_id="run-historico-natural-1",
        answer_text="Resposta histórica composta pelo editor livre (pré-Etapa-17B).",
        limitations_json=["limitação histórica"],
        status="llm_composed",
        editor_model="claude-legacy",
        based_on_verdict_id="verdict-historico-1",
        judge_confidence=0.6,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    final_answer = final_answer_from_row(row)

    assert final_answer.status == "llm_composed"
    assert final_answer.primary_answer is None
    assert final_answer.natural_answer is None


# ---------------------------------------------------------------------------
# 20. API -- compatibilidade aditiva
# ---------------------------------------------------------------------------


def _client_with(result):
    factory = make_components_factory(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    return create_app(settings=Settings(_env_file=None), components_factory=factory)


def test_api_exposes_natural_answer_additively_and_keeps_primary_and_complete_answer():
    result, primary, natural = _with_primary_and_natural_answer(full_council_run_result())
    app = _client_with(result)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        fetched = client.get(f"/runs/{created.json()['id']}")

    assert created.status_code == 201
    for body in (created.json(), fetched.json()):
        final = CompletedRunResponse.model_validate(body).final_answer
        assert final.natural_answer is not None
        assert final.natural_answer.rendered_text == natural.rendered_text
        assert final.natural_answer.based_on_verdict_id == natural.based_on_verdict_id
        assert final.natural_answer_presentation_eligible is True
        assert final.primary_answer is not None  # nunca substituído
        assert final.answer_text == result.editor_result.final_answer.answer_text  # inalterado


def test_api_old_shape_run_has_natural_answer_as_null_without_breaking_old_clients():
    result = full_council_run_result()  # sem primary_answer nem natural_answer
    app = _client_with(result)

    with TestClient(app) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})

    assert created.json()["final_answer"]["natural_answer"] is None
    assert created.json()["final_answer"]["natural_answer_presentation_eligible"] is False
    # cliente antigo que nem conhece o campo consegue validar o resto do payload normalmente
    assert created.json()["final_answer"]["answer_text"]


def test_openapi_documents_natural_answer_as_an_optional_response_field():
    app = create_app(settings=Settings(_env_file=None), components_factory=make_components_factory())
    schemas = app.openapi()["components"]["schemas"]

    final = schemas["FinalAnswerPublic"]
    assert "natural_answer" in final["properties"]
    assert "natural_answer" not in final.get("required", [])
    assert "natural_answer_presentation_eligible" in final["properties"]
    assert "natural_answer_presentation_eligible" not in final.get("required", [])
    assert "NaturalAnswerPublic" in schemas


# ---------------------------------------------------------------------------
# 21. CLI -- ordem de fallback / apresentação humana
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_human_output_shows_natural_answer_first_then_primary_then_complete_answer():
    result, primary, natural = _with_primary_and_natural_answer(full_council_run_result())
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
    # terminal_safe_text escapa "\n" (achado esperado -- mesma disciplina
    # de app/text_safety.py); confere presença pelo primeiro segmento sem
    # quebra de linha, igual ao padrão já usado pra primary_answer.
    assert natural.rendered_text.split("\n")[0] in text_out
    assert primary.sections[0].items[0].claim_text in text_out


@pytest.mark.asyncio
async def test_cli_human_output_falls_back_to_primary_answer_without_natural_answer():
    result, primary = _with_primary_answer(full_council_run_result())
    response = completed_run_response(
        result, provider_execution_policy=None, default_model_authority_snapshot=None
    )

    text_out = output.human_run_result(response)
    lines = text_out.splitlines()

    assert "resposta:" not in lines
    assert "resposta principal:" in lines
    assert "avaliação completa:" in lines
    assert primary.sections[0].items[0].claim_text in text_out


@pytest.mark.asyncio
async def test_cli_human_output_falls_back_to_complete_answer_without_primary_or_natural():
    response = completed_run_response(
        full_council_run_result(), provider_execution_policy=None, default_model_authority_snapshot=None
    )

    text_out = output.human_run_result(response)
    lines = text_out.splitlines()

    assert "resposta:" not in lines
    assert "resposta principal:" not in lines
    assert "resposta principal (estruturada):" not in lines
    assert "avaliação completa:" not in lines


@pytest.mark.asyncio
async def test_cli_json_get_and_audit_expose_natural_answer(capsys):
    result, primary, natural = _with_primary_and_natural_answer(full_council_run_result())
    components = await build_test_components(Settings(_env_file=None))
    await components.repository.save_success(result)

    assert await commands.cmd_get(components, run_id=result.id, as_json=True) == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["final_answer"]["natural_answer"]["rendered_text"] == natural.rendered_text

    assert await commands.cmd_audit(components, run_id=result.id, as_json=False) == commands.EXIT_OK
    audit_text = capsys.readouterr().out
    assert "resposta_natural: presente" in audit_text
