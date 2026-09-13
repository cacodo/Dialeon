from __future__ import annotations

import json

import pytest

from app.debate.debate_engine import DebateEngine
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType, ProviderResponse, TokenUsage
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.orchestrator.errors import InsufficientQuorumError
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


def _ungrouped_response(provider_name: str, content: str, split_marker: str) -> ProviderResponse:
    """Resposta padrão de agrupamento/reconciliação: devolve TODOS os ids
    recebidos em `ungrouped_claim_ids`, sem nenhuma fusão -- merge/
    reconciliação com fusão real já são testados à parte
    (test_grouping.py, test_claim_extraction.py). Compartilhada entre
    `_normal_processing_handler`/`_capturing_handler` pras duas formas de
    chamada que compartilham o mesmo `ClaimGroupingOutput`
    (agrupamento="CLAIMS_BRUTAS:\\n", reconciliação="CLAIMS_ATUAIS ..." --
    ver `split_marker`)."""
    payload = json.loads(content.split(split_marker, 1)[1])
    ids = [c["id"] for c in payload]
    return _ok(provider_name, json.dumps({"groups": [], "ungrouped_claim_ids": ids}))


def _normal_processing_handler(provider_name: str, debate_text: str):
    """Handler padrão: extração devolve 1 claim; agrupamento E
    reconciliação (cross-round) devolvem tudo em ungrouped_claim_ids (sem
    fusão — fusão já é testada à parte em test_grouping.py/
    test_claim_extraction.py); qualquer outra chamada é tratada como
    resposta de debate normal (rodada inicial ou crítica)."""

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "CLAIMS_BRUTAS" in content:
            return _ungrouped_response(provider_name, content, "CLAIMS_BRUTAS:\n")
        if "CLAIMS_ATUAIS" in content:
            return _ungrouped_response(
                provider_name, content, "rodada de crítica combinadas):\n"
            )
        if "RESPOSTA_A_ANALISAR" in content:
            text = json.dumps(
                {"claims": [{"text": f"claim de {provider_name}", "revises_claim_id": None}]}
            )
            return _ok(provider_name, text)
        return _ok(provider_name, debate_text)

    return handler


def _make_provider(name: str, debate_text: str) -> CallableProvider:
    return CallableProvider(name, _normal_processing_handler(name, debate_text))


def _capturing_handler(provider_name: str, debate_text: str, captured_grouping_max_tokens: list):
    """Igual a `_normal_processing_handler`, mas guarda o `max_tokens`
    enviado em CADA chamada de agrupamento OU reconciliação (as duas
    compartilham `max_output_tokens_grouping`, ver
    app/debate/debate_engine.py) -- pra provar que essas chamadas usam
    `max_output_tokens_grouping`, distinto do `max_output_tokens_per_call`
    geral usado pra extração/crítica."""

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "CLAIMS_BRUTAS" in content:
            captured_grouping_max_tokens.append(request.max_tokens)
            return _ungrouped_response(provider_name, content, "CLAIMS_BRUTAS:\n")
        if "CLAIMS_ATUAIS" in content:
            captured_grouping_max_tokens.append(request.max_tokens)
            return _ungrouped_response(
                provider_name, content, "rodada de crítica combinadas):\n"
            )
        if "RESPOSTA_A_ANALISAR" in content:
            text = json.dumps(
                {"claims": [{"text": f"claim de {provider_name}", "revises_claim_id": None}]}
            )
            return _ok(provider_name, text)
        return _ok(provider_name, debate_text)

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
    # 3 claims da rodada 1 + 3 claims da rodada 2 (extração simples, sem merge) +
    # reconciliação cross-round sem fusão (fake handler devolve tudo
    # ungrouped) -- claims inalteradas
    assert len(result.claims) == 6
    # 3 extrações + 1 agrupamento por rodada (8) + 1 reconciliação
    # cross-round (ambos os lados -- R1 e R2 -- têm claims atuais) = 9
    # attempts, todos aceitos
    assert len(result.claim_processing_attempts) == 9
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
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok("anthropic", json.dumps({"groups": [], "ungrouped_claim_ids": ids}))
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
    if call_index == 3:
        assert "CLAIMS_BRUTAS" in content
        payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
        ids = [c["id"] for c in payload]
        text = json.dumps({"groups": [], "ungrouped_claim_ids": ids})
        return _ok("anthropic", text, input_tokens=700, output_tokens=300)
    raise AssertionError(f"chamada inesperada nº {call_index}")


@pytest.mark.asyncio
async def test_budget_gate_considers_all_calls_not_just_initial_result():
    """round1=4000 (3000+1000) + extraction=2000 (1500+500) = 6000 já
    esgota max_total_tokens=6000 -- Etapa 17A (B2): o agrupamento agora é
    gateado TAMBÉM antes de começar, então nem chega a ser chamado (só
    2 chamadas reais, não 3). Nome do teste preservado -- o ponto
    original (o gate de budget antes da crítica precisa enxergar TODAS
    as chamadas de processamento, não só InitialResponsesResult) continua
    verdadeiro e testado aqui, só que agora o agrupamento em si já para
    de rodar antes disso."""
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
    assert result.cumulative_input_tokens == 3000 + 1500  # agrupamento não rodou
    assert result.cumulative_output_tokens == 1000 + 500
    assert providers["anthropic"].call_count == 2  # rodada inicial + extração, sem agrupamento


@pytest.mark.asyncio
async def test_budget_gate_blocks_grouping_specifically_when_extraction_alone_exhausts():
    """Etapa 17A (B2), caso específico: budget suficiente pra rodada
    inicial + extração, mas exatamente esgotado depois disso -- prova
    que o agrupamento não roda (chamada nº 3 nunca acontece), distinto
    do gate antes da crítica (que roda depois, sobre o total já
    reduzido)."""
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
    assert providers["anthropic"].call_count > 3


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
async def test_budget_gate_b_extraction_alone_exhausts_blocks_grouping():
    """Etapa 17A (B2), caso B: extração sozinha já leva o total acima do
    budget -- agrupamento nunca é chamado (call_count == 2, rodada
    inicial + extração, sem agrupamento)."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_example_handler)}
    engine = DebateEngine(providers)
    run_config = _run_config(
        ["anthropic"], "anthropic",
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        max_total_tokens=5500,  # round1(4000)+extração(2000)=6000 excede; round1 sozinho não
    )

    result = await engine.run(run_config)

    assert providers["anthropic"].call_count == 2  # rodada inicial + extração, sem agrupamento


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo B) — agrupamento usa teto PRÓPRIO, distinto do geral
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grouping_call_uses_max_output_tokens_grouping_not_the_general_ceiling():
    """`max_output_tokens_per_call` (geral, usado por extração/crítica) e
    `max_output_tokens_grouping` (próprio do agrupamento) precisam ser
    valores DIFERENTES forwarded pra CompletionRequest.max_tokens
    corretos -- prova direta de que debate_engine._process_round lê o
    campo certo do RunConfig pra cada tipo de chamada."""
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

    assert captured  # a chamada de agrupamento realmente aconteceu
    assert all(max_tokens == 8000 for max_tokens in captured)
    # confirma que os dois tetos são de fato distintos nesta chamada
    assert 8000 != run_config.max_output_tokens_per_call

    # extração continua usando o teto GERAL, não o de agrupamento
    extraction_requests = [
        req
        for req in providers["anthropic"].received_requests
        if "RESPOSTA_A_ANALISAR" in req.messages[0].content
    ]
    assert extraction_requests
    assert all(req.max_tokens == 1024 for req in extraction_requests)
