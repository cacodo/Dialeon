from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.editor.result import FinalAnswer
from app.models.domain import ClaimAssessment, ClaimSupport
from app.models.provider_models import DefaultModelAuthoritySnapshot, ModelIdentitySource
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from app.reconciliation.errors import ReconciliationError
from app.storage.records import AcceptedRunRecord, CompletedRunRecord, QuorumFailureRecord
from app.storage.repository import _run_config_from_json
from tests.storage.fixtures import (
    claim,
    error_model_response,
    full_council_run_result,
    model_response,
    now,
    provider_execution_policy,
    quorum_failure_exception,
    run_config,
    very_rich_council_run_result,
    with_recomputed_reconciliation,
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
async def test_historical_oversized_question_still_loads_verbatim(repo):
    """Accepted Question Size Boundary V1, seção 5 do contrato --
    RunConfig.question NUNCA aplica o novo teto como field_validator
    (ver app/orchestrator/config.py), justamente pra que uma execução
    histórica com question > MAX_QUESTION_CHARACTERS (persistida ANTES
    deste limite existir) continue reconstruível via
    `RunConfig(**run_config_json)`. Este teste simula essa execução
    histórica diretamente (save_success/reload reais, sem migração de
    schema nenhuma) -- a question sobrevive INTEIRA, sem truncamento/
    reescrita."""
    from app.orchestrator.config import MAX_QUESTION_CHARACTERS

    oversized_question = "pergunta histórica muito longa " * 1000
    assert len(oversized_question) > MAX_QUESTION_CHARACTERS

    result = full_council_run_result(run_config=run_config(question=oversized_question))
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.council_run_result.run_config.question == oversized_question
    assert len(loaded.council_run_result.run_config.question) == len(oversized_question)


@pytest.mark.asyncio
async def test_historical_whitespace_only_question_still_loads_verbatim(repo):
    """Idem acima, pro outro lado do contrato -- uma question
    whitespace-only historicamente persistida (nunca rejeitada por
    RunConfig, só pela boundary de aceite de execuções NOVAS) também
    precisa continuar reconstruível verbatim."""
    result = full_council_run_result(run_config=run_config(question="   \n\t  "))
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    assert loaded.council_run_result.run_config.question == "   \n\t  "


@pytest.mark.asyncio
async def test_historical_run_config_with_infinite_cost_still_loads_unchanged(repo):
    """Deployment Execution Configuration Boundary V1, matriz I -- esta
    slice valida `default_max_cost_usd`/`orchestrator_round_dispatch_timeout_seconds`
    em `Settings` (app/config.py), NUNCA no próprio campo de
    `RunConfig` (que continua `Field(gt=0)`, sem `allow_inf_nan=False`)
    -- justamente pra não quebrar a reconstrução de uma execução
    histórica que porventura tenha sido aceita com esses valores antes
    desta correção existir (nenhuma migração/backfill/reinterpretação).
    Simula essa execução histórica diretamente (save_success/reload
    reais) -- `max_cost_usd=inf` sobrevive INTEIRO."""
    result = full_council_run_result(run_config=run_config(max_cost_usd=float("inf")))
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    assert math.isinf(loaded.council_run_result.run_config.max_cost_usd)


@pytest.mark.asyncio
async def test_historical_run_config_with_infinite_round_timeout_still_loads_unchanged(repo):
    """Historical Non-Finite Execution-Limit Public Representation V1,
    T22 -- companion do teste acima pro OUTRO campo historicamente
    afetado (`round_dispatch_timeout_seconds`). A reconstrução via
    `RunConfig(**run_config_json)` continua produzindo `+inf` DOMÍNIO
    puro -- nenhuma reinterpretação/backfill acontece aqui; o token
    público `"positive_infinity"` só existe depois do mapper de
    apresentação (ver tests/presentation/test_run_config_public.py)."""
    result = full_council_run_result(
        run_config=run_config(round_dispatch_timeout_seconds=float("inf"))
    )
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    assert math.isinf(loaded.council_run_result.run_config.round_dispatch_timeout_seconds)


@pytest.mark.asyncio
async def test_historical_provider_execution_policy_with_infinite_timeout_still_loads_unchanged(
    repo,
):
    """Idem acima, pra `ProviderExecutionPolicy.attempt_timeout_seconds`
    (também reconstruído de JSON persistido, ver `_policy_from_json`,
    app/storage/repository.py) -- este slice NÃO estreita o campo do
    próprio `ProviderExecutionPolicy` (só `Settings.provider_timeout_seconds`,
    que é `int` e nunca pode ser `inf` pela via real de deployment), pela
    mesma disciplina de não quebrar reconstrução histórica."""
    from app.models.provider_models import ProviderExecutionPolicy

    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=float("inf"), max_transport_attempts_per_completion=3
    )
    await repo.save_accepted(
        "run-historical-inf-timeout",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=policy,
    )

    loaded = await repo.get_run("run-historical-inf-timeout")

    assert math.isinf(loaded.provider_execution_policy.attempt_timeout_seconds)


@pytest.mark.asyncio
async def test_historical_policy_reconstruction_never_invokes_new_execution_validator(
    repo, monkeypatch
):
    """Provider Execution Policy Finite New-Execution Boundary V1, B10 --
    sentinela direto: a reconstrução histórica (`_policy_from_json`)
    NUNCA chama `validate_provider_execution_policy_for_new_execution`
    -- reforça, além do teste acima (que só prova que +inf sobrevive),
    que o CAMINHO em si é estruturalmente distinto da boundary de
    execução nova."""
    import app.models.provider_models as provider_models_module
    from app.models.provider_models import ProviderExecutionPolicy

    def _should_never_be_called(policy):
        raise AssertionError(
            "validate_provider_execution_policy_for_new_execution nunca deveria "
            "ser chamado durante reconstrução histórica"
        )

    monkeypatch.setattr(
        provider_models_module,
        "validate_provider_execution_policy_for_new_execution",
        _should_never_be_called,
    )

    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=float("inf"), max_transport_attempts_per_completion=3
    )
    await repo.save_accepted(
        "run-historical-inf-timeout-2",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=policy,
    )

    loaded = await repo.get_run("run-historical-inf-timeout-2")

    assert math.isinf(loaded.provider_execution_policy.attempt_timeout_seconds)


@pytest.mark.asyncio
async def test_historical_infeasible_quorum_run_config_still_loads_unchanged(repo):
    """Accepted Quorum Feasibility Boundary V1, matriz F, item 29 --
    `RunConfig.quorum.min_to_return > len(enabled_providers)` (rejeitado
    hoje pra execuções NOVAS, ver app/orchestrator/config.py) já
    persistido historicamente (aceito ANTES desta regra existir) precisa
    continuar carregável, verbatim, sem reinterpretação/backfill/
    migração -- mesma disciplina de
    `test_historical_oversized_question_still_loads_verbatim` acima."""
    from app.orchestrator.config import QuorumPolicy

    infeasible_quorum = QuorumPolicy(min_for_debate=2, min_to_return=2)
    result = full_council_run_result(
        run_config=run_config(enabled_providers=["openai"], quorum=infeasible_quorum)
    )
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)

    assert isinstance(loaded, CompletedRunRecord)
    reloaded_config = loaded.council_run_result.run_config
    assert reloaded_config.quorum.min_to_return == 2
    assert reloaded_config.enabled_providers == ("openai",)


@pytest.mark.asyncio
async def test_executing_historical_infeasible_quorum_config_today_is_rejected(repo):
    """Accepted Quorum Feasibility Boundary V1, matriz F, item 31 --
    carregar a configuração histórica infactível (teste acima) é
    permitido; EXECUTÁ-LA de novo através das boundaries de execução
    ATUAIS (`Orchestrator.run()` aqui, o nível mais direto/baixo) é
    rejeitado ANTES de qualquer dispatch -- nenhuma migração/backfill/
    reinterpretação, só a boundary de aceite de execução NOVA fazendo
    seu trabalho sobre um RunConfig genuinamente reconstruído da
    persistência."""
    from app.orchestrator.config import QuorumPolicy
    from app.orchestrator.orchestrator import Orchestrator

    infeasible_quorum = QuorumPolicy(min_for_debate=2, min_to_return=2)
    result = full_council_run_result(
        run_config=run_config(enabled_providers=["openai"], quorum=infeasible_quorum)
    )
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    historical_config = loaded.council_run_result.run_config

    orchestrator = Orchestrator({})  # nenhum provider real precisa existir
    with pytest.raises(ValueError, match="min_to_return"):
        await orchestrator.run(historical_config)


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
async def test_claim_support_provider_reported_roundtrip(repo):
    """ClaimSupport regression matrix, C -- round-trip provider_reported."""
    result = full_council_run_result()
    original_claim = result.debate_result.claims[0]
    diverged_support = original_claim.supporting_model_response_ids[0].model_copy(
        update={"model_identity_source": ModelIdentitySource.PROVIDER_REPORTED}
    )
    updated_claim = original_claim.model_copy(
        update={
            "supporting_model_response_ids": [
                diverged_support,
                *original_claim.supporting_model_response_ids[1:],
            ]
        }
    )
    debate = result.debate_result.model_copy(update={"claims": [updated_claim]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.claims[0].supporting_model_response_ids[0]
    assert reloaded.model_response_id == diverged_support.model_response_id
    assert reloaded.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_claim_support_requested_fallback_roundtrip(repo):
    """ClaimSupport regression matrix, D -- round-trip requested_fallback."""
    result = full_council_run_result()
    original_claim = result.debate_result.claims[0]
    diverged_support = original_claim.supporting_model_response_ids[0].model_copy(
        update={"model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK}
    )
    updated_claim = original_claim.model_copy(
        update={
            "supporting_model_response_ids": [
                diverged_support,
                *original_claim.supporting_model_response_ids[1:],
            ]
        }
    )
    debate = result.debate_result.model_copy(update={"claims": [updated_claim]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.claims[0].supporting_model_response_ids[0]
    assert reloaded.model_response_id == diverged_support.model_response_id
    assert reloaded.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_claim_support_historical_none_roundtrips_as_none(repo):
    """ClaimSupport regression matrix, E -- uma linha histórica (coluna
    NULL, model presente) reconstrói como None no domínio/público, nunca
    reinterpretada -- inclusive quando model == requested_model do
    ModelResponse referenciado (equality nunca prova nada, ver
    docstring de ModelIdentitySource)."""
    result = full_council_run_result()
    original_claim = result.debate_result.claims[0]
    diverged_support = original_claim.supporting_model_response_ids[0].model_copy(
        update={"model_identity_source": None}
    )
    updated_claim = original_claim.model_copy(
        update={
            "supporting_model_response_ids": [
                diverged_support,
                *original_claim.supporting_model_response_ids[1:],
            ]
        }
    )
    debate = result.debate_result.model_copy(update={"claims": [updated_claim]})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = loaded.debate_result.claims[0].supporting_model_response_ids[0]
    assert reloaded.model_identity_source is None


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
    # base_claim/second_claim deixam de ser correntes (merged_from_claim_ids
    # de `merged` as referencia) -- o veredito original só avaliava
    # base_claim.id (a claim única da fixture-base), que não é mais
    # corrente. Reconciliação exige avaliação pra TODA claim corrente
    # (aqui, só "merged-claim-id") -- reaponta o mesmo veredito pra ela.
    verdict = result.judge_result.verdict.model_copy(
        update={
            "claim_assessments": [
                ClaimAssessment(claim_id=merged.id, verdict="supported", explanation="Consenso.")
            ]
        }
    )
    judge_result = result.judge_result.model_copy(update={"verdict": verdict})
    result = with_recomputed_reconciliation(
        result.model_copy(
            update={"debate_result": debate_result, "judge_result": judge_result}
        )
    )

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
    # base_claim/round2_claim deixam de ser correntes (merged_from_claim_ids
    # de `reconciled` as referencia) -- reaponta o veredito pra a única
    # claim corrente resultante.
    verdict = result.judge_result.verdict.model_copy(
        update={
            "claim_assessments": [
                ClaimAssessment(
                    claim_id=reconciled.id, verdict="supported", explanation="Consenso."
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
async def test_final_answer_limitations_with_extraction_coverage_note_roundtrips_in_order(repo):
    """Repair (adversarial review, Finding B) -- `FinalAnswer.limitations`
    deixou de ser exclusivamente cópia verbatim do Judge -- pode conter
    uma entrada FINAL app-autorada (disclosure de cobertura de extração
    incompleta). Persistência é genérica (`list[str]`), mas este teste
    prova DIRETAMENTE que a ordem/conteúdo exatos sobrevivem save/load,
    nunca deduplica/reordena/trunca."""
    result = full_council_run_result()
    judge_limitation = "o debate teve cobertura parcial de crítica"
    coverage_note = (
        "Cobertura de extração de afirmações incompleta: 1 de 2 respostas "
        "bem-sucedidas dos participantes não puderam ter suas afirmações "
        "extraídas para avaliação -- o resultado acima considera só as "
        "afirmações que puderam ser extraídas."
    )
    final_answer = result.editor_result.final_answer.model_copy(
        update={"limitations": [judge_limitation, coverage_note]}
    )
    editor = result.editor_result.model_copy(update={"final_answer": final_answer})
    result = result.model_copy(update={"editor_result": editor})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.limitations == [judge_limitation, coverage_note]
    assert loaded.editor_result.final_answer.limitations[-1] == coverage_note


@pytest.mark.asyncio
async def test_final_answer_answer_blocks_none_roundtrips_as_none(repo):
    """`full_council_run_result()` nunca popula `answer_blocks` (fixture
    histórica, `status="llm_composed"`) -- confirma que a coluna NULLABLE
    persiste/reconstrói `None` honestamente, nunca inventa estrutura."""
    result = full_council_run_result()
    assert result.editor_result.final_answer.answer_blocks is None
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.answer_blocks is None


@pytest.mark.asyncio
async def test_final_answer_answer_blocks_roundtrip_preserves_structure(repo):
    """UI Slice 3 -- um `answer_blocks` populado (parágrafo + seção de
    claims com nota de reconciliação) sobrevive save/load byte-a-byte
    igual, inclusive a discriminação `kind` de cada variante da union."""
    from app.editor.answer_blocks import (
        AnswerClaimItem,
        AnswerClaimSectionBlock,
        AnswerParagraphBlock,
    )

    result = full_council_run_result()
    # Listas mutáveis deliberadamente (não tuplas) -- exercitam o mesmo
    # caminho de um chamador real (`FinalAnswer(...)`, nunca `model_copy`,
    # que puларia a validação/coerção pra tupla que este teste prova).
    blocks = [
        AnswerParagraphBlock(text="Resultado da avaliação do debate:"),
        AnswerClaimSectionBlock(
            heading="Conclusões sustentadas pelo debate:",
            items=[
                AnswerClaimItem(
                    claim_text="A receita cresceu 12% em 2025.",
                    verdict_label="sustentada pelo debate",
                    explanation="Múltiplos participantes concordam.",
                    source_relationship_note="Relação com a fonte fornecida: a fonte aponta na MESMA direção da avaliação do debate.",
                )
            ],
        ),
    ]
    # `model_copy(update=...)` NÃO valida -- passaria `blocks` (lista)
    # adiante sem coerção pra tupla, mascarando exatamente a garantia do
    # achado 2. Reconstruir via o construtor normal (`FinalAnswer(...)`)
    # força a mesma validação real que qualquer caminho de produção
    # também sofre.
    #
    # Repair (fechamento do contrato estruturado) -- `status` sobrescrito
    # pra `"llm_planned"`: a fixture base usa `status="llm_composed"`
    # (histórico, nunca produz `answer_blocks` de verdade), que agora é
    # corretamente rejeitado por `_answer_blocks_forbidden_for_unstructured_statuses`
    # (app/editor/result.py) quando `answer_blocks` não é `None` -- o
    # mesmo conjunto de campos (`editor_model`/`based_on_verdict_id`/
    # `judge_confidence`) continua exigido pelos dois status, então a
    # troca não precisa de mais nenhum campo.
    final_answer = FinalAnswer(
        **{**result.editor_result.final_answer.__dict__, "status": "llm_planned", "answer_blocks": blocks}
    )
    editor_result = result.editor_result.model_copy(update={"final_answer": final_answer})
    result = result.model_copy(update={"editor_result": editor_result})

    assert isinstance(final_answer.answer_blocks, tuple)

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    loaded_blocks = loaded.editor_result.final_answer.answer_blocks
    assert loaded_blocks is not None
    assert loaded_blocks == tuple(blocks)
    assert loaded_blocks[0].kind == "paragraph"
    assert loaded_blocks[1].kind == "claim_section"
    assert loaded_blocks[1].items[0].source_relationship_note == blocks[1].items[0].source_relationship_note


@pytest.mark.asyncio
async def test_final_answer_unevaluated_claims_none_roundtrips_as_none(repo):
    """`full_council_run_result()` nunca popula `unevaluated_claims`
    (fixture histórica, `status="llm_composed"`, campo só aplicável a
    `deterministic_no_verdict`) -- confirma que a coluna NULLABLE
    persiste/reconstrói `None` honestamente, nunca inventa estrutura."""
    result = full_council_run_result()
    assert result.editor_result.final_answer.unevaluated_claims is None
    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.editor_result.final_answer.unevaluated_claims is None


@pytest.mark.asyncio
async def test_final_answer_unevaluated_claims_roundtrip_preserves_order_and_content(repo):
    """Repair (adversarial review -- Structured Unevaluated Claims,
    requisito 9) -- um `unevaluated_claims` populado sobrevive save/load
    byte-a-byte igual, incluindo a ORDEM exata e o tipo tupla."""
    result = full_council_run_result()
    final_answer = FinalAnswer(
        answer_text=(
            "A avaliação final não pôde ser concluída: falha de teste. As seguintes "
            "afirmações foram levantadas pelos modelos participantes, mas não foram "
            "avaliadas:\n- primeira claim\n- segunda claim\n- terceira claim"
        ),
        limitations=["Avaliação final não realizada: falha de teste."],
        status="deterministic_no_verdict",
        unevaluated_claims=("primeira claim", "segunda claim", "terceira claim"),
    )
    editor_result = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "attempts": [],
            "fallback_reason": "judge_verdict_unavailable",
        }
    )
    result = result.model_copy(update={"editor_result": editor_result})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    loaded_claims = loaded.editor_result.final_answer.unevaluated_claims
    assert loaded_claims == ("primeira claim", "segunda claim", "terceira claim")
    assert isinstance(loaded_claims, tuple)


@pytest.mark.asyncio
async def test_malformed_scalar_unevaluated_claims_json_fails_closed_on_load(engine, repo):
    """Repair (adversarial review -- fail-closed em JSON persistido
    malformado) -- uma string JSON escalar ("abc") NUNCA deve ganhar
    autoridade semântica como afirmações do modelo participante via
    coerção ingênua `tuple("abc") == ("a", "b", "c")`. Mesma disciplina
    de test_malformed_answer_blocks_json_fails_closed_on_load, aplicada
    à coluna nova desta slice."""
    result = full_council_run_result()
    final_answer = FinalAnswer(
        answer_text="A avaliação final não pôde ser concluída: falha de teste.",
        limitations=["Avaliação final não realizada: falha de teste."],
        status="deterministic_no_verdict",
        unevaluated_claims=("primeira claim",),
    )
    editor_result = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "attempts": [],
            "fallback_reason": "judge_verdict_unavailable",
        }
    )
    result = result.model_copy(update={"editor_result": editor_result})
    await repo.save_success(result)
    final_answer_id = result.editor_result.final_answer.id

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE final_answers SET unevaluated_claims_json = :json WHERE id = :id"),
            {"json": json.dumps("abc"), "id": final_answer_id},
        )

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


@pytest.mark.asyncio
async def test_malformed_object_unevaluated_claims_json_fails_closed_on_load(engine, repo):
    """Mesmo achado que o teste acima, forma diferente -- um objeto JSON
    ({"forged": "value"}) NUNCA deve ganhar autoridade semântica via
    coerção ingênua `tuple({"forged": "value"}) == ("forged",)` (chaves
    do dict viram claims forjadas)."""
    result = full_council_run_result()
    final_answer = FinalAnswer(
        answer_text="A avaliação final não pôde ser concluída: falha de teste.",
        limitations=["Avaliação final não realizada: falha de teste."],
        status="deterministic_no_verdict",
        unevaluated_claims=("primeira claim",),
    )
    editor_result = result.editor_result.model_copy(
        update={
            "final_answer": final_answer,
            "attempts": [],
            "fallback_reason": "judge_verdict_unavailable",
        }
    )
    result = result.model_copy(update={"editor_result": editor_result})
    await repo.save_success(result)
    final_answer_id = result.editor_result.final_answer.id

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE final_answers SET unevaluated_claims_json = :json WHERE id = :id"),
            {"json": json.dumps({"forged": "value"}), "id": final_answer_id},
        )

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


@pytest.mark.asyncio
async def test_malformed_answer_blocks_json_fails_closed_on_load(engine, repo):
    """Achado 3 da revisão adversarial, adjacente -- `answer_blocks_json`
    persistido malformado (aqui: um heading que não está no vocabulário
    fechado, ver `AnswerSectionHeading`/`ANSWER_SECTION_HEADING_ORDER`,
    app/editor/answer_blocks.py) precisa fazer a reconstrução FALHAR
    explicitamente (`ValidationError`) -- nunca ser silenciosamente
    reparada/descartada. Mesma disciplina de
    test_malformed_request_provenance_json_fails_closed_on_load acima,
    aplicada à coluna nova desta slice."""
    result = full_council_run_result()
    await repo.save_success(result)
    final_answer_id = result.editor_result.final_answer.id

    forged_blocks = [
        {"kind": "paragraph", "text": "Abertura."},
        {
            "kind": "claim_section",
            "heading": "Um heading forjado que não existe no vocabulário fechado:",
            "items": [
                {
                    "claim_text": "c",
                    "verdict_label": "sustentada pelo debate",
                    "explanation": "e",
                    "source_relationship_note": None,
                }
            ],
        },
    ]

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE final_answers SET answer_blocks_json = :json WHERE id = :id"),
            {"json": json.dumps(forged_blocks), "id": final_answer_id},
        )

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


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
async def test_save_success_rejects_new_write_with_reconciliation_none(repo):
    """Repair #4 (revisão adversarial) -- `reconciliation=None` é
    reservado EXCLUSIVAMENTE pra reconstrução de execuções históricas
    persistidas antes deste slice existir; uma escrita NOVA sem
    reconciliação concreta é recusada ANTES de qualquer `session.add()`
    (nenhuma transação chega a abrir)."""
    result = full_council_run_result().model_copy(update={"reconciliation": None})

    with pytest.raises(ReconciliationError):
        await repo.save_success(result)

    # nada foi persistido -- nem o council_run em si.
    assert await repo.get_run(result.id) is None


@pytest.mark.asyncio
async def test_save_success_rejects_new_write_with_incoherent_reconciliation(repo):
    """Repair #4/#2B -- uma reconciliação estruturalmente presente mas
    incoerente com os dados reais desta execução (aqui: judge_verdict_id
    referenciando um veredito que não é o real) também é recusada ANTES
    de qualquer escrita -- `validate_reconciliation_coherence` roda na
    fronteira de persistência, não só no runner."""
    result = full_council_run_result()
    tampered_outcome = result.reconciliation.claim_outcomes[0].model_copy(
        update={"judge_verdict_id": "veredito-que-nao-e-o-real"}
    )
    tampered_reconciliation = result.reconciliation.model_copy(
        update={"claim_outcomes": [tampered_outcome]}
    )
    result = result.model_copy(update={"reconciliation": tampered_reconciliation})

    with pytest.raises(ReconciliationError):
        await repo.save_success(result)

    assert await repo.get_run(result.id) is None


@pytest.mark.asyncio
async def test_save_success_accepts_new_write_with_valid_v1_reconciliation(repo):
    """Contraparte positiva dos dois testes acima -- uma reconciliação
    concreta e coerente (o caso comum, produzida pelo pipeline real)
    persiste normalmente."""
    result = full_council_run_result()

    await repo.save_success(result)

    loaded = (await repo.get_run(result.id)).council_run_result
    assert loaded.reconciliation is not None
    assert loaded.reconciliation.status == "complete"


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


@pytest.mark.asyncio
async def test_all_initial_extractions_failed_reason_survives_persistence_and_reload(repo):
    """Repair (Run02 claim-extraction exhaustion) -- `debate_skipped_reason`/
    `verdict_unavailable_reason` são colunas `str | None` genéricas (ver
    app/storage/models.py) -- nenhuma migração/enum novo foi introduzida
    por este repair, então os NOVOS valores precisam sobreviver a
    save+load byte-a-byte como qualquer outro, prova DIRETA (nunca só
    inferida da ausência de migração)."""
    from app.editor.result import FinalAnswer
    from app.reconciliation.reconcile import reconcile_source_and_judge

    result = full_council_run_result()

    debate = result.debate_result.model_copy(
        update={
            "critique_round": None,
            "claims": [],
            "claim_processing_attempts": [],
            "numeric_verification_attempts": [],
            "debate_skipped_reason": "all_initial_extractions_failed",
            "cumulative_budget_exceeded": False,
        }
    )
    judge = result.judge_result.model_copy(
        update={
            "verdict": None,
            "attempts": [],
            "verdict_unavailable_reason": "claim_extraction_failed",
        }
    )
    editor = result.editor_result.model_copy(
        update={
            "final_answer": FinalAnswer(
                answer_text="resposta determinística de teste",
                status="deterministic_no_verdict",
            ),
            "attempts": [],
            "fallback_reason": "judge_verdict_unavailable",
        }
    )
    # Reconciliação coerente com claims=[]/verdict=None -- mesma função
    # real que app/council/runner.py chama, nunca reconstruída à mão.
    reconciliation = reconcile_source_and_judge([], judge, None)
    result = result.model_copy(
        update={
            "debate_result": debate,
            "judge_result": judge,
            "editor_result": editor,
            "source_analysis_result": None,
            "reconciliation": reconciliation,
        }
    )

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.debate_result.debate_skipped_reason == "all_initial_extractions_failed"
    assert loaded.judge_result.verdict_unavailable_reason == "claim_extraction_failed"
    assert loaded.debate_result.critique_round is None
    assert loaded.judge_result.verdict is None
    assert loaded.editor_result.fallback_reason == "judge_verdict_unavailable"
    # Os dois computed_field novos de cobertura de extração continuam
    # deriváveis normalmente após reload (nunca colunas próprias --
    # derivados de initial_result/critique_round/claim_processing_attempts,
    # todos reconstruídos a partir dos registros-fato).
    assert loaded.debate_result.claim_extraction_eligible_response_count == (
        result.debate_result.claim_extraction_eligible_response_count
    )
    assert loaded.debate_result.claim_extraction_missing_response_count == (
        result.debate_result.claim_extraction_missing_response_count
    )


# ---------------------------------------------------------------------------
# Model identity provenance -- round-trip de model_identity_source em cada
# tabela que persiste identidade de modelo (ModelIdentitySource), NULL
# histórico, e não-backfill.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_response_provider_reported_roundtrip(repo):
    result = full_council_run_result()
    mr1 = result.debate_result.initial_result.responses[0]
    diverged = mr1.model_copy(
        update={"model_identity_source": ModelIdentitySource.PROVIDER_REPORTED}
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
    assert reloaded.model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


@pytest.mark.asyncio
async def test_model_response_requested_fallback_roundtrip(repo):
    result = full_council_run_result()
    mr1 = result.debate_result.initial_result.responses[0]
    diverged = mr1.model_copy(
        update={"model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK}
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
    assert reloaded.model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_model_response_historical_none_roundtrips_as_none_not_backfilled(repo):
    """Uma linha com model_identity_source=None (histórica, coluna
    existia mas nenhum valor foi persistido) reconstrói exatamente como
    None -- nunca reinterpretada como requested_fallback só porque
    `model == requested_model` bateria por acidente."""
    result = full_council_run_result()
    mr1 = result.debate_result.initial_result.responses[0]
    diverged = mr1.model_copy(
        update={
            "model_identity_source": None,
            "requested_model": mr1.model,  # bate por acidente -- não pode virar fallback
        }
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
    assert reloaded.model_identity_source is None


@pytest.mark.asyncio
async def test_judge_verdict_and_final_answer_model_identity_source_roundtrip(repo):
    """Cobre judge_verdicts/final_answers -- os dois usam nomes de
    coluna próprios (judge_model_identity_source/
    editor_model_identity_source), não o genérico model_identity_source."""
    result = full_council_run_result()
    judge = result.judge_result
    verdict = judge.verdict.model_copy(
        update={"judge_model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK}
    )
    judge = judge.model_copy(update={"verdict": verdict})
    editor = result.editor_result
    final_answer = editor.final_answer.model_copy(
        update={"editor_model_identity_source": ModelIdentitySource.REQUESTED_FALLBACK}
    )
    editor = editor.model_copy(update={"final_answer": final_answer})
    result = result.model_copy(update={"judge_result": judge, "editor_result": editor})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.judge_result.verdict.judge_model_identity_source == (
        ModelIdentitySource.REQUESTED_FALLBACK
    )
    assert loaded.editor_result.final_answer.editor_model_identity_source == (
        ModelIdentitySource.REQUESTED_FALLBACK
    )


@pytest.mark.asyncio
async def test_quorum_failure_preserves_model_identity_source_per_response(repo):
    """Caminho de quórum insuficiente -- as respostas já produzidas
    também persistem provenance de identidade de modelo (mesma disciplina
    do caminho completed)."""
    exc = quorum_failure_exception()
    rc = run_config()

    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )
    loaded = await repo.get_run(failure_id)

    by_provider = {r.provider: r for r in loaded.round_result.responses}
    assert by_provider["openai"].model_identity_source == ModelIdentitySource.PROVIDER_REPORTED
    assert by_provider["anthropic"].model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1 -- round-trip de
# request_provenance nas 5 famílias que a carregam, NULL histórico, e
# fail-closed sobre JSON malformado.
# ---------------------------------------------------------------------------


def _provenance(contract_version: str, digest_suffix: str) -> RequestProvenance:
    return RequestProvenance(
        contract_version=contract_version,
        request_digest=REQUEST_DIGEST_PREFIX + digest_suffix * 64,
    )


@pytest.mark.asyncio
async def test_request_provenance_roundtrips_across_all_five_record_families(repo):
    result = very_rich_council_run_result()

    mr_provenance = _provenance("initial_response_v1", "a")
    proc_provenance = _provenance("claim_extraction_v1", "b")
    judge_provenance = _provenance("judge_v1", "c")
    editor_provenance = _provenance("editor_v1", "d")
    source_provenance = _provenance("source_analysis_v1", "e")

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

    source_attempt = result.source_analysis_result.attempts[0].model_copy(
        update={"request_provenance": source_provenance}
    )
    source_analysis_result = result.source_analysis_result.model_copy(
        update={"attempts": [source_attempt]}
    )

    result = result.model_copy(
        update={
            "debate_result": debate_result,
            "judge_result": judge_result,
            "editor_result": editor_result,
            "source_analysis_result": source_analysis_result,
        }
    )
    result = with_recomputed_reconciliation(result)

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    assert loaded.debate_result.initial_result.responses[0].request_provenance == mr_provenance
    assert (
        loaded.debate_result.claim_processing_attempts[0].request_provenance == proc_provenance
    )
    assert loaded.judge_result.attempts[0].request_provenance == judge_provenance
    assert loaded.editor_result.attempts[0].request_provenance == editor_provenance
    assert loaded.source_analysis_result.attempts[0].request_provenance == source_provenance


@pytest.mark.asyncio
async def test_model_response_historical_none_request_provenance_roundtrips_as_none(repo):
    """Uma linha com request_provenance=None (histórica, coluna existia
    mas nenhum valor foi persistido) reconstrói exatamente como None --
    nunca um v1 inferido/regenerado a partir do builder atual (ver
    seção 3 do contrato desta slice)."""
    result = full_council_run_result()
    mr1 = result.debate_result.initial_result.responses[0].model_copy(
        update={"request_provenance": None}
    )
    responses = [mr1] + result.debate_result.initial_result.responses[1:]
    initial = result.debate_result.initial_result.model_copy(update={"responses": responses})
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    await repo.save_success(result)
    loaded = (await repo.get_run(result.id)).council_run_result

    reloaded = next(
        r for r in loaded.debate_result.initial_result.responses if r.id == mr1.id
    )
    assert reloaded.request_provenance is None


@pytest.mark.asyncio
async def test_malformed_request_provenance_json_fails_closed_on_load(engine, repo):
    """Um valor parcial/malformado persistido em `request_provenance_json`
    (aqui: só `contract_version`, sem `request_digest`) precisa fazer a
    reconstrução do domínio FALHAR explicitamente -- nunca ser
    silenciosamente reparada/completada com um digest inventado (ver
    seção 15 do contrato desta slice: validar o valor completo como
    unidade)."""
    result = full_council_run_result()
    await repo.save_success(result)
    mr1 = result.debate_result.initial_result.responses[0]

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE model_responses SET request_provenance_json = :json WHERE id = :id"
            ),
            {"json": json.dumps({"contract_version": "initial_response_v1"}), "id": mr1.id},
        )

    with pytest.raises(ValidationError):
        await repo.get_run(result.id)


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
    # c_z/c_a/c_m são claims correntes novas (nenhuma superseded/merged) --
    # reconciliação exige avaliação do Judge pra TODA claim corrente,
    # então o veredito original (só sobre a claim única da fixture-base)
    # precisa ser estendido pra cobri-las também.
    extra_assessments = [
        ClaimAssessment(claim_id=c.id, verdict="supported", explanation="ok")
        for c in (c_z, c_a, c_m)
    ]
    verdict = result.judge_result.verdict.model_copy(
        update={
            "claim_assessments": list(result.judge_result.verdict.claim_assessments)
            + extra_assessments
        }
    )
    judge_result = result.judge_result.model_copy(update={"verdict": verdict})
    result = with_recomputed_reconciliation(
        result.model_copy(update={"debate_result": debate, "judge_result": judge_result})
    )

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


# ---------------------------------------------------------------------------
# T02.4 — lifecycle de aceite durável (accepted_runs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_accepted_persists_minimal_running_record(repo):
    rc = run_config()
    started_at = now()
    policy = provider_execution_policy()
    await repo.save_accepted(
        "run-accept-1", run_config=rc, started_at=started_at, provider_execution_policy=policy
    )

    loaded = await repo.get_run("run-accept-1")
    assert isinstance(loaded, AcceptedRunRecord)
    assert loaded.status == "running"
    assert loaded.id == "run-accept-1"
    assert loaded.failed_at is None
    assert loaded.failure_classification is None
    assert loaded.failure_message is None
    assert loaded.run_config == rc
    assert loaded.provider_execution_policy == policy  # T02.2, teste H


@pytest.mark.asyncio
async def test_accepted_running_record_appears_in_list_runs_with_null_ended_at(repo):
    """Teste H -- modela honestamente "processo morreu (ou ainda está
    rodando) logo depois do aceite, antes de qualquer desfecho
    terminal": nenhuma simulação de crash real de processo é necessária
    -- só NÃO chamar nenhum save_success/save_quorum_failure/
    save_unexpected_failure depois de save_accepted já é o estado
    honesto que este teste verifica."""
    await repo.save_accepted("run-accept-2", run_config=run_config(), started_at=now(), provider_execution_policy=provider_execution_policy())

    summaries = await repo.list_runs()
    assert len(summaries) == 1
    assert summaries[0].id == "run-accept-2"
    assert summaries[0].status == "running"
    assert summaries[0].ended_at is None


@pytest.mark.asyncio
async def test_save_success_finalizes_and_deletes_accepted_row(repo):
    result = full_council_run_result()
    policy = provider_execution_policy()
    await repo.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=policy,
    )

    await repo.save_success(result)

    summaries = await repo.list_runs()
    assert len(summaries) == 1  # a linha de aceite não sobrevive -- nunca 2 fontes pro mesmo id
    assert summaries[0].status == "completed"

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    # T02.2, teste I -- a política aceita é copiada EXATA pro terminal completed.
    assert loaded.provider_execution_policy == policy


@pytest.mark.asyncio
async def test_save_success_without_prior_accepted_row_leaves_policy_none(repo):
    """T02.2 -- chamador direto de save_success, sem passar por
    save_accepted antes (mesma disciplina já testada pra outros campos):
    não há linha de aceite nenhuma de onde copiar, então
    provider_execution_policy fica None -- nunca um default inventado."""
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.provider_execution_policy is None


@pytest.mark.asyncio
async def test_save_quorum_failure_with_run_id_reuses_identity_and_deletes_accepted_row(repo):
    exc = quorum_failure_exception()
    rc = run_config()
    started_at = now()
    policy = provider_execution_policy()
    await repo.save_accepted(
        "run-accept-3", run_config=rc, started_at=started_at, provider_execution_policy=policy
    )

    failure_id = await repo.save_quorum_failure(
        exc, run_config=rc, started_at=started_at, failed_at=now(), run_id="run-accept-3"
    )
    assert failure_id == "run-accept-3"

    summaries = await repo.list_runs()
    assert len(summaries) == 1
    assert summaries[0].status == "insufficient_quorum"

    loaded = await repo.get_run("run-accept-3")
    assert isinstance(loaded, QuorumFailureRecord)
    # T02.2, teste J -- a política aceita é copiada EXATA pro terminal insufficient_quorum.
    assert loaded.provider_execution_policy == policy


@pytest.mark.asyncio
async def test_save_quorum_failure_without_run_id_still_mints_its_own(repo):
    """Compatibilidade -- chamadores diretos (testes existentes acima)
    que exercitam só a mecânica de persistência de quórum, sem passar
    por save_accepted antes, continuam funcionando exatamente como
    antes: `run_id` é opcional."""
    exc = quorum_failure_exception()
    failure_id = await repo.save_quorum_failure(
        exc, run_config=run_config(), started_at=now(), failed_at=now()
    )
    assert failure_id is not None
    loaded = await repo.get_run(failure_id)
    assert isinstance(loaded, QuorumFailureRecord)


@pytest.mark.asyncio
async def test_save_unexpected_failure_transitions_accepted_row_to_failed(repo):
    rc = run_config()
    started_at = now()
    policy = provider_execution_policy()
    await repo.save_accepted(
        "run-accept-4", run_config=rc, started_at=started_at, provider_execution_policy=policy
    )

    failed_at = now()
    await repo.save_unexpected_failure(
        "run-accept-4",
        failed_at=failed_at,
        failure_classification="WeirdBug",
        failure_message="Erro interno inesperado durante a execução.",
    )

    loaded = await repo.get_run("run-accept-4")
    assert isinstance(loaded, AcceptedRunRecord)
    assert loaded.status == "failed"
    assert loaded.failed_at == failed_at
    assert loaded.failure_classification == "WeirdBug"
    assert loaded.failure_message == "Erro interno inesperado durante a execução."
    assert loaded.run_config == rc  # config aceita original, nunca perdida na transição
    # T02.2, teste K -- a política aceita sobrevive INTACTA à transição
    # running -> failed (save_unexpected_failure nunca a toca).
    assert loaded.provider_execution_policy == policy

    summaries = await repo.list_runs()
    assert len(summaries) == 1
    assert summaries[0].status == "failed"
    assert summaries[0].ended_at == failed_at


@pytest.mark.asyncio
async def test_save_success_terminal_rollback_preserves_accepted_row(repo):
    """Teste G -- se a transação terminal "completed" falhar
    (integridade referencial violada), a linha de aceite gravada ANTES
    precisa sobreviver intacta -- nunca um estado parcial/fabricado, e a
    falha de persistência propaga (nunca é convertida em sucesso ou
    silenciada)."""
    result = full_council_run_result()
    policy = provider_execution_policy()
    await repo.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=policy,
    )

    bad_claim = result.debate_result.claims[0].model_copy(
        update={"source_model_response_id": "id-que-nao-existe-em-nenhum-model-response"}
    )
    debate_result = result.debate_result.model_copy(update={"claims": [bad_claim]})
    broken_result = result.model_copy(update={"debate_result": debate_result})

    with pytest.raises(Exception):
        await repo.save_success(broken_result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, AcceptedRunRecord)
    assert loaded.status == "running"  # nem completed, nem apagado -- a evidência de aceite persiste
    # T02.2, teste L -- a política aceita também sobrevive ao rollback intacta.
    assert loaded.provider_execution_policy == policy


@pytest.mark.asyncio
async def test_save_quorum_failure_terminal_rollback_preserves_accepted_row(repo):
    """Teste G, variante quorum failure -- mesma garantia, via violação
    de PK (2 ModelResponse com o mesmo id na mesma rodada)."""
    from app.orchestrator.result import RoundResult

    duplicated_id = "resposta-duplicada-de-proposito-t02-4"
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
    rc = run_config()
    started_at = now()
    await repo.save_accepted("run-accept-5", run_config=rc, started_at=started_at, provider_execution_policy=provider_execution_policy())

    with pytest.raises(Exception):
        await repo.save_quorum_failure(
            exc, run_config=rc, started_at=started_at, failed_at=now(), run_id="run-accept-5"
        )

    loaded = await repo.get_run("run-accept-5")
    assert isinstance(loaded, AcceptedRunRecord)
    assert loaded.status == "running"


# ---------------------------------------------------------------------------
# T02.2 -- provenance de ProviderExecutionPolicy: distinguibilidade,
# compatibilidade legada, finalização de linha legada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_policy_same_run_config_produces_distinguishable_manifests(repo):
    """Teste M -- duas execuções com o MESMO RunConfig efetivo, mas
    ProviderExecutionPolicy RESOLVIDA diferente (deployment mudou
    provider_timeout_seconds/provider_max_retries entre as duas),
    precisam permanecer distinguíveis via o snapshot persistido -- é
    exatamente o gap que esta feature fecha (antes, essa diferença era
    invisível/irreconstruível)."""
    rc = run_config()
    result_a = full_council_run_result(run_config=rc)
    result_b = full_council_run_result(run_config=rc)
    policy_a = provider_execution_policy(
        attempt_timeout_seconds=30.0, max_transport_attempts_per_completion=2
    )
    policy_b = provider_execution_policy(
        attempt_timeout_seconds=90.0, max_transport_attempts_per_completion=5
    )

    await repo.save_accepted(
        result_a.id, run_config=rc, started_at=result_a.started_at, provider_execution_policy=policy_a
    )
    await repo.save_success(result_a)
    await repo.save_accepted(
        result_b.id, run_config=rc, started_at=result_b.started_at, provider_execution_policy=policy_b
    )
    await repo.save_success(result_b)

    loaded_a = await repo.get_run(result_a.id)
    loaded_b = await repo.get_run(result_b.id)

    assert loaded_a.council_run_result.run_config == loaded_b.council_run_result.run_config
    assert loaded_a.provider_execution_policy == policy_a
    assert loaded_b.provider_execution_policy == policy_b
    assert loaded_a.provider_execution_policy != loaded_b.provider_execution_policy


@pytest.mark.asyncio
async def test_legacy_rows_without_policy_column_reconstruct_as_none(engine, repo):
    """Teste N -- simula uma linha "pré-T02.2" inserindo diretamente via
    SQL cru SEM a coluna provider_execution_policy_json (mesma técnica
    já usada pelos testes de compatibilidade legada de run_config_json
    mais acima neste arquivo) -- nunca deve reconstruir com os defaults
    ATUAIS de Settings."""
    from app.storage.database import make_session_factory
    from app.storage.repository import CouncilRepository

    result = full_council_run_result()
    # save_success grava a coluna nova -- pra simular uma linha
    # GENUINAMENTE legada, sobrescrevemos com UPDATE direto pra NULL
    # (equivalente ao que uma linha pré-upgrade real teria).
    await repo.save_success(result)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await session.execute(
            text("UPDATE council_runs SET provider_execution_policy_json = NULL WHERE id = :id"),
            {"id": result.id},
        )
        await session.commit()

    fresh_repo = CouncilRepository(session_factory)
    loaded = await fresh_repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.provider_execution_policy is None  # NUNCA os defaults atuais (60s/3 tentativas)


@pytest.mark.asyncio
async def test_legacy_accepted_row_without_policy_finalizes_with_policy_none(engine, repo):
    """Teste O -- uma linha accepted_runs "pré-upgrade" (policy=NULL),
    ao ser finalizada como completed DEPOIS do upgrade de schema, deve
    produzir um terminal com provider_execution_policy=None -- nunca
    fabricar um valor pra ela só porque o resto do sistema já suporta a
    feature. Prova que save_success COPIA o que está na linha de
    aceite, nunca INVENTA quando a linha de aceite está incompleta."""
    result = full_council_run_result()
    policy = provider_execution_policy()
    await repo.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=policy,
    )
    # Simula que esta linha de aceite é, na verdade, legada -- o
    # deployment fez upgrade de schema, mas esta linha específica nunca
    # teve a política gravada (ex.: aceita bem antes do slice T02.2
    # existir, sobrevivendo até agora como "running").
    from app.storage.database import make_session_factory

    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE accepted_runs SET provider_execution_policy_json = NULL WHERE id = :id"
            ),
            {"id": result.id},
        )
        await session.commit()

    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.provider_execution_policy is None  # nunca herdou o `policy` original


# ---------------------------------------------------------------------------
# Provider Default-Model Snapshot Provenance V1 -- SIBLING de
# `provider_execution_policy` acima, mesma disciplina de persistência
# (matriz C8-C15 do contrato desta slice).
# ---------------------------------------------------------------------------


def _snapshot(**configured_default_models) -> DefaultModelAuthoritySnapshot:
    fields = configured_default_models or {"openai": "gpt-x", "anthropic": "claude-x"}
    return DefaultModelAuthoritySnapshot(configured_default_models=fields)


@pytest.mark.asyncio
async def test_c8_snapshot_persisted_at_acceptance(repo):
    rc = run_config()
    snapshot = _snapshot()
    await repo.save_accepted(
        "run-snapshot-accept-1",
        run_config=rc,
        started_at=now(),
        provider_execution_policy=provider_execution_policy(),
        default_model_authority_snapshot=snapshot,
    )

    loaded = await repo.get_run("run-snapshot-accept-1")
    assert isinstance(loaded, AcceptedRunRecord)
    assert loaded.default_model_authority_snapshot == snapshot


@pytest.mark.asyncio
async def test_c9_running_and_accepted_expose_snapshot(repo):
    """C9 -- exposição em `AcceptedRunRecord` (consumida por
    running_run_response/failed_run_response, tanto em detail quanto em
    audit -- ver app/presentation/mappers.py)."""
    snapshot = _snapshot(openai="gpt-running")
    await repo.save_accepted(
        "run-snapshot-running-1",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=provider_execution_policy(),
        default_model_authority_snapshot=snapshot,
    )

    loaded = await repo.get_run("run-snapshot-running-1")
    assert loaded.status == "running"
    assert loaded.default_model_authority_snapshot.configured_default_models == {
        "openai": "gpt-running"
    }


@pytest.mark.asyncio
async def test_c10_completed_terminal_root_preserves_exact_snapshot(repo):
    result = full_council_run_result()
    snapshot = _snapshot(openai="gpt-accept-time", anthropic="claude-accept-time")
    await repo.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=provider_execution_policy(),
        default_model_authority_snapshot=snapshot,
    )

    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.default_model_authority_snapshot == snapshot


@pytest.mark.asyncio
async def test_c11_quorum_failure_terminal_root_preserves_exact_snapshot(repo):
    exc = quorum_failure_exception()
    rc = run_config()
    snapshot = _snapshot(openai="gpt-accept-time")
    run_id = "run-snapshot-quorum-1"
    await repo.save_accepted(
        run_id,
        run_config=rc,
        started_at=now(),
        provider_execution_policy=provider_execution_policy(),
        default_model_authority_snapshot=snapshot,
    )

    await repo.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now(), run_id=run_id
    )

    loaded = await repo.get_run(run_id)
    assert isinstance(loaded, QuorumFailureRecord)
    assert loaded.default_model_authority_snapshot == snapshot


@pytest.mark.asyncio
async def test_c12_unexpected_failed_terminal_root_preserves_exact_snapshot(repo):
    run_id = "run-snapshot-failed-1"
    snapshot = _snapshot(openai="gpt-accept-time")
    await repo.save_accepted(
        run_id,
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=provider_execution_policy(),
        default_model_authority_snapshot=snapshot,
    )

    await repo.save_unexpected_failure(
        run_id,
        failed_at=now(),
        failure_classification="WeirdBug",
        failure_message="Erro interno inesperado durante a execução.",
    )

    loaded = await repo.get_run(run_id)
    assert isinstance(loaded, AcceptedRunRecord)
    assert loaded.status == "failed"
    assert loaded.default_model_authority_snapshot == snapshot


@pytest.mark.asyncio
async def test_c13_configuration_change_after_acceptance_does_not_alter_historical_snapshot(repo):
    """C13/seção 22 do contrato -- o teste de fechamento chave desta
    slice: aceitar com `provider A default_model = model-old`, persistir,
    depois "reconfigurar" o runtime (`provider A default_model =
    model-new`) e carregar o run original -- o snapshot histórico
    precisa continuar dizendo `model-old`, NUNCA `model-new`."""
    from app.application.service import build_default_model_authority_snapshot

    class _MutableFakeProvider:
        def __init__(self, default_model: str):
            self.default_model = default_model

    provider_a = _MutableFakeProvider("model-old")
    rc = run_config(
        enabled_providers=["openai"],
        claim_processor_provider="openai",
        judge_provider="openai",
        editor_provider="openai",
        source_analyzer_provider="openai",
    )
    snapshot_at_acceptance = build_default_model_authority_snapshot(rc, {"openai": provider_a})

    result = full_council_run_result(run_config=rc)
    await repo.save_accepted(
        result.id,
        run_config=rc,
        started_at=result.started_at,
        provider_execution_policy=provider_execution_policy(),
        default_model_authority_snapshot=snapshot_at_acceptance,
    )
    await repo.save_success(result)

    # "Reconfiguração de deployment" -- o MESMO objeto de provider muda
    # de configuração depois que o aceite já foi persistido.
    provider_a.default_model = "model-new"

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.default_model_authority_snapshot.configured_default_models == {
        "openai": "model-old"
    }
    assert loaded.default_model_authority_snapshot.configured_default_models["openai"] != (
        "model-new"
    )


@pytest.mark.asyncio
async def test_c14_c15_historical_rows_load_with_snapshot_none_never_backfilled(engine, repo):
    """C14/C15 -- mesma técnica de `test_legacy_rows_without_policy_column_reconstruct_as_none`
    acima: simula uma linha "pré-slice" via UPDATE direto pra NULL --
    nunca deve reconstruir com o registry de provider ATUAL."""
    from app.storage.database import make_session_factory
    from app.storage.repository import CouncilRepository

    result = full_council_run_result()
    await repo.save_success(result)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE council_runs SET default_model_authority_snapshot_json = NULL "
                "WHERE id = :id"
            ),
            {"id": result.id},
        )
        await session.commit()

    fresh_repo = CouncilRepository(session_factory)
    loaded = await fresh_repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.default_model_authority_snapshot is None


@pytest.mark.asyncio
async def test_save_success_without_prior_accepted_row_leaves_snapshot_none(repo):
    """Mesma disciplina de `test_save_success_without_prior_accepted_row_leaves_policy_none`
    -- chamador direto de `save_success`, sem `save_accepted` antes: não
    há linha de aceite de onde copiar, então `None` honesto."""
    result = full_council_run_result()
    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.default_model_authority_snapshot is None


# ---------------------------------------------------------------------------
# Judge Transport Execution Policy V1 -- snapshot de política com
# `judge_override`: persistência, reload terminal, compat histórica.
# ---------------------------------------------------------------------------


def _policy_with_judge_override():
    from app.models.provider_models import TransportAttemptPolicy

    return provider_execution_policy(
        attempt_timeout_seconds=60.0,
        max_transport_attempts_per_completion=3,
        judge_override=TransportAttemptPolicy(
            attempt_timeout_seconds=120.0, max_transport_attempts_per_completion=1
        ),
    )


@pytest.mark.asyncio
async def test_accepted_run_persists_default_policy_and_judge_override(engine, repo):
    policy = _policy_with_judge_override()
    await repo.save_accepted(
        "run-judge-policy-1",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=policy,
    )

    loaded = await repo.get_run("run-judge-policy-1")
    assert loaded.provider_execution_policy == policy
    assert loaded.provider_execution_policy.judge_override.attempt_timeout_seconds == 120.0
    assert loaded.provider_execution_policy.judge_override.max_transport_attempts_per_completion == 1
    # default (topo) preservado, distinto do override
    assert loaded.provider_execution_policy.attempt_timeout_seconds == 60.0
    assert loaded.provider_execution_policy.max_transport_attempts_per_completion == 3

    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT provider_execution_policy_json FROM accepted_runs WHERE id = :id"),
                {"id": "run-judge-policy-1"},
            )
        ).scalar_one()
    assert json.loads(raw)["judge_override"] == {
        "attempt_timeout_seconds": 120.0,
        "max_transport_attempts_per_completion": 1,
    }


@pytest.mark.asyncio
async def test_terminal_completed_run_reloads_the_same_policy_with_judge_override(repo):
    result = full_council_run_result()
    policy = _policy_with_judge_override()
    await repo.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=policy,
    )

    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.provider_execution_policy == policy


@pytest.mark.asyncio
async def test_terminal_quorum_failure_reloads_the_same_policy_with_judge_override(repo):
    exc = quorum_failure_exception()
    rc = run_config()
    started_at = now()
    policy = _policy_with_judge_override()
    await repo.save_accepted(
        "run-judge-policy-qf", run_config=rc, started_at=started_at, provider_execution_policy=policy
    )

    await repo.save_quorum_failure(
        exc, run_config=rc, started_at=started_at, failed_at=now(), run_id="run-judge-policy-qf"
    )

    loaded = await repo.get_run("run-judge-policy-qf")
    assert isinstance(loaded, QuorumFailureRecord)
    assert loaded.provider_execution_policy == policy


@pytest.mark.asyncio
async def test_historical_policy_snapshot_without_judge_override_loads_and_is_never_rewritten(
    engine, repo
):
    """Snapshot no formato ANTIGO (só as duas chaves originais) -- carrega
    com `judge_override=None` ("nenhum override registrado") e a cópia
    verbatim accepted -> completed NÃO fabrica a chave nova: o JSON
    persistido continua exatamente igual ao histórico. Sem migração de
    banco (coluna JSON pré-existente)."""
    result = full_council_run_result()
    await repo.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=provider_execution_policy(),
    )
    historical = {"attempt_timeout_seconds": 60.0, "max_transport_attempts_per_completion": 3}
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE accepted_runs SET provider_execution_policy_json = :j WHERE id = :id"),
            {"j": json.dumps(historical), "id": result.id},
        )

    before = await repo.get_run(result.id)  # ainda "running" (accepted)
    assert before.provider_execution_policy.judge_override is None

    await repo.save_success(result)

    loaded = await repo.get_run(result.id)
    assert isinstance(loaded, CompletedRunRecord)
    assert loaded.provider_execution_policy.judge_override is None
    assert loaded.provider_execution_policy.attempt_timeout_seconds == 60.0
    async with engine.begin() as conn:
        raw = (
            await conn.execute(
                text("SELECT provider_execution_policy_json FROM council_runs WHERE id = :id"),
                {"id": result.id},
            )
        ).scalar_one()
    assert json.loads(raw) == historical  # nada fabricado retroativamente
