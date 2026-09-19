from __future__ import annotations

import json

import pytest

from app.debate.processing_record import ClaimProcessingAttempt
from app.judge.context import JUDGE_CONTRACT_VERSION
from app.judge.single_judge import SingleJudge
from app.models.provider_models import ModelIdentitySource, TokenUsage
from app.orchestrator.config import QuorumPolicy, RunConfig
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response
from tests.judge.fixtures import debate_result, model_response, raw_claim


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Qual a capital do Brasil?",
        enabled_providers=["openai", "anthropic"],
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        round_dispatch_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)


def _assessment_payload(claim_id: str, verdict: str = "supported", **extra) -> str:
    body = {
        "claim_assessments": [
            {"claim_id": claim_id, "verdict": verdict, "explanation": "avaliação de teste"}
        ],
        "confidence": 0.5,
        "reasoning": "justificativa de teste",
    }
    body.update(extra)
    return json.dumps(body)


# ---------------------------------------------------------------------------
# Os 5 estados de veredito
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_five_verdict_states_are_reachable():
    c1 = raw_claim("A", "resp-1", provider="openai")
    c2 = raw_claim("B", "resp-2", provider="anthropic")
    c3 = raw_claim("C", "resp-3", provider="openai")
    c4 = raw_claim("D", "resp-4", provider="anthropic")
    c5 = raw_claim("E", "resp-5", provider="openai")
    dr = debate_result(
        [c1, c2, c3, c4, c5], [model_response("openai"), model_response("anthropic")]
    )

    payload = json.dumps(
        {
            "claim_assessments": [
                {"claim_id": c1.id, "verdict": "supported", "explanation": "ok"},
                {"claim_id": c2.id, "verdict": "partially_supported", "explanation": "ok"},
                {"claim_id": c3.id, "verdict": "rejected", "explanation": "ok"},
                {"claim_id": c4.id, "verdict": "conflicting", "explanation": "ok"},
                {"claim_id": c5.id, "verdict": "unresolved", "explanation": "ok"},
            ],
            "confidence": 0.6,
            "reasoning": "justificativa",
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    verdicts = {a.claim_id: a.verdict for a in result.verdict.claim_assessments}
    assert verdicts == {
        c1.id: "supported",
        c2.id: "partially_supported",
        c3.id: "rejected",
        c4.id: "conflicting",
        c5.id: "unresolved",
    }


# ---------------------------------------------------------------------------
# Completude exata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_omitted_claim_is_inconsistent_and_retry_exhausts():
    c1 = raw_claim("A", "resp-1", provider="openai")
    c2 = raw_claim("B", "resp-2", provider="anthropic")
    dr = debate_result([c1, c2], [model_response("openai"), model_response("anthropic")])
    incomplete = _assessment_payload(c1.id)  # c2 fica de fora

    provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", incomplete), text_response("anthropic", incomplete)]
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_output_invalid"
    assert len(result.attempts) == 2
    assert all(a.parse_status == "inconsistent_references" for a in result.attempts)


@pytest.mark.asyncio
async def test_duplicate_claim_id_is_inconsistent():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    dup = json.dumps(
        {
            "claim_assessments": [
                {"claim_id": c1.id, "verdict": "supported", "explanation": "a"},
                {"claim_id": c1.id, "verdict": "rejected", "explanation": "b"},
            ],
            "confidence": 0.5,
            "reasoning": "x",
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", dup), text_response("anthropic", dup)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "judge_output_invalid"
    assert all(a.parse_status == "inconsistent_references" for a in result.attempts)


@pytest.mark.asyncio
async def test_unknown_claim_id_is_inconsistent_but_retry_can_still_succeed():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    bad = _assessment_payload("id-que-nao-existe")
    fallback = _assessment_payload(c1.id)
    provider = ScriptedProvider("anthropic", [text_response("anthropic", bad), text_response("anthropic", fallback)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.attempts[0].parse_status == "inconsistent_references"
    assert result.verdict is not None
    assert result.attempts[1].parse_status == "accepted"


@pytest.mark.asyncio
async def test_unresolved_is_accepted_as_valid_no_omission():
    c1 = raw_claim("claim difícil de avaliar", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _assessment_payload(c1.id, verdict="unresolved")
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert result.verdict.claim_assessments[0].verdict == "unresolved"


# ---------------------------------------------------------------------------
# Model identity provenance -- production-path regression (repair pós-
# revisão independente): prova que SingleJudge.judge() (não uma
# construção manual de JudgeAttempt/JudgeVerdict) copia
# model_identity_source do ProviderResponse REAL devolvido pelo
# provider fake -- uma mutação que apagasse essa cópia faria estes
# testes falharem.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_attempt_and_verdict_copy_model_identity_source_from_provider_response():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _assessment_payload(c1.id)
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response(
                "anthropic", payload, model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK
            )
        ],
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert result.verdict.judge_model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK
    assert result.attempts[-1].model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK


@pytest.mark.asyncio
async def test_malformed_then_accepted_attempts_each_copy_their_own_model_identity_source():
    """Cada JudgeAttempt (inclusive o REJEITADO por malformed) copia
    model_identity_source do ProviderResponse da SUA PRÓPRIA tentativa,
    nunca um valor global herdado da tentativa seguinte/anterior."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response(
                "anthropic",
                "isso não é json",
                model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
            ),
            text_response(
                "anthropic", good, model_identity_source=ModelIdentitySource.PROVIDER_REPORTED
            ),
        ],
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.attempts[0].parse_status == "malformed"
    assert result.attempts[0].model_identity_source == ModelIdentitySource.REQUESTED_FALLBACK
    assert result.attempts[1].parse_status == "accepted"
    assert result.attempts[1].model_identity_source == ModelIdentitySource.PROVIDER_REPORTED
    assert result.verdict.judge_model_identity_source == ModelIdentitySource.PROVIDER_REPORTED


# ---------------------------------------------------------------------------
# Retry de structured output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_retries_then_succeeds():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)
    provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", "isso não é json"), text_response("anthropic", good)]
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert len(result.attempts) == 2
    assert result.attempts[0].parse_status == "malformed"
    assert result.attempts[1].parse_status == "accepted"


@pytest.mark.asyncio
async def test_malformed_retry_exhausted():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", "ruim 1"), text_response("anthropic", "ruim 2")]
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_output_invalid"
    assert len(result.attempts) == 2


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_judge_attempt_carries_request_provenance():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)
    provider = ScriptedProvider("anthropic", [text_response("anthropic", good)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    attempt = result.attempts[0]
    assert attempt.request_provenance is not None
    assert attempt.request_provenance.contract_version == JUDGE_CONTRACT_VERSION


@pytest.mark.asyncio
async def test_transport_error_judge_attempt_carries_request_provenance():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [transport_error_response("anthropic")])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    attempt = result.attempts[0]
    assert attempt.request_provenance is not None
    assert attempt.request_provenance.contract_version == JUDGE_CONTRACT_VERSION


@pytest.mark.asyncio
async def test_malformed_then_success_judge_attempts_share_identical_request_provenance():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)
    provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", "isso não é json"), text_response("anthropic", good)]
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert len(result.attempts) == 2
    assert result.attempts[0].request_provenance is not None
    assert result.attempts[0].request_provenance == result.attempts[1].request_provenance


# ---------------------------------------------------------------------------
# Falha de transporte
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_failure_has_no_retry_at_this_layer():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [transport_error_response("anthropic")])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_transport_failed"
    assert len(result.attempts) == 1
    assert len(provider.received_requests) == 1


# ---------------------------------------------------------------------------
# no_claims_to_judge — bug do early return corrigido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_claims_with_budget_fine_reports_false():
    dr = debate_result([], [model_response("openai", status="error")])
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=1_000_000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "no_claims_to_judge"
    assert result.cumulative_budget_exceeded is False
    assert len(provider.received_requests) == 0


@pytest.mark.asyncio
async def test_no_claims_with_budget_already_exceeded_reports_true():
    big_response = model_response("openai", usage=TokenUsage(input_tokens=8000, output_tokens=0))
    dr = debate_result([], [big_response])
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "no_claims_to_judge"
    assert result.cumulative_budget_exceeded is True
    assert len(provider.received_requests) == 0


# ---------------------------------------------------------------------------
# Repair (Run02 claim-extraction exhaustion) -- D: falha ESTRUTURAL total
# de extração NUNCA chama o provider de Judge, e é rotulada DISTINTA de
# "no_claims_to_judge" (que continua reservado pra extração genuinamente
# vazia).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_extraction_failed_never_calls_judge_provider():
    dr = debate_result(
        [],
        [model_response("openai")],
        debate_skipped_reason="all_initial_extractions_failed",
        claim_processing_attempts=[],
    )
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=1_000_000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "claim_extraction_failed"
    assert result.attempts == []
    assert len(provider.received_requests) == 0
    assert result.cumulative_budget_exceeded is False


@pytest.mark.asyncio
async def test_claim_extraction_failed_takes_priority_over_budget_state():
    """O motivo reportado é sempre "claim_extraction_failed" nesse
    cenário -- `cumulative_budget_exceeded` continua um fato
    INDEPENDENTE (mesma disciplina de "no_claims_to_judge"), nunca muda
    qual razão é reportada."""
    big_response = model_response("openai", usage=TokenUsage(input_tokens=8000, output_tokens=0))
    dr = debate_result(
        [],
        [big_response],
        debate_skipped_reason="all_initial_extractions_failed",
        claim_processing_attempts=[],
    )
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "claim_extraction_failed"
    assert result.cumulative_budget_exceeded is True
    assert len(provider.received_requests) == 0


def _accepted_empty_extraction_attempt(response_id: str) -> ClaimProcessingAttempt:
    return ClaimProcessingAttempt(
        operation="extraction", round_number=1, attempt_number=1,
        provider="anthropic", requested_model="claude-sonnet-5", model="claude-sonnet-5",
        target_model_response_id=response_id,
        transport_status="success", transport_attempts=1,
        raw_output_text='{"claims": []}', parse_status="accepted",
        usage=TokenUsage(input_tokens=0, output_tokens=0), cost_usd=0.0, latency_ms=1,
    )


def _failed_extraction_attempt(response_id: str) -> ClaimProcessingAttempt:
    return ClaimProcessingAttempt(
        operation="extraction", round_number=1, attempt_number=1,
        provider="anthropic", requested_model="claude-sonnet-5", model="claude-sonnet-5",
        target_model_response_id=response_id,
        transport_status="success", transport_attempts=1,
        raw_output_text="isto não é JSON", parse_status="malformed",
        parse_error_message="JSON inválido",
        usage=TokenUsage(input_tokens=0, output_tokens=0), cost_usd=0.0, latency_ms=1,
    )


# ---------------------------------------------------------------------------
# Repair (adversarial review, Finding A) -- distinção entre cobertura
# COMPLETA (no_claims_to_judge genuíno), cobertura INCOMPLETA por falha
# estrutural (claim_extraction_incomplete), e cobertura incompleta por
# budget (budget_exhausted_before_judge preservado) -- as 3 nunca devem
# se confundir.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_extraction_incomplete_when_one_accepted_empty_and_one_failed():
    """Cobertura incompleta (1 aceita-vazia + 1 falhada), budget NÃO
    excedido -- "claim_extraction_incomplete", NUNCA "no_claims_to_judge"
    (que mentiria dizendo que a extração rodou completa)."""
    accepted_empty_response = model_response("openai")
    failed_response = model_response("anthropic")
    dr = debate_result(
        [],
        [accepted_empty_response, failed_response],
        claim_processing_attempts=[
            _accepted_empty_extraction_attempt(accepted_empty_response.id),
            _failed_extraction_attempt(failed_response.id),
        ],
        debate_skipped_reason="insufficient_initial_quorum",
    )
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=1_000_000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "claim_extraction_incomplete"
    assert result.cumulative_budget_exceeded is False
    assert len(provider.received_requests) == 0


@pytest.mark.asyncio
async def test_incomplete_coverage_from_not_attempted_target_preserves_budget_reason():
    """Cobertura incompleta por alvo NUNCA tentado (budget) -- quando o
    Judge também vê budget excedido, o motivo preservado é
    "budget_exhausted_before_judge", NUNCA "no_claims_to_judge" nem um
    "claim_extraction_incomplete" genérico que esconderia a causa real
    (Finding A, requisito 4)."""
    accepted_empty_response = model_response("openai")
    never_attempted_response = model_response(
        "anthropic", usage=TokenUsage(input_tokens=8000, output_tokens=0)
    )
    dr = debate_result(
        [],
        [accepted_empty_response, never_attempted_response],
        claim_processing_attempts=[
            _accepted_empty_extraction_attempt(accepted_empty_response.id),
            # never_attempted_response: NENHUMA tentativa -- budget parou
            # o processamento antes de alcançá-la.
        ],
        debate_skipped_reason="budget_exhausted_before_critique",
    )
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "budget_exhausted_before_judge"
    assert result.cumulative_budget_exceeded is True
    assert len(provider.received_requests) == 0


@pytest.mark.asyncio
async def test_complete_coverage_all_accepted_empty_across_retry_still_reports_no_claims_to_judge():
    """Retry-safe: um alvo malformado-então-aceito (retry bem-sucedido)
    conta como coberto -- cobertura permanece COMPLETA, "no_claims_to_judge"
    continua correto mesmo com uma tentativa rejeitada no meio do
    caminho."""
    response = model_response("openai")
    malformed_then_accepted = [
        _failed_extraction_attempt(response.id),
        ClaimProcessingAttempt(
            operation="extraction", round_number=1, attempt_number=2,
            provider="anthropic", requested_model="claude-sonnet-5", model="claude-sonnet-5",
            target_model_response_id=response.id,
            transport_status="success", transport_attempts=1,
            raw_output_text='{"claims": []}', parse_status="accepted",
            usage=TokenUsage(input_tokens=0, output_tokens=0), cost_usd=0.0, latency_ms=1,
        ),
    ]
    dr = debate_result(
        [],
        [response],
        claim_processing_attempts=malformed_then_accepted,
        debate_skipped_reason="insufficient_initial_quorum",
    )
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=1_000_000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "no_claims_to_judge"
    assert len(provider.received_requests) == 0


# ---------------------------------------------------------------------------
# Budget — 6999/7000/7001 e soft cap durante trabalho já iniciado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_6999_opens_gate():
    c1 = raw_claim("A", "resp-1", provider="openai")
    response = model_response("openai", usage=TokenUsage(input_tokens=6999, output_tokens=0))
    dr = debate_result([c1], [response])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", _assessment_payload(c1.id))])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_budget_7000_closes_gate_exact_tie():
    c1 = raw_claim("A", "resp-1", provider="openai")
    response = model_response("openai", usage=TokenUsage(input_tokens=7000, output_tokens=0))
    dr = debate_result([c1], [response])
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "budget_exhausted_before_judge"
    assert len(provider.received_requests) == 0


@pytest.mark.asyncio
async def test_budget_7001_closes_gate():
    c1 = raw_claim("A", "resp-1", provider="openai")
    response = model_response("openai", usage=TokenUsage(input_tokens=7001, output_tokens=0))
    dr = debate_result([c1], [response])
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict_unavailable_reason == "budget_exhausted_before_judge"
    assert len(provider.received_requests) == 0


@pytest.mark.asyncio
async def test_soft_cap_during_started_work_still_returns_valid_verdict():
    """6999 abre o gate; a chamada do Judge consome mais 1500, passando
    do limite — o veredito continua válido (soft cap, nunca interrompe
    trabalho já em andamento), só cumulative_budget_exceeded reflete o
    estouro depois do fato."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    response = model_response("openai", usage=TokenUsage(input_tokens=6999, output_tokens=0))
    dr = debate_result([c1], [response])
    judge_response = text_response(
        "anthropic", _assessment_payload(c1.id), input_tokens=1500, output_tokens=0
    )
    provider = ScriptedProvider("anthropic", [judge_response])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(max_total_tokens=7000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert result.cumulative_budget_exceeded is True


# ---------------------------------------------------------------------------
# evaluated_through_round
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluated_through_round_1_when_critique_skipped():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])  # sem critique_responses
    provider = ScriptedProvider("anthropic", [text_response("anthropic", _assessment_payload(c1.id))])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict.evaluated_through_round == 1


@pytest.mark.asyncio
async def test_evaluated_through_round_2_even_with_zero_critique_success():
    """'Ocorreu' é sobre CritiqueResult existir, não sobre
    successful_count>0 — crítica com 0/N ainda avança pro round 2."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result(
        [c1],
        [model_response("openai"), model_response("anthropic")],
        critique_responses=[
            model_response("openai", round_number=2, status="error"),
            model_response("anthropic", round_number=2, status="error"),
        ],
    )
    assert dr.critique_round.critique_obtained is False  # confirma o cenário 0/N

    provider = ScriptedProvider("anthropic", [text_response("anthropic", _assessment_payload(c1.id))])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict.evaluated_through_round == 2


# ---------------------------------------------------------------------------
# best_arguments_by — validado só contra participantes reais
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_best_arguments_by_rejects_non_participant():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    bad = _assessment_payload(c1.id, best_arguments_by={"gemini": "bom argumento"})
    fallback = _assessment_payload(c1.id)
    provider = ScriptedProvider("anthropic", [text_response("anthropic", bad), text_response("anthropic", fallback)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.attempts[0].parse_status == "inconsistent_references"
    assert result.verdict is not None


@pytest.mark.asyncio
async def test_best_arguments_by_accepts_real_participant():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id, best_arguments_by={"openai": "bom argumento"})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", good)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict.best_arguments_by == {"openai": "bom argumento"}


# ---------------------------------------------------------------------------
# Configuração / auditoria
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_judge_provider_raises_before_any_call():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [])
    judge = SingleJudge({"anthropic": provider})

    with pytest.raises(ValueError, match="judge_provider desconhecido"):
        await judge.judge(
        dr, _run_config(judge_provider="provider-que-nao-existe"),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert len(provider.received_requests) == 0


@pytest.mark.asyncio
async def test_judge_model_reflects_accepted_response_not_presumed():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "ruim", model="claude-attempt-1"),
            text_response("anthropic", _assessment_payload(c1.id), model="claude-attempt-2"),
        ],
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict.judge_model == "claude-attempt-2"


@pytest.mark.asyncio
async def test_judge_can_run_with_weak_initial_debate_1_of_3():
    c1 = raw_claim("única resposta", "resp-1", provider="openai")
    dr = debate_result(
        [c1],
        [
            model_response("openai"),
            model_response("anthropic", status="error"),
            model_response("gemini", status="error"),
        ],
    )
    payload = _assessment_payload(
        c1.id,
        verdict="unresolved",
        debate_limitations=["apenas 1 de 3 modelos respondeu à rodada inicial"],
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert result.verdict.evaluated_through_round == 1
    assert result.verdict.debate_limitations


# ---------------------------------------------------------------------------
# Etapa 17A (B2) — retry bloqueado por budget já esgotado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_retry_blocked_when_budget_already_exhausted():
    """Só 1 resposta roteirizada -- se o retry fosse tentado mesmo com
    budget já esgotado (bug), o ScriptedProvider levantaria
    AssertionError interna por esgotar o roteiro."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", "não é JSON válido", cost_usd=0.03)]
    )
    judge = SingleJudge({"anthropic": provider})

    # prior=0.03 (< 0.05, 1ª tentativa autorizada) -- depois da 1ª
    # tentativa (+0.03), o total 0.06 >= 0.05 bloqueia o retry.
    result = await judge.judge(
        dr, _run_config(max_cost_usd=0.05),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.03,
    )

    assert len(result.attempts) == 1  # sem retry
    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_output_invalid"


# ---------------------------------------------------------------------------
# Etapa 17A.1 (Objetivo D) — tolerância a cerca de código Markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fenced_json_judge_output_succeeds():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = (
        '```json\n{"claim_assessments": [{"claim_id": "'
        + c1.id
        + '", "verdict": "supported", "explanation": "ok"}], '
        '"confidence": 0.5, "reasoning": "justificativa"}\n```'
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert result.attempts[0].parse_status == "accepted"
    assert result.verdict is not None


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo B) — Judge usa teto PRÓPRIO, distinto do geral
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_call_uses_max_output_tokens_judge_not_the_general_ceiling():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", _assessment_payload(c1.id))])
    judge = SingleJudge({"anthropic": provider})

    await judge.judge(
        dr,
        _run_config(max_output_tokens_per_call=1024, max_output_tokens_judge=9000),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert len(provider.received_requests) == 1
    assert provider.received_requests[0].max_tokens == 9000


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo C/D) — truncamento conhecido cancela o retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_truncation_transport_failure_reports_truncated_reason():
    """Reprodução do Run B (Evidência A): resposta sem bloco de texto
    (erro de transporte) MAS com provider_finish_reason="max_tokens" --
    já não tinha retry (transporte nunca é retentado nesta camada), mas
    agora vira um motivo ESPECÍFICO (judge_output_truncated), não o
    genérico judge_transport_failed."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider(
        "anthropic", [transport_error_response("anthropic", provider_finish_reason="max_tokens")]
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_output_truncated"
    assert len(result.attempts) == 1
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_known_truncation_on_malformed_judge_output_skips_retry():
    """JSON do Judge cortado no meio por max_tokens (transporte com
    sucesso, mas malformado) não deve disparar uma 2a tentativa idêntica
    -- só 1 resposta roteirizada; se o retry fosse tentado, o
    ScriptedProvider esgotaria o roteiro."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response(
                "anthropic",
                '{"claim_assessments": [{"claim_id": "' + c1.id + '"',
                provider_finish_reason="max_tokens",
            )
        ],
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_output_truncated"
    assert len(result.attempts) == 1
    assert result.attempts[0].parse_status == "malformed"
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_malformed_judge_output_without_truncation_signal_still_retries():
    """Regressão: malformação SEM motivo de truncamento confirmado
    continua se comportando exatamente como antes (retry normal)."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "isso não é json", provider_finish_reason="end_turn"),
            text_response("anthropic", good),
        ],
    )
    judge = SingleJudge({"anthropic": provider})

    result = await judge.judge(
        dr, _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )

    assert result.verdict is not None
    assert len(result.attempts) == 2
    assert result.attempts[0].parse_status == "malformed"
    assert result.attempts[1].parse_status == "accepted"


# ---------------------------------------------------------------------------
# Cross-Channel Reconciliation V1 -- regressão de source-blindness (seção 1
# do contrato).
# ---------------------------------------------------------------------------


def test_single_judge_judge_does_not_accept_source_analysis_result_or_reconciliation():
    import inspect

    signature = inspect.signature(SingleJudge.judge)
    assert "source_analysis_result" not in signature.parameters
    assert "reconciliation" not in signature.parameters


# ---------------------------------------------------------------------------
# Judge Transport Execution Policy V1
#
# `SingleJudge(execution_policy=...)` passa o override de TRANSPORTE a CADA
# `LLMProvider.complete()` do Judge. Transporte e retry de output
# ESTRUTURADO são mecanismos separados: o retry estruturado continua
# existindo, e cada completion dele recebe a política independentemente.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app.config import Settings  # noqa: E402
from app.models.provider_models import (  # noqa: E402
    ProviderExecutionPolicy,
    TransportAttemptPolicy,
)
from app.providers.base import LLMProvider  # noqa: E402
from app.providers.pricing import PricingRegistry  # noqa: E402


def _resolved_judge_override() -> TransportAttemptPolicy:
    """O override REAL de produção (resolvido de `Settings` default)."""
    override = ProviderExecutionPolicy.from_settings(Settings(_env_file=None)).judge_override
    assert override is not None
    return override


async def _judge_once(judge: SingleJudge, dr):
    return await judge.judge(
        dr,
        _run_config(),
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )


class _RealTransportProvider(LLMProvider):
    """`LLMProvider` REAL (loop de retry/timeout de produção, sem
    sobrescrever `complete()`), com `_call_api` roteirizado -- cada item
    do script é `(delay_s, texto)` ou uma exceção."""

    provider_name = "anthropic"

    def __init__(self, script, *, timeout_seconds: float, max_retries: int):
        super().__init__(
            api_key="fake-key",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            pricing=PricingRegistry({}),
        )
        self._script = list(script)
        self.call_count = 0

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def _call_api(self, request):
        self.call_count += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        delay, text = item
        await asyncio.sleep(delay)
        return (text, TokenUsage(input_tokens=10, output_tokens=5), "fake-model", "end_turn")


@pytest.mark.asyncio
async def test_judge_completion_receives_the_resolved_120s_single_attempt_policy():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", _assessment_payload(c1.id))])
    override = _resolved_judge_override()
    judge = SingleJudge({"anthropic": provider}, execution_policy=override)

    result = await _judge_once(judge, dr)

    assert result.verdict is not None
    assert provider.received_execution_policies == [override]
    assert override.attempt_timeout_seconds == 120.0
    assert override.max_transport_attempts_per_completion == 1


@pytest.mark.asyncio
async def test_judge_without_configured_policy_passes_none_default_behavior_unchanged():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", _assessment_payload(c1.id))])

    result = await _judge_once(SingleJudge({"anthropic": provider}), dr)

    assert result.verdict is not None
    assert provider.received_execution_policies == [None]


@pytest.mark.asyncio
async def test_structured_output_retry_still_works_and_each_completion_gets_the_policy():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)
    provider = ScriptedProvider(
        "anthropic", [text_response("anthropic", "isso não é json"), text_response("anthropic", good)]
    )
    override = _resolved_judge_override()
    judge = SingleJudge({"anthropic": provider}, execution_policy=override)

    result = await _judge_once(judge, dr)

    assert result.verdict is not None
    assert [a.parse_status for a in result.attempts] == ["malformed", "accepted"]
    # DUAS completions (retry estruturado preservado), CADA UMA com a
    # política do Judge independentemente.
    assert provider.received_execution_policies == [override, override]


@pytest.mark.asyncio
async def test_structured_retry_completions_each_get_a_full_independent_transport_budget():
    """Provider REAL cujo default (0.02s) estouraria QUALQUER chamada de
    0.1s: o override (5s / 1 tentativa) precisa valer pra completion do
    1º output inválido E pra do retry estruturado -- a 2ª completion não
    herda "o que sobrou" da 1ª."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = _RealTransportProvider(
        [(0.1, "isso não é json"), (0.1, _assessment_payload(c1.id))],
        timeout_seconds=0.02,
        max_retries=2,
    )
    override = TransportAttemptPolicy(
        attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1
    )
    judge = SingleJudge({"anthropic": provider}, execution_policy=override)

    result = await _judge_once(judge, dr)

    assert result.verdict is not None
    assert [a.parse_status for a in result.attempts] == ["malformed", "accepted"]
    assert [a.transport_attempts for a in result.attempts] == [1, 1]
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_single_timed_out_judge_attempt_never_retries_or_sleeps_and_keeps_unknown_accounting():
    """Provider REAL, default 3 tentativas: com o override (1 tentativa)
    um timeout gera UMA chamada de transporte, nenhum sleep de backoff,
    `JudgeAttempt.transport_attempts == 1`, usage/cost DESCONHECIDOS e
    `had_uncertain_prior_attempts` False (não houve tentativa ANTERIOR)."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = _RealTransportProvider(
        [(10, "nunca chega lá")] * 3, timeout_seconds=5.0, max_retries=2
    )
    override = TransportAttemptPolicy(
        attempt_timeout_seconds=0.01, max_transport_attempts_per_completion=1
    )
    judge = SingleJudge({"anthropic": provider}, execution_policy=override)

    with patch("app.providers.base._backoff_delay") as mock_backoff:
        result = await _judge_once(judge, dr)

    assert result.verdict is None
    assert result.verdict_unavailable_reason == "judge_transport_failed"
    assert len(result.attempts) == 1  # sem retry estruturado pra erro de transporte
    assert provider.call_count == 1
    mock_backoff.assert_not_called()
    attempt = result.attempts[0]
    assert attempt.transport_status == "error"
    assert attempt.transport_error is not None
    assert attempt.transport_error.type.value == "timeout"
    assert attempt.transport_attempts == 1
    assert attempt.had_uncertain_prior_attempts is False
    assert attempt.usage is None
    assert attempt.cost_usd is None


@pytest.mark.asyncio
async def test_judge_request_content_and_digest_are_identical_with_and_without_the_policy():
    """A política de transporte NUNCA entra no `CompletionRequest` -- o
    request enviado e o digest `judge_v1` gravado são idênticos com e sem
    override (semântica do request inalterada)."""
    from app.models.request_provenance import compute_request_digest

    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good = _assessment_payload(c1.id)

    plain = ScriptedProvider("anthropic", [text_response("anthropic", good)])
    overridden = ScriptedProvider("anthropic", [text_response("anthropic", good)])
    plain_result = await _judge_once(SingleJudge({"anthropic": plain}), dr)
    overridden_result = await _judge_once(
        SingleJudge({"anthropic": overridden}, execution_policy=_resolved_judge_override()), dr
    )

    assert plain.received_requests[0] == overridden.received_requests[0]
    assert compute_request_digest(plain.received_requests[0]) == compute_request_digest(
        overridden.received_requests[0]
    )
    assert (
        plain_result.attempts[0].request_provenance
        == overridden_result.attempts[0].request_provenance
    )
    assert plain_result.attempts[0].request_provenance.contract_version == JUDGE_CONTRACT_VERSION == "judge_v1"
