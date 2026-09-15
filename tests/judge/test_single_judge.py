from __future__ import annotations

import json

import pytest

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
