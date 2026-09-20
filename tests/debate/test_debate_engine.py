from __future__ import annotations

import json

import pytest

from app.debate.context import CRITIQUE_CONTRACT_VERSION
from app.debate.debate_engine import DebateEngine
from app.models.provider_models import (
    ModelIdentitySource,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
    TokenUsage,
)
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.orchestrator import INITIAL_RESPONSE_CONTRACT_VERSION
from tests.debate.fakes import CallableProvider


def _run_config(enabled_providers, claim_processor_provider, **overrides) -> RunConfig:
    fields = dict(
        question="Qual a capital do Brasil?",
        enabled_providers=enabled_providers,
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        round_dispatch_timeout_seconds=5.0,
        claim_processor_provider=claim_processor_provider,
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)


def _ok(provider: str, text: str, input_tokens=10, output_tokens=5) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model="fake-model",
        model="fake-model",
        model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
        status="success",
        text=text,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        cost_usd=None,
        latency_ms=10,
        attempts=1,
    )


def _err(provider: str) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model="fake-model",
        model="fake-model",
        model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
        status="error",
        text=None,
        usage=None,
        cost_usd=None,
        latency_ms=10,
        attempts=2,
        error=ProviderErrorInfo(type=ProviderErrorType.API_ERROR, message="falhou", retryable=True),
    )


async def _err_coro(provider_name: str) -> ProviderResponse:
    return _err(provider_name)


def _normal_processing_handler(provider_name: str, debate_text: str):
    """Handler padrão: extração devolve 1 claim; qualquer outra chamada é
    tratada como resposta de debate normal (rodada inicial ou crítica).
    Agrupamento e reconciliação cross-round NÃO existem mais na execução
    corrente -- se uma dessas chamadas fosse agendada, o corpo dela cairia
    aqui como "resposta de debate" e quebraria as contagens de attempts."""

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" in content:
            text = json.dumps(
                {"claims": [{"text": f"claim de {provider_name}", "revises_claim_id": None}]}
            )
            return _ok(provider_name, text)
        return _ok(provider_name, debate_text)

    return handler


def _make_provider(name: str, debate_text: str) -> CallableProvider:
    return CallableProvider(name, _normal_processing_handler(name, debate_text))


def _capturing_handler(provider_name: str, debate_text: str, captured_max_tokens: list):
    """Igual a `_normal_processing_handler`, mas guarda o `max_tokens`
    enviado em CADA chamada -- pra provar que nenhuma chamada usa mais o
    teto `max_output_tokens_grouping` (não há mais agrupamento/reconciliação
    na execução corrente) e que a extração usa o teto geral."""

    async def handler(call_index: int, request) -> ProviderResponse:
        captured_max_tokens.append(request.max_tokens)
        return await _normal_processing_handler(provider_name, debate_text)(call_index, request)

    return handler


def _flaky_after_round1_handler(provider_name: str):
    """1a chamada (rodada inicial) sempre sucesso; a partir da 2a (rodada
    de crítica) sempre falha — simula um provider que respondeu bem na
    rodada 1 mas falha especificamente na crítica."""

    async def handler(call_index: int, request) -> ProviderResponse:
        if call_index == 1:
            return _ok(provider_name, "resposta da rodada inicial")
        return _err(provider_name)

    return handler


# ---------------------------------------------------------------------------
# Fluxo completo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_flow_3_of_3_runs_critique_and_produces_claims():
    providers = {
        "openai": _make_provider("openai", "Brasília é a capital."),
        "anthropic": _make_provider("anthropic", "A capital do Brasil é Brasília."),
        "gemini": _make_provider("gemini", "Brasília é a capital do país."),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(["openai", "anthropic", "gemini"], "anthropic")

    result = await engine.run(run_config)

    assert result.initial_result.successful_count == 3
    assert result.debate_skipped_reason is None
    assert result.critique_round is not None
    assert result.critique_round.critique_obtained is True
    assert result.critique_round.coverage_ratio == 1.0
    # 3 claims da rodada 1 + 3 claims da rodada 2 (extração simples; nenhuma
    # fusão/agrupamento/reconciliação existe na execução corrente)
    assert len(result.claims) == 6
    # SÓ extrações: 3 por rodada = 6 attempts, todos aceitos, nenhum
    # attempt de agrupamento/reconciliação
    assert len(result.claim_processing_attempts) == 6
    assert {a.operation for a in result.claim_processing_attempts} == {"extraction"}
    assert all(a.parse_status == "accepted" for a in result.claim_processing_attempts)
    assert all(a.provider == "anthropic" for a in result.claim_processing_attempts)


@pytest.mark.asyncio
async def test_claim_processor_can_also_be_a_debate_participant_without_extra_weight():
    """O processor (anthropic) participa do debate normalmente — suas
    claims não recebem tratamento privilegiado nenhum."""
    providers = {
        "openai": _make_provider("openai", "resposta A"),
        "anthropic": _make_provider("anthropic", "resposta B"),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai", "anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )
    result = await engine.run(run_config)
    assert result.initial_result.successful_count == 2
    providers_in_claims = {
        s.provider for c in result.claims for s in c.supporting_model_response_ids
    }
    assert providers_in_claims == {"openai", "anthropic"}


# ---------------------------------------------------------------------------
# Os 4 estados de cobertura da crítica
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critique_coverage_2_of_3():
    providers = {
        "openai": _make_provider("openai", "resposta A"),
        "anthropic": _make_provider("anthropic", "resposta B"),
        "gemini": CallableProvider("gemini", _flaky_after_round1_handler("gemini")),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(["openai", "anthropic", "gemini"], "anthropic")

    result = await engine.run(run_config)

    assert result.initial_result.successful_count == 3
    assert result.critique_round.round_result.successful_count == 2
    assert result.critique_round.critique_obtained is True
    assert result.critique_round.coverage_ratio == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_critique_coverage_1_of_3():
    providers = {
        "openai": CallableProvider("openai", _flaky_after_round1_handler("openai")),
        "anthropic": _make_provider("anthropic", "resposta B"),
        "gemini": CallableProvider("gemini", _flaky_after_round1_handler("gemini")),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(["openai", "anthropic", "gemini"], "anthropic")

    result = await engine.run(run_config)

    assert result.initial_result.successful_count == 3
    assert result.critique_round.round_result.successful_count == 1
    assert result.critique_round.critique_obtained is True
    assert result.critique_round.coverage_ratio == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_critique_coverage_0_of_3_does_not_raise_and_debate_still_returns():
    async def anthropic_flaky_critique(call_index, request):
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" in content:
            return _ok(
                "anthropic",
                json.dumps({"claims": [{"text": "claim de anthropic", "revises_claim_id": None}]}),
            )
        if "SUAS CLAIMS" in content:  # chamada de crítica — falha de propósito
            return _err("anthropic")
        return _ok("anthropic", "resposta da rodada inicial")

    providers = {
        "openai": CallableProvider("openai", _flaky_after_round1_handler("openai")),
        "anthropic": CallableProvider("anthropic", anthropic_flaky_critique),
        "gemini": CallableProvider("gemini", _flaky_after_round1_handler("gemini")),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(["openai", "anthropic", "gemini"], "anthropic")

    result = await engine.run(run_config)

    assert result.critique_round.round_result.successful_count == 0
    assert result.critique_round.critique_obtained is False
    assert result.critique_round.coverage_ratio == 0.0
    assert result.debate_skipped_reason is None
    assert len(result.claims) >= 3  # claims da rodada 1 seguem válidas


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_and_critique_responses_carry_their_own_contract_versions():
    providers = {
        "openai": _make_provider("openai", "Brasília é a capital."),
        "anthropic": _make_provider("anthropic", "A capital do Brasil é Brasília."),
        "gemini": _make_provider("gemini", "Brasília é a capital do país."),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(["openai", "anthropic", "gemini"], "anthropic")

    result = await engine.run(run_config)

    for response in result.initial_result.responses:
        assert response.request_provenance is not None
        assert response.request_provenance.contract_version == INITIAL_RESPONSE_CONTRACT_VERSION

    critique_responses = result.critique_round.round_result.responses
    assert len(critique_responses) > 0
    for response in critique_responses:
        assert response.request_provenance is not None
        assert response.request_provenance.contract_version == CRITIQUE_CONTRACT_VERSION

    # rodadas diferentes -- contract_version (e portanto provenance) nunca
    # se confunde entre resposta inicial e crítica.
    initial_digest = result.initial_result.responses[0].request_provenance.request_digest
    critique_digest = critique_responses[0].request_provenance.request_digest
    assert initial_digest != critique_digest


# ---------------------------------------------------------------------------
# Quórum da rodada inicial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_initial_quorum_skips_critique():
    providers = {
        "openai": CallableProvider("openai", lambda i, r: _err_coro("openai")),
        "anthropic": _make_provider("anthropic", "única resposta"),
        "gemini": CallableProvider("gemini", lambda i, r: _err_coro("gemini")),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai", "anthropic", "gemini"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )

    result = await engine.run(run_config)

    assert result.initial_result.successful_count == 1
    assert result.initial_result.insufficient_data_for_consensus is True
    assert result.critique_round is None
    assert result.debate_skipped_reason == "insufficient_initial_quorum"
    assert len(result.claims) >= 1


@pytest.mark.asyncio
async def test_zero_quorum_propagates_insufficient_quorum_error_without_processing_claims():
    providers = {
        "openai": CallableProvider("openai", lambda i, r: _err_coro("openai")),
        "anthropic": CallableProvider("anthropic", lambda i, r: _err_coro("anthropic")),
        "gemini": CallableProvider("gemini", lambda i, r: _err_coro("gemini")),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(["openai", "anthropic", "gemini"], "anthropic")

    with pytest.raises(InsufficientQuorumError):
        await engine.run(run_config)

    assert providers["anthropic"].call_count == 1  # só a tentativa da rodada 1


# ---------------------------------------------------------------------------
# Budget cumulativo — exemplo numérico exato do pedido
# ---------------------------------------------------------------------------


async def _budget_example_handler(call_index: int, request) -> ProviderResponse:
    content = request.messages[0].content
    if call_index == 1:
        return _ok("anthropic", "Brasília é a capital.", input_tokens=3000, output_tokens=1000)
    if call_index == 2:
        assert "RESPOSTA_A_ANALISAR" in content
        text = json.dumps({"claims": [{"text": "Brasília é a capital.", "revises_claim_id": None}]})
        return _ok("anthropic", text, input_tokens=1500, output_tokens=500)
    # a partir daqui (só alcançável se o gate de budget deixar a crítica
    # rodar): crítica + extração da crítica. NUNCA agrupamento/reconciliação.
    assert "CLAIMS_BRUTAS" not in content and "CLAIMS_ATUAIS" not in content
    if "RESPOSTA_A_ANALISAR" in content:
        text = json.dumps({"claims": [{"text": "Brasília segue a capital.", "revises_claim_id": None}]})
        return _ok("anthropic", text, input_tokens=100, output_tokens=50)
    return _ok("anthropic", "crítica: mantenho a resposta.", input_tokens=200, output_tokens=100)


@pytest.mark.asyncio
async def test_budget_gate_considers_all_calls_not_just_initial_result():
    """round1=4000 (3000+1000) + extraction=2000 (1500+500) = 6000 já
    esgota max_total_tokens=6000 -- o gate de budget antes da crítica
    precisa enxergar TODAS as chamadas de processamento (extração), não só
    InitialResponsesResult (2 chamadas reais; agrupamento não existe mais)."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_example_handler)}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_total_tokens=6000,
    )

    result = await engine.run(run_config)

    assert result.initial_result.total_input_tokens == 3000
    assert result.initial_result.total_output_tokens == 1000
    assert result.debate_skipped_reason == "budget_exhausted_before_critique"
    assert result.critique_round is None
    assert result.cumulative_budget_exceeded is True
    assert result.cumulative_input_tokens == 3000 + 1500
    assert result.cumulative_output_tokens == 1000 + 500
    assert providers["anthropic"].call_count == 2  # rodada inicial + extração


@pytest.mark.asyncio
async def test_budget_gate_skips_critique_when_initial_round_plus_extraction_exhaust_exactly():
    """Budget suficiente pra rodada inicial + extração, mas exatamente
    esgotado depois disso -- a crítica não roda (chamada nº 3 nunca
    acontece)."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_example_handler)}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_total_tokens=6000,  # 4000 (round1) + 2000 (extração) = 6000 exatos
    )

    result = await engine.run(run_config)

    assert providers["anthropic"].call_count == 2
    assert result.cumulative_input_tokens == 3000 + 1500
    assert result.cumulative_output_tokens == 1000 + 500


@pytest.mark.asyncio
async def test_budget_not_exceeded_allows_critique_to_proceed():
    providers = {"anthropic": CallableProvider("anthropic", _budget_example_handler)}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_total_tokens=100_000,
    )

    result = await engine.run(run_config)

    assert result.debate_skipped_reason is None
    assert result.critique_round is not None
    # rodada inicial + extração + crítica + extração da crítica -- nada mais
    assert providers["anthropic"].call_count == 4


# ---------------------------------------------------------------------------
# claim_processor_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_claim_processor_provider_raises_clear_error():
    providers = {"openai": _make_provider("openai", "resposta")}
    engine = DebateEngine(providers)
    run_config = _run_config(["openai"], "provider-que-nao-existe")

    with pytest.raises(ValueError, match="claim_processor_provider desconhecido"):
        await engine.run(run_config)


@pytest.mark.asyncio
async def test_claim_processor_provider_does_not_need_to_be_in_enabled_providers():
    providers = {
        "openai": _make_provider("openai", "resposta A"),
        "anthropic": _make_provider("anthropic", "resposta do processor dedicado"),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai"], "anthropic", quorum=QuorumPolicy(min_for_debate=1, min_to_return=1)
    )

    result = await engine.run(run_config)

    assert result.claim_processor_provider == "anthropic"
    assert all(a.provider == "anthropic" for a in result.claim_processing_attempts)
    supporting_providers = {
        s.provider for c in result.claims for s in c.supporting_model_response_ids
    }
    assert supporting_providers == {"openai"}


@pytest.mark.asyncio
async def test_budget_gate_a_initial_round_alone_exhausts_blocks_extraction():
    """Etapa 17A (B2), caso A: a rodada inicial SOZINHA (4000 tokens) já
    esgota o budget -- extração nunca é chamada (call_count == 1, só a
    rodada inicial)."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_example_handler)}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_total_tokens=3500,  # round1 sozinho (4000) já excede
    )

    result = await engine.run(run_config)

    assert providers["anthropic"].call_count == 1  # só a rodada inicial
    assert result.claims == []
    assert result.claim_processing_attempts == []


@pytest.mark.asyncio
async def test_budget_gate_b_extraction_alone_exhausts_skips_critique():
    """Caso B: extração sozinha já leva o total acima do budget -- a
    crítica nunca é chamada (call_count == 2, rodada inicial + extração)."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_example_handler)}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_total_tokens=5500,  # round1(4000)+extração(2000)=6000 excede; round1 sozinho não
    )

    result = await engine.run(run_config)

    assert providers["anthropic"].call_count == 2  # rodada inicial + extração


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo B) — agrupamento usa teto PRÓPRIO, distinto do geral
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_call_uses_max_output_tokens_grouping_and_extraction_keeps_the_general_ceiling():
    """`max_output_tokens_grouping` continua um campo de RunConfig (compat
    de reconstrução de runs históricas), mas NENHUMA chamada da execução
    corrente o usa: agrupamento/reconciliação não são mais agendados. A
    extração segue no teto GERAL (`max_output_tokens_per_call`)."""
    captured: list[int] = []
    providers = {
        "anthropic": CallableProvider(
            "anthropic",
            _capturing_handler("anthropic", "Brasília é a capital.", captured),
        )
    }
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=8000,
        max_output_tokens_judge=1024,
    )

    await engine.run(run_config)

    assert captured  # a execução realmente fez chamadas
    assert 8000 not in captured
    assert all(max_tokens == 1024 for max_tokens in captured)


# ---------------------------------------------------------------------------
# Repair (Run02 claim-extraction exhaustion) -- D: falha ESTRUTURAL total
# de extração na rodada 1 (quórum/budget suficientes) short-circuita antes
# da crítica; C.3: cobertura de extração PARCIAL é registrada, nunca
# confundida com falha total; budget/accounting: tentativas rejeitadas são
# cobradas exatamente uma vez.
# ---------------------------------------------------------------------------


def _always_malformed_extraction_handler(provider_name: str, **usage_overrides):
    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" not in content:
            raise AssertionError(
                f"chamada inesperada pro processor malformado ({provider_name}): "
                f"{content[:80]!r}"
            )
        return _ok(provider_name, "isto não é JSON válido", **usage_overrides)

    return handler


@pytest.mark.asyncio
async def test_all_initial_extractions_failed_short_circuits_before_critique():
    """Quórum (2/2) e budget suficientes, mas TODAS as extrações da
    rodada 1 falham estruturalmente (JSON sempre malformado, retry
    esgotado pras duas respostas) -- crítica NUNCA é disparada (nenhuma
    chamada de dispatch adicional aos participantes), e o skip é
    rotulado corretamente, nunca confundido com quórum/budget
    insuficientes."""
    openai = _make_provider("openai", "resposta de OPENAI")
    gemini = _make_provider("gemini", "resposta de GEMINI")
    processor = CallableProvider("anthropic", _always_malformed_extraction_handler("anthropic"))
    providers = {"openai": openai, "gemini": gemini, "anthropic": processor}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai", "gemini"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )

    result = await engine.run(run_config)

    assert result.initial_result.successful_count == 2
    assert result.initial_result.insufficient_data_for_consensus is False
    assert result.critique_round is None
    assert result.debate_skipped_reason == "all_initial_extractions_failed"
    assert result.cumulative_budget_exceeded is False
    assert result.claims == []
    # 2 respostas x 2 tentativas cada (retry esgotado, nunca aceito) = 4.
    assert len(result.claim_processing_attempts) == 4
    assert all(a.parse_status == "malformed" for a in result.claim_processing_attempts)
    # Nenhuma chamada de rodada de crítica aconteceu -- só a rodada inicial.
    assert openai.call_count == 1
    assert gemini.call_count == 1
    assert result.claim_extraction_eligible_response_count == 2
    assert result.claim_extraction_missing_response_count == 2


@pytest.mark.asyncio
async def test_all_initial_extractions_failed_charges_attempts_exactly_once():
    """Budget/accounting -- cada tentativa de extração rejeitada (mesmo
    malformada) consumiu tokens reais e precisa aparecer exatamente uma
    vez em cumulative_input_tokens/cumulative_output_tokens -- nunca
    duas vezes, nunca omitida."""
    openai = _make_provider("openai", "resposta de OPENAI")
    gemini = _make_provider("gemini", "resposta de GEMINI")
    processor = CallableProvider(
        "anthropic",
        _always_malformed_extraction_handler("anthropic", input_tokens=100, output_tokens=50),
    )
    providers = {"openai": openai, "gemini": gemini, "anthropic": processor}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai", "gemini"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )

    result = await engine.run(run_config)

    assert result.debate_skipped_reason == "all_initial_extractions_failed"
    assert len(result.claim_processing_attempts) == 4
    extraction_input = sum(a.usage.input_tokens for a in result.claim_processing_attempts)
    extraction_output = sum(a.usage.output_tokens for a in result.claim_processing_attempts)
    assert extraction_input == 400
    assert extraction_output == 200
    assert (
        result.cumulative_input_tokens
        == result.initial_result.total_input_tokens + extraction_input
    )
    assert (
        result.cumulative_output_tokens
        == result.initial_result.total_output_tokens + extraction_output
    )


@pytest.mark.asyncio
async def test_partial_extraction_coverage_is_disclosed_while_debate_proceeds_normally():
    """Uma resposta bem-sucedida (gemini) falha estruturalmente na
    extração em AMBAS as rodadas, enquanto openai segue normalmente -- o
    debate PROSSEGUE normalmente (openai teve extração aceita no round1,
    então nunca é "all_initial_extractions_failed"), e a cobertura
    incompleta fica corretamente registrada nos dois computed_field
    novos, nunca confundida com falha total nem com extração vazia
    válida."""

    async def processor_handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" in content:
            if "resposta de GEMINI" in content:
                return _ok("anthropic", "isto não é JSON válido")
            text = json.dumps({"claims": [{"text": "claim de openai", "revises_claim_id": None}]})
            return _ok("anthropic", text)
        raise AssertionError(f"chamada inesperada: {content[:80]!r}")

    providers = {
        "openai": _make_provider("openai", "resposta de OPENAI"),
        "gemini": _make_provider("gemini", "resposta de GEMINI"),
        "anthropic": CallableProvider("anthropic", processor_handler),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai", "gemini"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )

    result = await engine.run(run_config)

    assert result.debate_skipped_reason is None
    assert result.critique_round is not None
    # claim de openai no round1 + claim de openai no round2 (reconciliação
    # sem merge, mesmo padrão dos handlers padrão deste arquivo).
    assert len(result.claims) == 2
    assert {c.text for c in result.claims} == {"claim de openai"}
    # 4 ModelResponse bem-sucedidas no total (openai+gemini x 2 rounds).
    assert result.claim_extraction_eligible_response_count == 4
    # gemini falhou extração nas DUAS rodadas -- nunca omitido, nunca
    # confundido com "all_initial_extractions_failed" (openai teve
    # extração aceita).
    assert result.claim_extraction_missing_response_count == 2


@pytest.mark.asyncio
async def test_all_rejected_where_final_attempt_crosses_budget_is_budget_reason_not_total_failure():
    """Repair (adversarial review, Finding A) -- TODAS as tentativas de
    extração da rodada 1 são rejeitadas (malformadas), e a ÚLTIMA delas
    é o que faz o budget cumulativo cruzar o teto -- o gate de budget
    (que roda ANTES do short-circuit de falha total) precisa continuar
    tendo prioridade: o resultado é "budget_exhausted_before_critique",
    NUNCA "all_initial_extractions_failed" -- mesmo `accepted_count==0`
    sendo tecnicamente verdadeiro nos dois casos, a causa raiz aqui É
    budget, não falha estrutural."""
    openai = _make_provider("openai", "resposta de OPENAI")
    gemini = _make_provider("gemini", "resposta de GEMINI")
    processor = CallableProvider("anthropic", _always_malformed_extraction_handler("anthropic"))
    providers = {"openai": openai, "gemini": gemini, "anthropic": processor}
    engine = DebateEngine(providers)
    # 2 respostas x (10+5) tokens de dispatch = 30, + 4 tentativas de
    # extração rejeitadas x (10+5) tokens cada = 60 -- total 90,
    # cruzando exatamente max_total_tokens=90 (>= é "excedido", ver
    # app/orchestrator/budget.py) na ÚLTIMA tentativa rejeitada.
    run_config = _run_config(
        ["openai", "gemini"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        max_total_tokens=90,
    )

    result = await engine.run(run_config)

    assert result.debate_skipped_reason == "budget_exhausted_before_critique"
    assert result.cumulative_budget_exceeded is True
    assert result.critique_round is None
    assert all(a.parse_status == "malformed" for a in result.claim_processing_attempts)


@pytest.mark.asyncio
async def test_round_one_success_is_never_masked_by_total_critique_extraction_failure():
    """Repair (adversarial review, Finding A) -- round-safety end-to-end:
    a rodada 1 TEM extração aceita pras 2 respostas (nunca falha total),
    enquanto a rodada de crítica (round 2) falha extração pras MESMAS 2
    respostas inteiramente -- o short-circuit de falha total é
    ESCOPADO À RODADA 1 (compute_claim_extraction_targets recebe só
    round 1 nesse ponto), então nunca deveria confundir a falha da
    crítica com falha da rodada inicial."""

    async def processor_handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" in content:
            # Diferencia rodada 1 de rodada de crítica por `call_index`,
            # nunca por conteúdo do texto -- as 2 primeiras chamadas de
            # extração deste processor são SEMPRE as da rodada 1 (mesma
            # ordem determinística já usada por `_budget_example_handler`
            # acima: dispatch da rodada 1 termina, DEPOIS extração roda
            # sequencialmente por resposta -- tudo `await`ado em sequência,
            # nunca concorrente). As chamadas de
            # extração seguintes (rodada de crítica) sempre falham,
            # exercitando falha TOTAL da crítica sem afetar a rodada 1.
            if call_index <= 2:
                text = json.dumps(
                    {"claims": [{"text": "claim inicial", "revises_claim_id": None}]}
                )
                return _ok("anthropic", text)
            return _ok("anthropic", "isto não é JSON válido")
        raise AssertionError(f"chamada inesperada: {content[:80]!r}")

    providers = {
        "openai": _make_provider("openai", "resposta de OPENAI"),
        "gemini": _make_provider("gemini", "resposta de GEMINI"),
        "anthropic": CallableProvider("anthropic", processor_handler),
    }
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["openai", "gemini"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
    )

    result = await engine.run(run_config)

    assert result.debate_skipped_reason is None
    assert result.critique_round is not None
    assert len(result.claims) == 2  # só as 2 claims da rodada 1 (crítica falhou inteira)
    assert {c.text for c in result.claims} == {"claim inicial"}
    assert result.claim_extraction_eligible_response_count == 4
    assert result.claim_extraction_missing_response_count == 2  # só as 2 respostas de crítica
