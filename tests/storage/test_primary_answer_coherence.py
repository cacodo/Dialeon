"""
Primary Answer -- coerência ENTRE REGISTROS (save + reload).

Um PrimaryAnswer internamente coerente (rendered_text == render(sections),
contagens == itens) NÃO pode reivindicar autoridade que os registros da
execução não sustentam. Cada mutante abaixo é construído COERENTE (texto
renderizado e escopo recomputados), de modo que só a checagem cruzada o pega.
Nenhum provider real.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.editor.primary_answer import (
    PrimaryAnswer,
    PrimaryAnswerItem,
    PrimaryAnswerSection,
    build_scope_note,
    render_primary_answer_text,
)
from app.editor.primary_answer_coherence import (
    PrimaryAnswerCoherenceError,
    validate_primary_answer_coherence,
)
from app.models.domain import Claim
from tests.storage.fixtures import full_council_run_result, with_recomputed_reconciliation
from tests.storage.test_primary_answer_persistence import _with_primary_answer


def _mutant(
    primary: PrimaryAnswer,
    *,
    sections=None,
    limitations=None,
    assessed=None,
    omitted=None,
    based_on=None,
) -> PrimaryAnswer:
    """Mutante INTERNAMENTE coerente: texto/escopo recomputados."""
    sections = tuple(sections) if sections is not None else primary.sections
    limitations = tuple(limitations) if limitations is not None else primary.limitations
    assessed = assessed if assessed is not None else primary.assessed_claim_count
    omitted = omitted if omitted is not None else primary.omitted_not_established_count
    selected = sum(len(s.items) for s in sections)
    scope = build_scope_note(assessed, selected, omitted)
    return PrimaryAnswer(
        based_on_verdict_id=based_on or primary.based_on_verdict_id,
        lead_in=primary.lead_in,
        sections=sections,
        limitations=limitations,
        assessed_claim_count=assessed,
        selected_claim_count=selected,
        omitted_not_established_count=omitted,
        scope_note=scope,
        rendered_text=render_primary_answer_text(sections, limitations, scope),
    )


def _replace_first_item(primary: PrimaryAnswer, **changes) -> tuple[PrimaryAnswerSection, ...]:
    first = primary.sections[0]
    item = first.items[0]
    new_item = PrimaryAnswerItem(**{**item.model_dump(), **changes})
    new_first = PrimaryAnswerSection(
        role=first.role, heading=first.heading, items=(new_item, *first.items[1:])
    )
    return (new_first, *primary.sections[1:])


def _run_with_retired_parent():
    """Execução em que a claim avaliada c1 REVISA um pai (retirado)."""
    base = full_council_run_result()
    c1 = base.debate_result.claims[0]
    parent = Claim(
        text="Texto antigo, retirado pela revisão.",
        source_model_response_id=c1.source_model_response_id,
        round_introduced=1,
        status="active",
        supporting_model_response_ids=list(c1.supporting_model_response_ids),
        total_models_in_round=c1.total_models_in_round,
    )
    revised = c1.model_copy(update={"parent_claim_id": parent.id})
    debate = base.debate_result.model_copy(update={"claims": [parent, revised]})
    return base.model_copy(update={"debate_result": debate}), parent


def _with_final(result, primary: PrimaryAnswer):
    editor = result.editor_result
    final = editor.final_answer.model_copy(update={"primary_answer": primary})
    return result.model_copy(update={"editor_result": editor.model_copy(update={"final_answer": final})})


def _check(result) -> None:
    validate_primary_answer_coherence(result.debate_result, result.judge_result, result.editor_result)


# ---------------------------------------------------------------------------
# A função única
# ---------------------------------------------------------------------------


def test_a_fully_coherent_primary_answer_is_accepted():
    result, _ = _with_primary_answer(full_council_run_result())

    _check(result)  # não levanta


def test_a_null_primary_answer_is_never_checked_or_reconstructed():
    _check(full_council_run_result())


def _mutants():
    result, primary = _with_primary_answer(full_council_run_result())
    retired_result, parent = _run_with_retired_parent()
    retired_result, retired_primary = _with_primary_answer(retired_result)
    limitation = tuple(result.editor_result.final_answer.limitations)
    return {
        "retired-id swap": (
            retired_result,
            _mutant(retired_primary, sections=_replace_first_item(retired_primary, claim_id=parent.id)),
            "retirada",
        ),
        "nonexistent id": (
            result,
            _mutant(primary, sections=_replace_first_item(primary, claim_id="id-inexistente")),
            "não existe",
        ),
        "text drift (coordinated)": (
            result,
            _mutant(primary, sections=_replace_first_item(primary, claim_text="Texto totalmente outro.")),
            "claim_text",
        ),
        "verdict-label drift (coordinated)": (
            result,
            _mutant(
                primary,
                sections=_replace_first_item(primary, verdict_label="parcialmente sustentada, com ressalvas"),
            ),
            "verdict_label",
        ),
        "assessed-count drift (coordinated)": (
            result,
            _mutant(primary, assessed=primary.assessed_claim_count + 1),
            "assessed_claim_count",
        ),
        "omitted-count drift (coordinated)": (
            result,
            _mutant(primary, omitted=primary.omitted_not_established_count + 1),
            "omitted_not_established_count",
        ),
        "limitation drift (coordinated)": (
            result,
            _mutant(primary, limitations=(*limitation, "Limitação forjada.")),
            "limitations",
        ),
        "limitation removed (coordinated)": (
            result,
            _mutant(primary, limitations=()) if limitation else None,
            "limitations",
        ),
        "wrong verdict identity": (
            result,
            _mutant(primary, based_on="outro-veredito"),
            "based_on_verdict_id|veredito",
        ),
    }


@pytest.mark.parametrize("name", list(_mutants()))
def test_internally_coherent_but_cross_record_invalid_primary_answers_are_rejected(name):
    result, mutant, expected = _mutants()[name]
    if mutant is None:
        pytest.skip("fixture sem limitações para remover")
    # o mutante É internamente válido -- só a checagem cruzada o pega
    assert PrimaryAnswer.model_validate(mutant.model_dump(mode="json")) == mutant

    with pytest.raises(PrimaryAnswerCoherenceError, match=expected):
        _check(_with_final(result, mutant))


def test_the_final_answer_verdict_identity_is_also_enforced_at_construction():
    result, primary = _with_primary_answer(full_council_run_result())
    mutant = _mutant(primary, based_on="outro-veredito")

    with pytest.raises(ValidationError, match="based_on_verdict_id"):
        type(result.editor_result.final_answer).model_validate(
            {**result.editor_result.final_answer.model_dump(), "primary_answer": mutant.model_dump()}
        )


def test_no_accepted_primary_answer_attempt_is_rejected():
    result, _ = _with_primary_answer(full_council_run_result())
    editor = result.editor_result.model_copy(update={"primary_answer_attempts": []})

    with pytest.raises(PrimaryAnswerCoherenceError, match="ACEITA"):
        validate_primary_answer_coherence(result.debate_result, result.judge_result, editor)


@pytest.mark.parametrize("bad_contract", ["editor_v1", "judge_v2", "primary_answer_plan_v0", None])
def test_an_accepted_attempt_with_the_wrong_or_missing_contract_is_not_sufficient(bad_contract):
    result, _ = _with_primary_answer(full_council_run_result())
    attempt = result.editor_result.primary_answer_attempts[0]
    provenance = (
        attempt.request_provenance.model_copy(update={"contract_version": bad_contract})
        if bad_contract is not None
        else None
    )
    editor = result.editor_result.model_copy(
        update={"primary_answer_attempts": [attempt.model_copy(update={"request_provenance": provenance})]}
    )

    with pytest.raises(PrimaryAnswerCoherenceError, match="proveniência"):
        validate_primary_answer_coherence(result.debate_result, result.judge_result, editor)


def test_the_correct_primary_answer_plan_v1_attempt_is_accepted():
    result, _ = _with_primary_answer(full_council_run_result())

    assert result.editor_result.primary_answer_attempts[0].request_provenance.contract_version == (
        "primary_answer_plan_v1"
    )
    _check(result)


def test_a_primary_answer_without_an_accepted_judge_verdict_is_rejected():
    from tests.editor.fixtures import judge_result

    result, _ = _with_primary_answer(full_council_run_result())

    with pytest.raises(PrimaryAnswerCoherenceError, match="veredito"):
        validate_primary_answer_coherence(result.debate_result, judge_result(None), result.editor_result)


# ---------------------------------------------------------------------------
# SAVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [n for n in _mutants() if n != "wrong verdict identity"])
async def test_save_refuses_a_cross_record_invalid_primary_answer_before_any_write(repo, engine, name):
    result, mutant, _ = _mutants()[name]
    if mutant is None:
        pytest.skip("fixture sem limitações para remover")

    with pytest.raises(PrimaryAnswerCoherenceError):
        await repo.save_success(_with_final(result, mutant))

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM council_runs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM final_answers"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_save_refuses_a_primary_answer_whose_attempt_lacks_the_contract(repo):
    result, _ = _with_primary_answer(full_council_run_result())
    attempt = result.editor_result.primary_answer_attempts[0].model_copy(update={"request_provenance": None})
    editor = result.editor_result.model_copy(update={"primary_answer_attempts": [attempt]})

    with pytest.raises(PrimaryAnswerCoherenceError, match="proveniência"):
        await repo.save_success(result.model_copy(update={"editor_result": editor}))


@pytest.mark.asyncio
async def test_valid_round_trip_persists_and_reloads_unchanged(repo):
    result, primary = _with_primary_answer(full_council_run_result())

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer == primary
    assert loaded.editor_result.primary_answer_attempts[0].request_provenance.contract_version == (
        "primary_answer_plan_v1"
    )


@pytest.mark.asyncio
async def test_a_coherent_primary_answer_over_a_revised_claim_round_trips(repo):
    """Revisão explícita legítima: o pai retirado existe no registro, mas a
    seleção referencia só a claim atual."""
    result, _parent = _run_with_retired_parent()
    result, primary = _with_primary_answer(result)

    await repo.save_success(with_recomputed_reconciliation(result))
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer == primary


# ---------------------------------------------------------------------------
# RELOAD (registro persistido adulterado -> fail-closed)
# ---------------------------------------------------------------------------


async def _tamper_primary_json(engine, mutate) -> None:
    async with engine.connect() as conn:
        (raw,) = (await conn.execute(text("SELECT primary_answer_json FROM final_answers"))).one()
    data = json.loads(raw) if isinstance(raw, str) else raw
    data = mutate(data)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE final_answers SET primary_answer_json = :j"), {"j": json.dumps(data)})


def _dump(mutant: PrimaryAnswer):
    return lambda _data: mutant.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "retired-id swap",
        "text drift (coordinated)",
        "verdict-label drift (coordinated)",
        "assessed-count drift (coordinated)",
        "omitted-count drift (coordinated)",
        "limitation drift (coordinated)",
    ],
)
async def test_reload_fails_closed_when_a_persisted_primary_answer_drifted_from_the_run(repo, engine, name):
    result, mutant, _ = _mutants()[name]
    # persiste o registro COERENTE original e adultera só o JSON do Primary Answer
    await repo.save_success(with_recomputed_reconciliation(result))
    await _tamper_primary_json(engine, _dump(mutant))

    with pytest.raises(ValidationError, match="primary_answer|claim|limitations|count"):
        await repo.get_run(result.id)


@pytest.mark.asyncio
async def test_reload_fails_closed_when_the_planning_attempt_lost_its_primary_purpose(repo, engine):
    result, _ = _with_primary_answer(full_council_run_result())
    await repo.save_success(result)
    async with engine.begin() as conn:
        # a tentativa passa a ser lida como do plano de ESTILO (purpose NULL)
        await conn.execute(text("UPDATE editor_attempts SET purpose = NULL WHERE id = 'primary-attempt-1'"))

    with pytest.raises(ValidationError, match="primary_answer"):
        await repo.get_run(result.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("provenance", ['{"contract_version": "editor_v1", "request_digest": "completion-request-sha256-v2:' + "a" * 64 + '"}', None])
async def test_reload_fails_closed_when_the_planning_attempt_has_the_wrong_or_missing_contract(repo, engine, provenance):
    result, _ = _with_primary_answer(full_council_run_result())
    await repo.save_success(result)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE editor_attempts SET request_provenance_json = :p WHERE id = 'primary-attempt-1'"),
            {"p": provenance},
        )

    with pytest.raises(ValidationError, match="proveniência"):
        await repo.get_run(result.id)


@pytest.mark.asyncio
async def test_reload_fails_closed_for_a_wrong_verdict_identity(repo, engine):
    result, _ = _with_primary_answer(full_council_run_result())
    await repo.save_success(result)
    await _tamper_primary_json(
        engine,
        lambda d: _mutant(PrimaryAnswer.model_validate(d), based_on="outro-veredito").model_dump(mode="json"),
    )

    with pytest.raises(ValidationError, match="based_on_verdict_id"):
        await repo.get_run(result.id)


# ---------------------------------------------------------------------------
# Compatibilidade histórica
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_null_primary_answer_and_null_purpose_attempts_keep_loading(repo, engine):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer is None
    assert loaded.editor_result.primary_answer_attempts == []
    assert [a.id for a in loaded.editor_result.attempts] == [a.id for a in result.editor_result.attempts]
    async with engine.connect() as conn:
        purposes = (await conn.execute(text("SELECT purpose FROM editor_attempts"))).scalars().all()
    assert purposes and all(p is None for p in purposes)  # estilo == NULL, como sempre foi


@pytest.mark.asyncio
async def test_an_old_shape_row_survives_the_additive_upgrade_and_public_mapping(tmp_path):
    import sqlite3

    from app.presentation.mappers import completed_run_audit, completed_run_response
    from app.storage.database import create_engine, init_db, make_session_factory
    from app.storage.repository import CouncilRepository

    db_path = str(tmp_path / "old_shape.db")
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    repo = CouncilRepository(make_session_factory(engine))
    result = full_council_run_result()
    await repo.save_success(result)
    await engine.dispose()
    conn = sqlite3.connect(db_path)  # devolve o banco ao formato PRÉ-Primary Answer
    conn.execute("ALTER TABLE final_answers DROP COLUMN primary_answer_json")
    conn.execute("ALTER TABLE editor_attempts DROP COLUMN purpose")
    conn.execute("ALTER TABLE council_runs DROP COLUMN editor_primary_answer_fallback_reason")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)  # upgrade aditivo
    record = await CouncilRepository(make_session_factory(engine)).get_run(result.id)
    await engine.dispose()

    loaded = record.council_run_result
    assert loaded.editor_result.final_answer.primary_answer is None
    assert [a.id for a in loaded.editor_result.attempts] == [a.id for a in result.editor_result.attempts]
    response = completed_run_response(
        loaded, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    audit = completed_run_audit(
        loaded, provider_execution_policy=None, default_model_authority_snapshot=None
    )
    assert response.final_answer.primary_answer is None
    assert audit.primary_answer_attempts == [] and audit.editor_outcome.primary_answer_fallback_reason is None


# ---------------------------------------------------------------------------
# Blocker 1 (closure repair sobre 9464fdf) -- "bind PrimaryAnswer to
# accepted plan": as checagens por-item acima (claim_id elegível,
# claim_text/verdict_label batendo com a avaliação real) provam que CADA
# item é individualmente válido, mas não que a SELEÇÃO/PAPÉIS/ORDEM
# persistidos correspondem exatamente ao plano REALMENTE aceito
# (`primary_answer_attempts[-1].raw_output_text`). Os mutantes abaixo
# passariam INCÓLUMES pelas checagens por-item de cima (cada claim
# referenciada É elegível, com texto/rótulo corretos) -- só a reconstrução
# determinística a partir do plano aceito (adicionada por este repair) os
# pega. Precisam de DUAS claims elegíveis reais (a fixture de 1 claim não
# basta pra "trocar por outra claim elegível" nem "mover pra outro papel
# compatível") -- ver `_two_claim_result` abaixo.
# ---------------------------------------------------------------------------


def _two_claim_result():
    """Execução com DUAS claims elegíveis, ambas `supported` -- a fixture
    padrão de 1 claim não permite exercitar "substituir por outra claim
    elegível"/"mover pra outro papel"/"reordenar" (precisam de pelo menos
    duas). `c2` é uma claim nova e genuína desta execução (não uma
    fabricação de teste sobreposta a um `PrimaryAnswer`) -- avaliada pelo
    Judge como qualquer outra."""
    from uuid import uuid4

    from app.models.domain import ClaimAssessment

    base = full_council_run_result()
    c1 = base.debate_result.claims[0]
    c2 = c1.model_copy(
        update={"id": str(uuid4()), "text": "Segunda claim, também elegível e sustentada."}
    )
    debate = base.debate_result.model_copy(update={"claims": [c1, c2]})
    verdict = base.judge_result.verdict.model_copy(
        update={
            "claim_assessments": [
                *base.judge_result.verdict.claim_assessments,
                ClaimAssessment(claim_id=c2.id, verdict="supported", explanation="Também sustentada."),
            ]
        }
    )
    judge_result = base.judge_result.model_copy(update={"verdict": verdict})
    result = base.model_copy(update={"debate_result": debate, "judge_result": judge_result})
    return with_recomputed_reconciliation(result), c1, c2


def test_a_genuinely_coherent_two_claim_primary_answer_is_accepted():
    """Controle positivo -- prova que o harness de duas claims em si não
    está simplesmente quebrado/rejeitando tudo: uma seleção que CORRESPONDE
    exatamente ao plano aceito é aceita normalmente."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id], "supporting_reasons": [c2.id]}
    result, _primary = _with_primary_answer(result, plan_payload=plan_payload)

    _check(result)  # não levanta


def test_coordinated_mutant_removing_a_selected_claim_is_rejected():
    """Remove `c2` (que o plano aceito realmente selecionou em
    `supporting_reasons`) da seleção persistida, recomputando
    `selected_claim_count`/`rendered_text`/`scope_note` pra continuar
    internamente coerente -- só `central_conclusion=[c1]` sobra. Cada item
    remanescente continua individualmente válido; só a reconstrução do
    plano aceito percebe que uma claim selecionada sumiu."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id], "supporting_reasons": [c2.id]}
    result, primary = _with_primary_answer(result, plan_payload=plan_payload)

    mutant = _mutant(primary, sections=[primary.sections[0]])  # só central_conclusion=[c1]
    assert PrimaryAnswer.model_validate(mutant.model_dump(mode="json")) == mutant  # internamente coerente

    with pytest.raises(PrimaryAnswerCoherenceError, match="reconstru|plano aceito"):
        _check(_with_final(result, mutant))


def test_coordinated_mutant_replacing_a_selected_claim_with_another_eligible_claim_is_rejected():
    """Troca `c1` (selecionado pelo plano aceito) por `c2` -- outra claim
    IGUALMENTE elegível e `supported` desta MESMA execução -- no MESMO
    papel (`central_conclusion`), com `claim_text`/`verdict_label`
    corretos pra `c2` (então cada checagem por-item de cima passa: `c2` É
    elegível, com texto/rótulo reais). Só a reconstrução do plano aceito
    (que continua dizendo `central_conclusion=[c1]`) percebe a troca."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id]}
    result, primary = _with_primary_answer(result, plan_payload=plan_payload)

    mutant = _mutant(
        primary,
        sections=_replace_first_item(
            primary, claim_id=c2.id, claim_text=c2.text, verdict_label="sustentada pelo debate"
        ),
    )
    assert PrimaryAnswer.model_validate(mutant.model_dump(mode="json")) == mutant

    with pytest.raises(PrimaryAnswerCoherenceError, match="reconstru|plano aceito"):
        _check(_with_final(result, mutant))


def test_coordinated_mutant_moving_a_claim_to_another_compatible_role_is_rejected():
    """O plano aceito colocou `c2` em `supporting_reasons`; o mutante move
    a MESMA claim (texto/rótulo intactos, `supported` é compatível com os
    dois papéis) pra `conditions` -- estruturalmente tão válida quanto
    antes, só o PAPEL mudou em relação ao que o plano aceito realmente
    continha."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id], "supporting_reasons": [c2.id]}
    result, primary = _with_primary_answer(result, plan_payload=plan_payload)

    from app.editor.primary_answer import PRIMARY_ANSWER_ROLE_HEADINGS

    moved_section = PrimaryAnswerSection(
        role="conditions",
        heading=PRIMARY_ANSWER_ROLE_HEADINGS["conditions"],
        items=primary.sections[1].items,  # a mesma claim c2, intacta
    )
    mutant = _mutant(primary, sections=[primary.sections[0], moved_section])
    assert PrimaryAnswer.model_validate(mutant.model_dump(mode="json")) == mutant

    with pytest.raises(PrimaryAnswerCoherenceError, match="reconstru|plano aceito"):
        _check(_with_final(result, mutant))


def test_coordinated_mutant_reordering_items_within_a_role_is_rejected():
    """O plano aceito escolheu `central_conclusion=[c1, c2]`, NESSA ordem;
    o mutante persiste as MESMAS duas claims, íntegras, mas em ORDEM
    invertida dentro da mesma seção -- a ordem é semanticamente persistida
    (reflete a ordem do plano aceito, ver `validate_plan`), então este
    repair exige que ela também corresponda."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id, c2.id]}
    result, primary = _with_primary_answer(result, plan_payload=plan_payload)

    original_section = primary.sections[0]
    assert [item.claim_id for item in original_section.items] == [c1.id, c2.id]
    reordered_section = PrimaryAnswerSection(
        role=original_section.role,
        heading=original_section.heading,
        items=tuple(reversed(original_section.items)),
    )
    mutant = _mutant(primary, sections=[reordered_section])
    assert PrimaryAnswer.model_validate(mutant.model_dump(mode="json")) == mutant

    with pytest.raises(PrimaryAnswerCoherenceError, match="reconstru|plano aceito"):
        _check(_with_final(result, mutant))


@pytest.mark.asyncio
async def test_save_refuses_a_coordinated_mutant_that_replaces_the_selected_claim_before_any_write(
    repo, engine
):
    """Mesma disciplina de `test_save_refuses_a_cross_record_invalid_primary_answer_before_any_write`
    (achados anteriores) aplicada ao Blocker 1: `save_success` recusa ANTES
    de qualquer escrita, nenhuma linha fica meio-persistida."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id]}
    result, primary = _with_primary_answer(result, plan_payload=plan_payload)
    mutant = _mutant(
        primary,
        sections=_replace_first_item(
            primary, claim_id=c2.id, claim_text=c2.text, verdict_label="sustentada pelo debate"
        ),
    )

    with pytest.raises(PrimaryAnswerCoherenceError):
        await repo.save_success(_with_final(result, mutant))

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM council_runs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM final_answers"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_a_genuinely_coherent_two_claim_primary_answer_round_trips(repo):
    """Controle positivo de persistência: a seleção real de duas claims
    (correspondendo ao plano aceito) sobrevive save+reload sem alteração --
    o repair não afetou o caminho honesto."""
    result, c1, c2 = _two_claim_result()
    plan_payload = {"central_conclusion": [c1.id], "supporting_reasons": [c2.id]}
    result, primary = _with_primary_answer(result, plan_payload=plan_payload)

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.primary_answer == primary


# ---------------------------------------------------------------------------
# Blocker 2 (closure repair sobre 9464fdf) -- "canonical limitation
# authority": os mutantes de "limitation drift"/"limitation removed" já
# existentes acima (`_mutants()`) só alteravam `primary.limitations`,
# deixando `final_answer.limitations` no valor ORIGINAL -- a checagem
# ANTIGA (primary<->final_answer) já pegava isso. O caso que a checagem
# antiga NUNCA pegava (o gap real que este blocker fecha): os DOIS
# registros mutados EM CONJUNTO, de forma consistente ENTRE SI -- só a
# comparação contra a derivação CANÔNICA (JudgeVerdict.debate_limitations
# + cobertura de extração, app/editor/limitations.py) percebe que nenhum
# dos dois bate com a fonte real.
# ---------------------------------------------------------------------------


def test_coordinated_mutant_forging_limitations_in_both_primary_and_final_answer_together_is_rejected():
    result, primary = _with_primary_answer(full_council_run_result())
    forged_limitations = ("Limitação inteiramente forjada, nunca dita pelo Judge.",)

    mutant_primary = _mutant(primary, limitations=forged_limitations)
    assert PrimaryAnswer.model_validate(mutant_primary.model_dump(mode="json")) == mutant_primary

    final_answer = result.editor_result.final_answer.model_copy(
        update={"primary_answer": mutant_primary, "limitations": list(forged_limitations)}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    tampered = result.model_copy(update={"editor_result": editor})

    with pytest.raises(PrimaryAnswerCoherenceError, match="canôn"):
        _check(tampered)


def test_coordinated_mutant_removing_limitations_from_both_records_together_is_rejected():
    result, primary = _with_primary_answer(full_council_run_result())
    assert primary.limitations  # controle: a fixture tem limitações reais a remover

    mutant_primary = _mutant(primary, limitations=())
    final_answer = result.editor_result.final_answer.model_copy(
        update={"primary_answer": mutant_primary, "limitations": []}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    tampered = result.model_copy(update={"editor_result": editor})

    with pytest.raises(PrimaryAnswerCoherenceError, match="canôn"):
        _check(tampered)


@pytest.mark.asyncio
async def test_save_refuses_a_coordinated_limitation_forgery_across_both_records_before_any_write(
    repo, engine
):
    result, primary = _with_primary_answer(full_council_run_result())
    forged_limitations = ("Limitação inteiramente forjada, nunca dita pelo Judge.",)
    mutant_primary = _mutant(primary, limitations=forged_limitations)
    final_answer = result.editor_result.final_answer.model_copy(
        update={"primary_answer": mutant_primary, "limitations": list(forged_limitations)}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    tampered = result.model_copy(update={"editor_result": editor})

    with pytest.raises(PrimaryAnswerCoherenceError):
        await repo.save_success(tampered)

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM council_runs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM final_answers"))).scalar_one() == 0
