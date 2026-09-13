from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.models.domain import ClaimSupport
from app.storage.records import CompletedRunRecord, QuorumFailureRecord
from app.storage.repository import _run_config_from_json
from tests.storage.fixtures import (
    claim,
    error_model_response,
    full_council_run_result,
    model_response,
    now,
    quorum_failure_exception,
    run_config,
)# ---------------------------------------------------------------------------
# SUCCESS — save + load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_load_completed_run_roundtrip(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.status == "completed"


@pytest.mark.asyncio
async def test_raw_response_text_preserved_exactly(repo):
    result = full_council_run_result()
    original_texts = {r.response_text for r in result.debate_result.initial_result.responses}
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    reloaded_texts = {r.response_text for r in loaded.debate_result.initial_result.responses}

    assert reloaded_texts == original_texts


@pytest.mark.asyncio
async def test_provider_and_effective_model_preserved(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    pairs = {(r.provider, r.model) for r in loaded.debate_result.initial_result.responses}

    assert pairs == {("openai", "gpt-5.5"), ("anthropic", "claude-sonnet-5")}


@pytest.mark.asyncio
async def test_rounds_preserved_initial_and_critique(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert len(loaded.debate_result.initial_result.responses) == 2
    assert loaded.debate_result.critique_round is not None
    assert len(loaded.debate_result.critique_round.round_result.responses) == 1
    assert loaded.debate_result.critique_round.round_result.responses[0].round_number == 2


@pytest.mark.asyncio
async def test_claims_and_claim_support_preserved(repo):
    result = full_council_run_result()
    original_claim = result.debate_result.claims[0]
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    reloaded_claim = loaded.debate_result.claims[0]

    assert reloaded_claim.id == original_claim.id
    assert reloaded_claim.text == original_claim.text
    assert len(reloaded_claim.supporting_model_response_ids) == 2
    assert reloaded_claim.supporting_models == original_claim.supporting_models


@pytest.mark.asyncio
async def test_claim_lineage_parent_and_merged_preserved(repo):
    """Claim de fusão (merged_from_claim_ids) precisa sobreviver ao
    round-trip via a tabela de junção claim_merges."""
    result = full_council_run_result()
    base_claim = result.debate_result.claims[0]
    second_claim = base_claim.model_copy(
        update={"id": "second-source-claim-id", "text": "Segunda claim de origem."}
    )

    merged = base_claim.model_copy(
        update={
            "id": "merged-claim-id",
            "source_model_response_id": None,
            "merged_from_claim_ids": [base_claim.id, second_claim.id],
            "text": "Claim de fusão.",
        }
    )
    debate_result = result.debate_result.model_copy(
        update={"claims": [base_claim, second_claim, merged]}
    )
    result = result.model_copy(update={"debate_result": debate_result})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_merged = next(c for c in loaded.debate_result.claims if c.id == "merged-claim-id")
    assert reloaded_merged.merged_from_claim_ids == [base_claim.id, second_claim.id]
    assert reloaded_merged.source_model_response_id is None


@pytest.mark.asyncio
async def test_claim_processing_attempts_preserved(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert len(loaded.debate_result.claim_processing_attempts) == 1
    attempt = loaded.debate_result.claim_processing_attempts[0]
    original = result.debate_result.claim_processing_attempts[0]
    assert attempt.id == original.id
    assert attempt.raw_output_text == original.raw_output_text
    assert attempt.pricing_provenance == original.pricing_provenance


@pytest.mark.asyncio
async def test_reconciliation_claim_support_scope_model_count_preserved(repo):
    """Cross-round claim reconciliation -- Claim.support_scope_model_count
    precisa sobreviver ao round-trip via a nova coluna nullable de
    `claims` (ver app/storage/models.py)."""
    result = full_council_run_result()
    base_claim = result.debate_result.claims[0]
    round2_claim = base_claim.model_copy(
        update={"id": "round2-source-claim-id", "round_introduced": 2, "text": "Claim da rodada de crítica."}
    )
    reconciled = base_claim.model_copy(
        update={
            "id": "reconciled-claim-id",
            "source_model_response_id": None,
            "merged_from_claim_ids": [base_claim.id, round2_claim.id],
            "round_introduced": 2,
            "total_models_in_round": 2,
            "support_scope_model_count": 4,
            "text": "Claim reconciliada entre Round 1 e Round 2.",
        }
    )
    debate_result = result.debate_result.model_copy(
        update={"claims": [base_claim, round2_claim, reconciled]}
    )
    result = result.model_copy(update={"debate_result": debate_result})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(c for c in loaded.debate_result.claims if c.id == "reconciled-claim-id")
    assert reloaded.support_scope_model_count == 4
    assert reloaded.total_models_in_round == 2
    # denominador efetivo pós-reload é support_scope_model_count (4), nunca total_models_in_round (2)
    assert reloaded.supporting_model_ratio == len(reloaded.supporting_models) / 4


@pytest.mark.asyncio
async def test_ordinary_claim_support_scope_model_count_roundtrips_as_none(repo):
    """Claim ordinária (nunca reconciliada) preserva
    support_scope_model_count=None através do round-trip -- nenhum valor
    inventado onde não existia."""
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    reloaded_claim = loaded.debate_result.claims[0]

    assert reloaded_claim.support_scope_model_count is None


@pytest.mark.asyncio
async def test_reconciliation_operation_roundtrips_through_persistence(repo):
    """Cross-round claim reconciliation -- ClaimProcessingAttempt com
    operation='reconciliation' precisa sobreviver ao round-trip, distinto
    de 'grouping', e round_number=2 preservado."""
    result = full_council_run_result()
    original_attempt = result.debate_result.claim_processing_attempts[0]
    reconciliation_attempt = original_attempt.model_copy(
        update={
            "id": "reconciliation-attempt-id",
            "operation": "reconciliation",
            "round_number": 2,
            "target_model_response_id": None,
            "target_claim_ids": ["r1-claim", "r2-claim"],
            "raw_output_text": '{"groups": [], "ungrouped_claim_ids": ["r1-claim", "r2-claim"]}',
        }
    )
    debate_result = result.debate_result.model_copy(
        update={
            "claim_processing_attempts": [original_attempt, reconciliation_attempt],
        }
    )
    result = result.model_copy(update={"debate_result": debate_result})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(
        a
        for a in loaded.debate_result.claim_processing_attempts
        if a.id == "reconciliation-attempt-id"
    )
    assert reloaded.operation == "reconciliation"
    assert reloaded.round_number == 2
    assert reloaded.target_claim_ids == ["r1-claim", "r2-claim"]
    # o attempt "grouping" original continua presente e inalterado
    other_operations = {
        a.operation for a in loaded.debate_result.claim_processing_attempts
    }
    assert other_operations == {"extraction", "reconciliation"}


@pytest.mark.asyncio
async def test_judge_verdict_and_claim_assessments_preserved(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.judge_result.verdict is not None
    assert loaded.judge_result.verdict.reasoning == result.judge_result.verdict.reasoning
    assert loaded.judge_result.verdict.best_arguments_by == result.judge_result.verdict.best_arguments_by
    assert len(loaded.judge_result.verdict.claim_assessments) == 1
    assert (
        loaded.judge_result.verdict.claim_assessments[0].verdict
        == result.judge_result.verdict.claim_assessments[0].verdict
    )


@pytest.mark.asyncio
async def test_final_answer_and_limitations_preserved(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.answer_text == result.editor_result.final_answer.answer_text
    assert loaded.editor_result.final_answer.limitations == result.editor_result.final_answer.limitations
    assert loaded.editor_result.final_answer.based_on_verdict_id == result.judge_result.verdict.id


@pytest.mark.asyncio
async def test_timestamps_preserved(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.started_at == result.started_at
    assert loaded.completed_at == result.completed_at


@pytest.mark.asyncio
async def test_none_cost_stays_none_after_roundtrip(repo):
    error_mr = error_model_response("gemini")
    assert error_mr.cost_usd is None
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, error_mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_error = next(
        r for r in loaded.debate_result.initial_result.responses if r.status == "error"
    )
    assert reloaded_error.cost_usd is None
    assert reloaded_error.pricing_provenance is None


@pytest.mark.asyncio
async def test_zero_cost_stays_zero_after_roundtrip(repo):
    zero_mr = model_response("gemini", model="local-model", cost_usd=0.0)
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, zero_mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_zero = next(
        r for r in loaded.debate_result.initial_result.responses if r.model == "local-model"
    )
    assert reloaded_zero.cost_usd == 0.0
    assert reloaded_zero.cost_usd is not None


@pytest.mark.asyncio
async def test_unknown_accounting_reconstructed_correctly(repo):
    """has_unknown_accounting_components é recalculado via
    sum_usage_and_cost na leitura, não uma coluna própria — precisa bater
    com o valor original mesmo assim."""
    error_mr = error_model_response("gemini")
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={
            "responses": [*result.debate_result.initial_result.responses, error_mr],
            "has_unknown_accounting_components": True,
        }
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.debate_result.initial_result.has_unknown_accounting_components is True
    assert loaded.has_unknown_accounting_components is True


@pytest.mark.asyncio
async def test_pricing_provenance_preserved_with_tier(repo):
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    reloaded = loaded.debate_result.initial_result.responses[0]
    original = result.debate_result.initial_result.responses[0]

    assert reloaded.pricing_provenance == original.pricing_provenance
    assert reloaded.pricing_provenance.source_id == "test-pricing-table"
    assert reloaded.pricing_provenance.tier == "standard"


@pytest.mark.asyncio
async def test_computed_totals_not_stored_as_canonical_still_match(repo):
    """total_input_tokens/total_output_tokens/total_cost_usd nunca têm
    coluna própria — são sempre recalculados a partir dos registros-fato,
    e ainda assim precisam bater com o valor original."""
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.total_input_tokens == result.total_input_tokens
    assert loaded.total_output_tokens == result.total_output_tokens
    assert loaded.total_cost_usd == pytest.approx(result.total_cost_usd)


@pytest.mark.asyncio
async def test_get_run_returns_none_for_unknown_id(repo):
    assert await repo.get_run("id-que-nao-existe") is None


# ---------------------------------------------------------------------------
# QUORUM FAILURE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_load_quorum_failure_roundtrip(repo):
    exc = quorum_failure_exception()
    rc = run_config()
    started, failed = now(), now()

    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=started, failed_at=failed
    )
    loaded = await repo.get_run(failure_id)

    assert isinstance(loaded, QuorumFailureRecord)
    assert loaded.status == "insufficient_quorum"
    assert loaded.successful_count == 1
    assert loaded.total_providers == 2
    assert loaded.min_to_return == 2


@pytest.mark.asyncio
async def test_quorum_failure_preserves_already_produced_responses(repo):
    """O caso central da Etapa 10: respostas que já geraram custo real
    não podem desaparecer só porque o quórum falhou."""
    exc = quorum_failure_exception()
    rc = run_config()

    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )
    loaded = await repo.get_run(failure_id)

    assert len(loaded.round_result.responses) == 2
    successful = [r for r in loaded.round_result.responses if r.status == "success"]
    assert len(successful) == 1
    assert successful[0].cost_usd is not None
    assert successful[0].cost_usd > 0


@pytest.mark.asyncio
async def test_quorum_failure_accounting_preserved(repo):
    exc = quorum_failure_exception()
    rc = run_config()

    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )
    loaded = await repo.get_run(failure_id)

    assert loaded.round_result.has_unknown_accounting_components is True
    assert loaded.round_result.total_input_tokens == 100
    assert loaded.round_result.total_output_tokens == 20


@pytest.mark.asyncio
async def test_quorum_failure_run_config_snapshot_preserved(repo):
    exc = quorum_failure_exception()
    rc = run_config(question="Pergunta específica de teste")

    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )
    loaded = await repo.get_run(failure_id)

    assert loaded.run_config.question == "Pergunta específica de teste"


# ---------------------------------------------------------------------------
# Etapa 17A.2 — compatibilidade com run_config_json persistido ANTES da
# adição de max_output_tokens_grouping/max_output_tokens_judge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_run_config_json_missing_grouping_judge_ceilings_backfills_from_general(
    engine, repo
):
    """`run_config_json` é um blob JSON completo (Decision Delta) -- um
    run salvo ANTES da Etapa 17A.2 não tem as duas chaves novas. A
    reconstrução precisa preencher as duas com o valor de
    `max_output_tokens_per_call` DAQUELE MESMO run (o que realmente
    aconteceu naquela execução -- agrupamento/Judge usavam esse único
    teto antes da Etapa 17A.2), nunca com o novo default global (8192)
    de runs futuros."""
    result = full_council_run_result(run_config=run_config(max_output_tokens_per_call=2048))
    await repo.save_success(result)

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT run_config_json FROM council_runs WHERE id = :id"),
                {"id": result.id},
            )
        ).scalar_one()
        data = json.loads(raw)
        assert "max_output_tokens_grouping" in data  # save normal já grava as chaves novas
        assert "max_output_tokens_judge" in data
        del data["max_output_tokens_grouping"]
        del data["max_output_tokens_judge"]
        await conn.execute(
            text("UPDATE council_runs SET run_config_json = :json WHERE id = :id"),
            {"json": json.dumps(data), "id": result.id},
        )

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    rc = loaded.council_run_result.run_config
    assert rc.max_output_tokens_per_call == 2048
    assert rc.max_output_tokens_grouping == 2048  # backfill honesto, não o default global (8192)
    assert rc.max_output_tokens_judge == 2048


@pytest.mark.asyncio
async def test_native_stage17a2_run_config_ceilings_are_never_overwritten_by_backfill(
    engine, repo
):
    """Um blob que JÁ tem as chaves novas (run nativo da Etapa 17A.2 em
    diante, com valores DIFERENTES do geral) nunca é alterado pelo
    backfill -- o backfill só age quando a chave está genuinamente
    ausente."""
    result = full_council_run_result(
        run_config=run_config(
            max_output_tokens_per_call=1024,
            max_output_tokens_grouping=8192,
            max_output_tokens_judge=6000,
        )
    )
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    rc = loaded.council_run_result.run_config
    assert rc.max_output_tokens_per_call == 1024
    assert rc.max_output_tokens_grouping == 8192
    assert rc.max_output_tokens_judge == 6000


@pytest.mark.asyncio
async def test_legacy_quorum_failure_run_config_json_backfills_from_general(engine, repo):
    """Mesmo backfill do caminho de sucesso, aplicado ao registro de
    falha de quórum (tabela separada, mesma forma de blob JSON)."""
    exc = quorum_failure_exception()
    rc = run_config(max_output_tokens_per_call=3000)
    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT run_config_json FROM quorum_failures WHERE id = :id"),
                {"id": failure_id},
            )
        ).scalar_one()
        data = json.loads(raw)
        del data["max_output_tokens_grouping"]
        del data["max_output_tokens_judge"]
        await conn.execute(
            text("UPDATE quorum_failures SET run_config_json = :json WHERE id = :id"),
            {"json": json.dumps(data), "id": failure_id},
        )

    loaded = await repo.get_run(failure_id)

    assert isinstance(loaded, QuorumFailureRecord)
    assert loaded.run_config.max_output_tokens_grouping == 3000
    assert loaded.run_config.max_output_tokens_judge == 3000


# ---------------------------------------------------------------------------
# Clarificação de contrato de execução (pós-run real) -- compatibilidade
# com run_config_json persistido ANTES do rename de
# overall_timeout_seconds para round_dispatch_timeout_seconds.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_run_config_json_with_old_timeout_key_name_is_readable(engine, repo):
    """Um run salvo ANTES da renomeação tem a chave antiga
    `overall_timeout_seconds` (nunca `round_dispatch_timeout_seconds`)
    no blob persistido -- a reconstrução precisa RENOMEAR a chave (nunca
    reinterpretar/recalcular o VALOR, que continua exatamente o número
    registrado naquela execução)."""
    result = full_council_run_result(run_config=run_config(round_dispatch_timeout_seconds=77.0))
    await repo.save_success(result)

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT run_config_json FROM council_runs WHERE id = :id"),
                {"id": result.id},
            )
        ).scalar_one()
        data = json.loads(raw)
        assert "round_dispatch_timeout_seconds" in data  # save normal já grava a chave nova
        data["overall_timeout_seconds"] = data.pop("round_dispatch_timeout_seconds")
        await conn.execute(
            text("UPDATE council_runs SET run_config_json = :json WHERE id = :id"),
            {"json": json.dumps(data), "id": result.id},
        )

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    rc = loaded.council_run_result.run_config
    assert rc.round_dispatch_timeout_seconds == 77.0  # valor preservado, só a chave mudou
    assert not hasattr(rc, "overall_timeout_seconds")


@pytest.mark.asyncio
async def test_native_run_config_json_with_new_timeout_key_is_never_overwritten_by_backfill(
    engine, repo
):
    """Um blob que JÁ tem a chave nova (run nativo desta renomeação em
    diante) nunca é alterado pelo backfill de rename -- ele só age
    quando a chave nova está genuinamente ausente E a antiga presente."""
    result = full_council_run_result(run_config=run_config(round_dispatch_timeout_seconds=33.0))
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    rc = loaded.council_run_result.run_config
    assert rc.round_dispatch_timeout_seconds == 33.0


@pytest.mark.asyncio
async def test_legacy_quorum_failure_run_config_json_with_old_timeout_key_is_readable(
    engine, repo
):
    """Mesmo backfill de rename, aplicado ao registro de falha de
    quórum (tabela separada, mesma forma de blob JSON)."""
    exc = quorum_failure_exception()
    rc = run_config(round_dispatch_timeout_seconds=15.0)
    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT run_config_json FROM quorum_failures WHERE id = :id"),
                {"id": failure_id},
            )
        ).scalar_one()
        data = json.loads(raw)
        data["overall_timeout_seconds"] = data.pop("round_dispatch_timeout_seconds")
        await conn.execute(
            text("UPDATE quorum_failures SET run_config_json = :json WHERE id = :id"),
            {"json": json.dumps(data), "id": failure_id},
        )

    loaded = await repo.get_run(failure_id)

    assert isinstance(loaded, QuorumFailureRecord)
    assert loaded.run_config.round_dispatch_timeout_seconds == 15.0


# ---------------------------------------------------------------------------
# Correção independente de revisão -- _run_config_from_json() falhava com
# extra_forbidden quando o blob tinha AS DUAS chaves (legada +
# canônica): a legada só era removida quando a canônica estava AUSENTE.
# A canônica precisa ser autoritativa sempre que as duas existirem,
# nunca um erro de conflito.
# ---------------------------------------------------------------------------


def test_run_config_from_json_legacy_key_only_renames_to_canonical():
    """1 -- unitário, direto na função: só a chave legada presente."""
    data = _minimal_run_config_json(overall_timeout_seconds=55.0)
    rc = _run_config_from_json(data)
    assert rc.round_dispatch_timeout_seconds == 55.0


def test_run_config_from_json_canonical_key_only_loads_unchanged():
    """2 -- unitário, direto na função: só a chave canônica presente."""
    data = _minimal_run_config_json(round_dispatch_timeout_seconds=88.0)
    rc = _run_config_from_json(data)
    assert rc.round_dispatch_timeout_seconds == 88.0


def test_run_config_from_json_both_keys_equal_values_canonical_wins():
    """3 -- as duas chaves presentes, mesmo valor -- carrega com
    sucesso (antes desta correção: ValidationError extra_forbidden,
    mesmo os dois valores sendo idênticos)."""
    data = _minimal_run_config_json(
        round_dispatch_timeout_seconds=120.0, overall_timeout_seconds=120.0
    )
    rc = _run_config_from_json(data)
    assert rc.round_dispatch_timeout_seconds == 120.0


def test_run_config_from_json_both_keys_conflicting_values_canonical_wins():
    """4 -- as duas chaves presentes, valores DIFERENTES -- carrega com
    sucesso usando a canônica; a legada nunca chega a
    RunConfig/validação, nunca um erro de conflito."""
    data = _minimal_run_config_json(
        round_dispatch_timeout_seconds=120.0, overall_timeout_seconds=999.0
    )
    rc = _run_config_from_json(data)
    assert rc.round_dispatch_timeout_seconds == 120.0


def test_run_config_from_json_does_not_mutate_source_dict_with_both_keys():
    """5 -- o dict original (que pode ser reusado/inspecionado pelo
    chamador) permanece intacto, com as DUAS chaves originais, mesmo
    quando a legada é descartada na reconstrução."""
    data = _minimal_run_config_json(
        round_dispatch_timeout_seconds=120.0, overall_timeout_seconds=999.0
    )
    original = dict(data)

    _run_config_from_json(data)

    assert data == original
    assert data["round_dispatch_timeout_seconds"] == 120.0
    assert data["overall_timeout_seconds"] == 999.0


def test_run_config_from_json_neither_key_still_fails_as_before():
    """8 -- comportamento preservado: sem NENHUMA das duas chaves,
    `round_dispatch_timeout_seconds` continua um campo obrigatório sem
    default -- nenhum valor é inventado durante a reconstrução
    histórica."""
    data = _minimal_run_config_json()
    with pytest.raises(ValidationError):
        _run_config_from_json(data)


@pytest.mark.asyncio
async def test_successful_run_reconstruction_uses_repaired_both_keys_behavior(engine, repo):
    """6 -- integração fim-a-fim: um run de SUCESSO persistido com as
    duas chaves no blob (equivalente a uma leitura+escrita intermediária
    durante a janela de transição) volta a carregar corretamente pelo
    caminho real do repositório, não só pela função isolada."""
    result = full_council_run_result(run_config=run_config(round_dispatch_timeout_seconds=42.0))
    await repo.save_success(result)

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT run_config_json FROM council_runs WHERE id = :id"),
                {"id": result.id},
            )
        ).scalar_one()
        data = json.loads(raw)
        data["overall_timeout_seconds"] = 777.0  # legada conflitante, adicionada de propósito
        await conn.execute(
            text("UPDATE council_runs SET run_config_json = :json WHERE id = :id"),
            {"json": json.dumps(data), "id": result.id},
        )

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.council_run_result.run_config.round_dispatch_timeout_seconds == 42.0


@pytest.mark.asyncio
async def test_quorum_failure_reconstruction_uses_repaired_both_keys_behavior(engine, repo):
    """7 -- mesma prova de integração, agora pro registro de falha de
    quórum (tabela separada, mesmo `_run_config_from_json` compartilhado
    -- nenhuma lógica de normalização duplicada)."""
    exc = quorum_failure_exception()
    rc = run_config(round_dispatch_timeout_seconds=24.0)
    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT run_config_json FROM quorum_failures WHERE id = :id"),
                {"id": failure_id},
            )
        ).scalar_one()
        data = json.loads(raw)
        data["overall_timeout_seconds"] = 888.0  # legada conflitante, adicionada de propósito
        await conn.execute(
            text("UPDATE quorum_failures SET run_config_json = :json WHERE id = :id"),
            {"json": json.dumps(data), "id": failure_id},
        )

    loaded = await repo.get_run(failure_id)

    assert isinstance(loaded, QuorumFailureRecord)
    assert loaded.run_config.round_dispatch_timeout_seconds == 24.0


def _minimal_run_config_json(**timeout_keys) -> dict:
    """Blob mínimo válido de RunConfig, exceto pelas chaves de timeout
    (legada/canônica) que o chamador injeta explicitamente -- reusado
    pelos testes unitários de `_run_config_from_json` acima, que testam
    a normalização isoladamente, sem precisar de banco."""
    return {
        "question": "pergunta",
        "enabled_providers": ["openai"],
        "max_cost_usd": 1.0,
        "max_total_tokens": 50_000,
        "max_output_tokens_per_call": 1024,
        "max_output_tokens_grouping": 1024,
        "max_output_tokens_judge": 1024,
        "quorum": {"min_for_debate": 1, "min_to_return": 1},
        "claim_processor_provider": "anthropic",
        "judge_provider": "anthropic",
        "editor_provider": "anthropic",
        "source_analyzer_provider": "anthropic",
        "source_text": None,
        **timeout_keys,
    }


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_includes_both_kinds(repo):
    completed = full_council_run_result()
    await repo.save_success(completed)

    exc = quorum_failure_exception()
    failure_id = await repo.save_quorum_failure(
        exc, run_config=run_config(), started_at=now(), failed_at=now()
    )

    summaries = await repo.list_runs()
    ids_and_statuses = {(s.id, s.status) for s in summaries}

    assert (completed.id, "completed") in ids_and_statuses
    assert (failure_id, "insufficient_quorum") in ids_and_statuses


@pytest.mark.asyncio
async def test_list_runs_respects_limit(repo):
    for _ in range(3):
        await repo.save_success(full_council_run_result())

    summaries = await repo.list_runs(limit=2)
    assert len(summaries) == 2


# ---------------------------------------------------------------------------
# Atomicidade — rollback completo em falha simulada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_success_rolls_back_completely_on_failure(repo):
    """Se qualquer parte do save falhar, NADA daquela execução fica
    gravado — nem o CouncilRunRow, nem nenhum ModelResponse/Claim/etc.
    já adicionados à sessão antes do ponto de falha."""
    result = full_council_run_result()

    # Corrompe deliberadamente um dos ModelResponse pra forçar uma
    # violação de integridade referencial na hora do commit (Claim
    # aponta pra um source_model_response_id que não existe entre as
    # ModelResponse reais sendo salvas).
    bad_claim = result.debate_result.claims[0].model_copy(
        update={"source_model_response_id": "id-que-nao-existe-em-nenhum-model-response"}
    )
    debate_result = result.debate_result.model_copy(update={"claims": [bad_claim]})
    result = result.model_copy(update={"debate_result": debate_result})

    with pytest.raises(Exception):
        await repo.save_success(result)

    # nada foi persistido — nem o council_run em si
    assert await repo.get_run(result.id) is None


@pytest.mark.asyncio
async def test_save_quorum_failure_rolls_back_completely_on_failure(repo):
    """Duas ModelResponse com o MESMO id na mesma falha de quórum violam
    a PK de model_responses -- força um erro de integridade real na hora
    do commit, sem precisar mockar nada."""
    from app.orchestrator.result import RoundResult

    duplicated_id = "resposta-duplicada-de-proposito"
    r1 = model_response("openai", id=duplicated_id)
    r2 = model_response("anthropic", id=duplicated_id)
    round_result = RoundResult(
        round_number=1,
        responses=[r1, r2],
        successful_count=2,
        total_participants=2,
        total_input_tokens=200,
        total_output_tokens=40,
        total_cost_usd=0.002,
        has_unknown_accounting_components=False,
    )
    exc = quorum_failure_exception(round_result=round_result)

    with pytest.raises(Exception):
        await repo.save_quorum_failure(
            exc, run_config=run_config(), started_at=now(), failed_at=now()
        )

    summaries = await repo.list_runs()
    assert summaries == []


# ---------------------------------------------------------------------------
# PATCH DE AUDITABILIDADE — usage_present distingue usage=None de
# TokenUsage(None, None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_none_roundtrips_as_none(repo):
    mr = model_response("openai", usage=None, cost_usd=None, pricing_provenance=None)
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(r for r in loaded.debate_result.initial_result.responses if r.id == mr.id)
    assert reloaded.usage is None


@pytest.mark.asyncio
async def test_usage_none_none_roundtrips_as_token_usage_object_not_none(repo):
    """O caso central do patch: TokenUsage(None, None) (ex.: Gemini sem
    usage_metadata) NÃO pode colidir com usage=None no round-trip."""
    from app.models.provider_models import TokenUsage

    mr = model_response(
        "gemini",
        model="gemini-3.7-flash",
        usage=TokenUsage(input_tokens=None, output_tokens=None),
        cost_usd=None,
        pricing_provenance=None,
    )
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(r for r in loaded.debate_result.initial_result.responses if r.id == mr.id)
    assert reloaded.usage is not None
    assert reloaded.usage.input_tokens is None
    assert reloaded.usage.output_tokens is None


@pytest.mark.asyncio
async def test_usage_input_known_output_none_roundtrips_correctly(repo):
    from app.models.provider_models import TokenUsage

    mr = model_response(
        "gemini",
        model="gemini-3.7-flash",
        usage=TokenUsage(input_tokens=10, output_tokens=None),
        cost_usd=None,
        pricing_provenance=None,
    )
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(r for r in loaded.debate_result.initial_result.responses if r.id == mr.id)
    assert reloaded.usage.input_tokens == 10
    assert reloaded.usage.output_tokens is None


@pytest.mark.asyncio
async def test_usage_input_none_output_known_roundtrips_correctly(repo):
    from app.models.provider_models import TokenUsage

    mr = model_response(
        "gemini",
        model="gemini-3.7-flash",
        usage=TokenUsage(input_tokens=None, output_tokens=20),
        cost_usd=None,
        pricing_provenance=None,
    )
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(r for r in loaded.debate_result.initial_result.responses if r.id == mr.id)
    assert reloaded.usage.input_tokens is None
    assert reloaded.usage.output_tokens == 20


@pytest.mark.asyncio
async def test_usage_zero_zero_roundtrips_as_known_zero(repo):
    from app.models.provider_models import TokenUsage

    mr = model_response(
        "local", model="self-hosted", usage=TokenUsage(input_tokens=0, output_tokens=0),
        cost_usd=0.0,
    )
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(r for r in loaded.debate_result.initial_result.responses if r.id == mr.id)
    assert reloaded.usage is not None
    assert reloaded.usage.input_tokens == 0
    assert reloaded.usage.output_tokens == 0


# ---------------------------------------------------------------------------
# PATCH DE AUDITABILIDADE — EvidenceRef JSON-safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_ref_saves_and_loads_without_json_error(repo):
    from app.models.domain import EvidenceRef

    result = full_council_run_result()
    base_claim = result.debate_result.claims[0]
    evidence = EvidenceRef(
        claim_id=base_claim.id,
        source_url="https://exemplo.com/fonte",
        summary="Fonte externa confirmando a claim.",
        verification_method="web_search",
    )
    claim_with_evidence = base_claim.model_copy(update={"external_evidence": evidence})
    debate = result.debate_result.model_copy(update={"claims": [claim_with_evidence]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)  # não pode levantar TypeError de JSON
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_claim = loaded.debate_result.claims[0]
    assert reloaded_claim.external_evidence is not None
    assert reloaded_claim.external_evidence.id == evidence.id
    assert reloaded_claim.external_evidence.claim_id == base_claim.id
    assert reloaded_claim.external_evidence.source_url == "https://exemplo.com/fonte"
    assert reloaded_claim.external_evidence.summary == evidence.summary
    assert reloaded_claim.external_evidence.verification_method == "web_search"
    assert reloaded_claim.external_evidence.verified_at.tzinfo is not None


# ---------------------------------------------------------------------------
# ROUND-TRIP FORTE — comparação estrutural completa, sem esconder
# diferenças usando sets/contagem/seleção parcial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_fidelity_roundtrip_matches_original_model_dump(repo):
    """Fixture rica cobrindo: >=2 responses/claims/claim_processing_
    attempts/JudgeAttempts/EditorAttempts/ClaimAssessments em ordem
    conhecida, critique round, EvidenceRef, TokenUsage(None, None),
    pricing provenance, timestamps. model_dump() antes/depois precisa
    bater exatamente — não uma comparação parcial."""
    from tests.storage.fixtures import very_rich_council_run_result

    result = very_rich_council_run_result()
    original_dump = result.model_dump()

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result
    reloaded_dump = loaded.model_dump()

    assert reloaded_dump == original_dump


# ---------------------------------------------------------------------------
# Etapa 13 (T03.A) — requested_model preservado no round-trip de
# persistência, DIVERGINDO deliberadamente de `model` em cada fixture
# (nunca igual por acidente) — prova que as duas colunas são
# independentes no schema, não apenas uma cópia uma da outra.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_response_requested_model_roundtrip(repo):
    """Cenário 8: requested_model de um ModelResponse sobrevive ao
    round-trip, distinto de `model` (provider-reported)."""
    result = full_council_run_result()
    mr1 = result.debate_result.initial_result.responses[0]
    diverged = mr1.model_copy(
        update={"requested_model": "gpt-5.5-latest", "model": "gpt-5.5-2025-06-15"}
    )
    responses = [diverged] + result.debate_result.initial_result.responses[1:]
    initial = result.debate_result.initial_result.model_copy(update={"responses": responses})
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(
        r for r in loaded.debate_result.initial_result.responses if r.id == diverged.id
    )
    assert reloaded.requested_model == "gpt-5.5-latest"
    assert reloaded.model == "gpt-5.5-2025-06-15"
    assert reloaded.requested_model != reloaded.model


@pytest.mark.asyncio
async def test_claim_processing_attempt_requested_model_roundtrip(repo):
    """Cenário 9: idem, para ClaimProcessingAttempt."""
    result = full_council_run_result()
    original = result.debate_result.claim_processing_attempts[0]
    diverged = original.model_copy(
        update={"requested_model": "claude-sonnet-5-latest", "model": "claude-sonnet-5-20250601"}
    )
    debate = result.debate_result.model_copy(update={"claim_processing_attempts": [diverged]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.claim_processing_attempts[0]
    assert reloaded.requested_model == "claude-sonnet-5-latest"
    assert reloaded.model == "claude-sonnet-5-20250601"
    assert reloaded.requested_model != reloaded.model


@pytest.mark.asyncio
async def test_judge_attempt_requested_model_roundtrip(repo):
    """Cenário 10: idem, para JudgeAttempt."""
    result = full_council_run_result()
    original = result.judge_result.attempts[0]
    diverged = original.model_copy(
        update={"requested_model": "claude-sonnet-5-latest", "model": "claude-sonnet-5-20250601"}
    )
    judge = result.judge_result.model_copy(update={"attempts": [diverged]})
    result = result.model_copy(update={"judge_result": judge})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.judge_result.attempts[0]
    assert reloaded.requested_model == "claude-sonnet-5-latest"
    assert reloaded.model == "claude-sonnet-5-20250601"
    assert reloaded.requested_model != reloaded.model


@pytest.mark.asyncio
async def test_editor_attempt_requested_model_roundtrip(repo):
    """Cenário 11: idem, para EditorAttempt."""
    result = full_council_run_result()
    original = result.editor_result.attempts[0]
    diverged = original.model_copy(
        update={"requested_model": "claude-sonnet-5-latest", "model": "claude-sonnet-5-20250601"}
    )
    editor = result.editor_result.model_copy(update={"attempts": [diverged]})
    result = result.model_copy(update={"editor_result": editor})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.editor_result.attempts[0]
    assert reloaded.requested_model == "claude-sonnet-5-latest"
    assert reloaded.model == "claude-sonnet-5-20250601"
    assert reloaded.requested_model != reloaded.model


# ---------------------------------------------------------------------------
# Etapa 15 — round-trip de persistência de DeterministicVerificationAttempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_verification_supports_roundtrip(repo):
    from app.debate.numeric_verification import build_verification_attempt

    result = full_council_run_result()
    claim = result.debate_result.claims[0]
    attempt = build_verification_attempt(
        claim.id, {"left": "2", "operator": "+", "right": "2", "asserted_result": "4"}
    )
    debate = result.debate_result.model_copy(update={"numeric_verification_attempts": [attempt]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.numeric_verification_attempts[0]
    assert reloaded.state == "supports"
    assert reloaded.claim_id == claim.id
    assert reloaded.assertion.left == "2"
    assert reloaded.assertion.operator == "+"
    assert reloaded.computed_result == "4"
    assert reloaded.raw_proposal is None


@pytest.mark.asyncio
async def test_deterministic_verification_contradicts_roundtrip(repo):
    from app.debate.numeric_verification import build_verification_attempt

    result = full_council_run_result()
    claim = result.debate_result.claims[0]
    attempt = build_verification_attempt(
        claim.id, {"left": "2", "operator": "+", "right": "2", "asserted_result": "5"}
    )
    debate = result.debate_result.model_copy(update={"numeric_verification_attempts": [attempt]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.numeric_verification_attempts[0]
    assert reloaded.state == "contradicts"
    assert reloaded.computed_result == "4"  # o valor CALCULADO, não o alegado


@pytest.mark.asyncio
async def test_deterministic_verification_invalid_proposal_roundtrip_preserves_raw_payload(repo):
    from app.debate.numeric_verification import build_verification_attempt

    result = full_council_run_result()
    claim = result.debate_result.claims[0]
    raw = {"left": "2", "operator": "não é um operador válido", "right": "2", "asserted_result": "4"}
    attempt = build_verification_attempt(claim.id, raw)
    assert attempt.state == "invalid_proposal"
    debate = result.debate_result.model_copy(update={"numeric_verification_attempts": [attempt]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.numeric_verification_attempts[0]
    assert reloaded.state == "invalid_proposal"
    assert reloaded.raw_proposal == raw
    assert reloaded.assertion is None
    assert reloaded.computed_result is None


@pytest.mark.asyncio
async def test_deterministic_verification_computation_failed_roundtrip(repo):
    from app.debate.numeric_verification import build_verification_attempt

    result = full_council_run_result()
    claim = result.debate_result.claims[0]
    attempt = build_verification_attempt(
        claim.id, {"left": "5", "operator": "/", "right": "0", "asserted_result": "0"}
    )
    assert attempt.state == "computation_failed"
    debate = result.debate_result.model_copy(update={"numeric_verification_attempts": [attempt]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.numeric_verification_attempts[0]
    assert reloaded.state == "computation_failed"
    assert reloaded.assertion is not None  # a asserção em si era válida
    assert reloaded.computed_result is None


@pytest.mark.asyncio
async def test_deterministic_verification_attempts_preserve_exact_list_order(repo):
    """Patch de revisão independente (Issue 1): a ordem de retorno do SQL
    NUNCA é contrato de persistência -- IDs/claim_ids deliberadamente
    desalinhados lexicograficamente da ordem da lista, pra garantir que
    o teste falharia se a reconstrução dependesse de ordenação por
    chave primária ou de inserção implícita em vez de `position`
    explícito."""
    from app.debate.numeric_verification import build_verification_attempt

    result = full_council_run_result()
    debate = result.debate_result
    real_response_id = debate.initial_result.responses[0].id
    support = ClaimSupport(model_response_id=real_response_id, provider="openai", model="gpt-5.5")
    # 3 claims brutas extras, com ids que o SQLite ordenaria de forma
    # DIFERENTE da ordem lógica desejada (position 0, 1, 2).
    c_z = claim(real_response_id, [support], text="zzz-claim", status="active")
    c_a = claim(real_response_id, [support], text="aaa-claim", status="active")
    c_m = claim(real_response_id, [support], text="mmm-claim", status="active")

    v_z = build_verification_attempt(
        c_z.id, {"left": "1", "operator": "+", "right": "1", "asserted_result": "2"}
    )
    v_a = build_verification_attempt(
        c_a.id, {"left": "2", "operator": "+", "right": "2", "asserted_result": "4"}
    )
    v_m = build_verification_attempt(
        c_m.id, {"left": "3", "operator": "+", "right": "3", "asserted_result": "6"}
    )
    # Ordem lógica desejada: z, a, m -- deliberadamente NÃO alfabética,
    # NÃO na ordem de criação dos objetos Claim, e os ids de
    # DeterministicVerificationAttempt (gerados via uuid4, aleatórios)
    # também não têm relação nenhuma com essa ordem.
    ordered_attempts = [v_z, v_a, v_m]

    debate = debate.model_copy(
        update={
            "claims": debate.claims + [c_z, c_a, c_m],
            "numeric_verification_attempts": ordered_attempts,
        }
    )
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_claim_ids = [a.claim_id for a in loaded.debate_result.numeric_verification_attempts]
    assert reloaded_claim_ids == [c_z.id, c_a.id, c_m.id]


# ---------------------------------------------------------------------------
# Etapa 17A (B1) — Run com falha de transporte real (sem API key) continua
# persistível de ponta a ponta.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_real_zero_transport_attempt_remains_persistible(repo):
    """Antes da correção B1, um JudgeAttempt real com
    transport_attempts=0 (produzido por um provider real sem API key)
    levantava ValidationError na própria construção -- o Run inteiro
    desaparecia sem nenhum rastro de auditoria. Prova que agora persiste
    e recarrega intacto, preservando cost_usd=0.0 (confirmado-zero, nunca
    None) e had_uncertain_prior_attempts=False."""
    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.pricing import PricingRegistry
    from app.judge.single_judge import SingleJudge
    from app.editor.compose import Editor
    from tests.judge.fixtures import debate_result as jr_debate_result
    from tests.judge.fixtures import model_response as jr_model_response
    from tests.judge.fixtures import raw_claim as jr_raw_claim

    c1 = jr_raw_claim("A", "resp-1", provider="openai")
    dr_for_judge = jr_debate_result([c1], [jr_model_response("openai")])
    keyless = AnthropicProvider(
        api_key=None, timeout_seconds=30, max_retries=2,
        default_model="claude-sonnet-5", pricing=PricingRegistry({}),
    )
    judge = SingleJudge({"anthropic": keyless})
    rc = run_config(judge_provider="anthropic", editor_provider="anthropic")
    jr_real = await judge.judge(
        dr_for_judge, rc,
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )
    assert jr_real.attempts[0].transport_attempts == 0
    assert jr_real.attempts[0].cost_usd == 0.0

    # editor_result CONSISTENTE com jr_real (verdict=None -> caminho
    # determinístico do Editor, sem chamada de provider nenhuma) --
    # evita o mismatch de FK que ocorreria ao trocar só judge_result
    # mantendo o editor_result/final_answer originais do fixture padrão.
    editor = Editor({"anthropic": keyless})
    er_real = await editor.compose(
        dr_for_judge, jr_real, rc,
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    result = full_council_run_result(judge_result=jr_real, editor_result=er_real)

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_attempt = loaded.judge_result.attempts[0]
    assert reloaded_attempt.transport_attempts == 0
    assert reloaded_attempt.cost_usd == 0.0  # continua confirmado-zero, não vira None
    assert reloaded_attempt.had_uncertain_prior_attempts is False


# ---------------------------------------------------------------------------
# Etapa 17A.1 (requisito L) — roundtrip de auditoria de provider_finish_reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_finish_reason_roundtrips_through_persistence(repo):
    result = full_council_run_result()
    mr = result.debate_result.initial_result.responses[0]
    mr_with_reason = mr.model_copy(update={"provider_finish_reason": "max_tokens"})
    new_initial = result.debate_result.initial_result.model_copy(
        update={
            "responses": [mr_with_reason, *result.debate_result.initial_result.responses[1:]]
        }
    )
    new_debate = result.debate_result.model_copy(update={"initial_result": new_initial})
    result = result.model_copy(update={"debate_result": new_debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_mr = loaded.debate_result.initial_result.responses[0]
    assert reloaded_mr.provider_finish_reason == "max_tokens"


@pytest.mark.asyncio
async def test_provider_finish_reason_none_roundtrips_as_none(repo):
    result = full_council_run_result()

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded_mr = loaded.debate_result.initial_result.responses[0]
    assert reloaded_mr.provider_finish_reason is None
