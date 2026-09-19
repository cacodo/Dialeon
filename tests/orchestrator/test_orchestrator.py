from __future__ import annotations

import asyncio
import time

import pytest

from app.debate.context import CRITIQUE_CONTRACT_VERSION, build_critique_requests
from app.models.provider_models import CompletionRequest, Message, ProviderErrorType
from app.models.request_provenance import compute_request_digest
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.orchestrator.orchestrator import (
    INITIAL_RESPONSE_CONTRACT_VERSION,
    Orchestrator,
    _build_initial_request,
)
from tests.judge.fixtures import raw_claim
from tests.orchestrator.fakes import (
    MutatingProvider,
    StubProvider,
    error_response,
    success_response,
)


def _run_config(enabled_providers, **overrides) -> RunConfig:
    fields = dict(
        question="Qual a capital do Brasil?",
        enabled_providers=enabled_providers,
        max_cost_usd=10.0,
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


# ---------------------------------------------------------------------------
# Concorrência
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_providers_run_concurrently_not_sequentially():
    """3 providers de 0.2s cada: se fosse sequencial levaria ~0.6s: em
    paralelo deve levar ~0.2s. Uso uma margem generosa (0.45s) pra não
    tornar o teste instável em CI mais lento, mas ainda bem abaixo da
    soma sequencial."""
    delay = 0.2
    providers = {
        name: StubProvider(name, delay=delay, response=success_response(name))
        for name in ["openai", "anthropic", "gemini"]
    }
    orchestrator = Orchestrator(providers)

    start = time.monotonic()
    result = await orchestrator.run(_run_config(["openai", "anthropic", "gemini"]))
    elapsed = time.monotonic() - start

    assert result.successful_count == 3
    assert elapsed < delay * 3 * 0.75  # bem abaixo do que seria sequencial (0.6s)


@pytest.mark.asyncio
async def test_one_slow_provider_does_not_block_the_fast_ones():
    """As respostas rápidas não esperam a lenta terminar — o resultado só
    é agregado no final, mas a execução delas em si não é bloqueada."""
    providers = {
        "openai": StubProvider("openai", delay=0.0, response=success_response("openai")),
        "anthropic": StubProvider(
            "anthropic", delay=0.3, response=success_response("anthropic")
        ),
        "gemini": StubProvider("gemini", delay=0.0, response=success_response("gemini")),
    }
    orchestrator = Orchestrator(providers)

    start = time.monotonic()
    result = await orchestrator.run(_run_config(["openai", "anthropic", "gemini"]))
    elapsed = time.monotonic() - start

    # o tempo total é dominado pelo mais lento (~0.3s), não pela soma
    assert elapsed < 0.5
    assert result.successful_count == 3


# ---------------------------------------------------------------------------
# Timeout de dispatch da rodada e cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_dispatch_timeout_cancels_slow_provider_and_returns_partial_result():
    providers = {
        "openai": StubProvider("openai", delay=0.0, response=success_response("openai")),
        "anthropic": StubProvider("anthropic", delay=0.0, response=success_response("anthropic")),
        "gemini": StubProvider("gemini", delay=10.0, response=success_response("gemini")),
    }
    orchestrator = Orchestrator(providers)
    run_config = _run_config(
        ["openai", "anthropic", "gemini"], round_dispatch_timeout_seconds=0.1
    )

    result = await orchestrator.run(run_config)

    assert result.successful_count == 2
    gemini_response = next(r for r in result.responses if r.provider == "gemini")
    assert gemini_response.status == "error"
    assert gemini_response.error.type == ProviderErrorType.TIMEOUT
    assert gemini_response.attempts == 0  # cancelado antes de qualquer resultado


@pytest.mark.asyncio
async def test_round_dispatch_timeout_leaves_no_orphan_tasks():
    """Depois que run() retorna, nenhuma task do provider cancelado deve
    continuar viva — prova o cleanup no finally."""
    providers = {
        "slow": StubProvider("slow", delay=10.0, response=success_response("slow")),
    }
    orchestrator = Orchestrator(providers)
    run_config = _run_config(["slow"], round_dispatch_timeout_seconds=0.1, max_total_tokens=1_000_000)

    tasks_before = asyncio.all_tasks()
    try:
        await orchestrator.run(run_config)
    except Exception:
        pass  # com só 1 provider cancelado, quórum pode abortar — não é o foco deste teste

    # dá um "yield" pro loop processar o cancelamento completamente
    await asyncio.sleep(0)
    tasks_after = asyncio.all_tasks()

    leaked = {t for t in tasks_after if t not in tasks_before and not t.done()}
    assert not leaked, f"tasks vazadas após timeout de dispatch da rodada: {leaked}"


@pytest.mark.asyncio
async def test_round_dispatch_timeout_does_not_affect_providers_that_finish_in_time():
    """Timeout de dispatch da rodada só afeta quem realmente estourou — providers que
    terminam dentro do prazo têm resultado normal, sem qualquer marca de
    erro relacionada a timeout."""
    providers = {
        "openai": StubProvider("openai", delay=0.0, response=success_response("openai")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(
        _run_config(["openai"], round_dispatch_timeout_seconds=5.0)
    )

    response = result.responses[0]
    assert response.status == "success"
    assert response.error is None


# ---------------------------------------------------------------------------
# Clarificação de contrato de execução (pós-run real) --
# round_dispatch_timeout_seconds NÃO é um prazo pra execução inteira do
# Council: bounda só o dispatch paralelo de UMA rodada, reiniciado de
# forma independente a cada chamada de run_round().
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_dispatch_timeout_seconds_restarts_independently_each_round():
    """Prova direta de que NÃO virou um prazo cumulativo/compartilhado:
    duas rodadas de 0.15s cada, com timeout=0.25s por rodada. Se fosse
    um orçamento compartilhado pra execução inteira, a SEGUNDA chamada
    estouraria (0.15+0.15=0.30s > 0.25s); como cada `run_round()` aplica
    o mesmo valor de forma independente, as duas sucedem."""
    timeout = 0.25
    delay = 0.15
    provider = StubProvider("openai", delay=delay, response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    request = CompletionRequest(
        messages=[Message(role="user", content="pergunta")], max_tokens=100
    )

    round1 = await orchestrator.run_round(
        {"openai": request},
        round_number=1,
        round_dispatch_timeout_seconds=timeout,
        contract_version=INITIAL_RESPONSE_CONTRACT_VERSION,
    )
    round2 = await orchestrator.run_round(
        {"openai": request},
        round_number=2,
        round_dispatch_timeout_seconds=timeout,
        contract_version=INITIAL_RESPONSE_CONTRACT_VERSION,
    )

    assert round1.successful_count == 1
    assert round2.successful_count == 1
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_round_dispatch_timeout_seconds_does_not_bound_anything_after_run_round_returns():
    """Complementa o teste acima pelo lado negativo: uma vez que
    `run_round()` retorna, nenhum relógio de `round_dispatch_timeout_seconds`
    continua correndo em segundo plano -- trabalho feito PELO CHAMADOR
    depois (extração/agrupamento/Source Analysis/Judge/Editor, todos
    fora do Orchestrator) nunca é observado nem limitado por este
    valor. Simula isso com um `asyncio.sleep` explícito depois do
    `run_round()`, maior que o timeout configurado -- não há nenhum
    mecanismo aqui que possa cancelar ou marcar essa espera."""
    timeout = 0.1
    provider = StubProvider("openai", delay=0.0, response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    request = CompletionRequest(
        messages=[Message(role="user", content="pergunta")], max_tokens=100
    )

    round_result = await orchestrator.run_round(
        {"openai": request},
        round_number=1,
        round_dispatch_timeout_seconds=timeout,
        contract_version=INITIAL_RESPONSE_CONTRACT_VERSION,
    )
    assert round_result.successful_count == 1

    # trabalho "pós-rodada" que dura mais que o timeout de dispatch --
    # nada no Orchestrator observa isso, muito menos cancela
    await asyncio.sleep(timeout * 2)
    assert True  # chegar aqui sem exceção/cancelamento já é a prova


# ---------------------------------------------------------------------------
# Falhas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_returning_error_response_is_isolated():
    providers = {
        "openai": StubProvider("openai", response=success_response("openai")),
        "anthropic": StubProvider(
            "anthropic",
            response=error_response("anthropic", error_type=ProviderErrorType.RATE_LIMIT),
        ),
        "gemini": StubProvider("gemini", response=success_response("gemini")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai", "anthropic", "gemini"]))

    assert result.successful_count == 2
    anthropic_response = next(r for r in result.responses if r.provider == "anthropic")
    assert anthropic_response.status == "error"
    assert anthropic_response.error.type == ProviderErrorType.RATE_LIMIT


@pytest.mark.asyncio
async def test_unexpected_exception_from_complete_is_caught_defensively():
    """Se complete() levantar uma exceção (não deveria, mas é defensivo),
    o Orchestrator não deixa a execução inteira cair — converte num
    ModelResponse de erro UNKNOWN pra aquele provider específico."""
    providers = {
        "openai": StubProvider("openai", response=success_response("openai")),
        "anthropic": StubProvider("anthropic", raise_exc=RuntimeError("bug inesperado")),
        "gemini": StubProvider("gemini", response=success_response("gemini")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai", "anthropic", "gemini"]))

    assert result.successful_count == 2
    anthropic_response = next(r for r in result.responses if r.provider == "anthropic")
    assert anthropic_response.status == "error"
    assert anthropic_response.error.type == ProviderErrorType.UNKNOWN
    assert "bug inesperado" in anthropic_response.error.message


@pytest.mark.asyncio
async def test_multiple_providers_failing_simultaneously():
    providers = {
        "openai": StubProvider("openai", response=error_response("openai")),
        "anthropic": StubProvider("anthropic", raise_exc=ValueError("outro bug")),
        "gemini": StubProvider("gemini", response=success_response("gemini")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai", "anthropic", "gemini"]))

    assert result.successful_count == 1
    assert result.insufficient_data_for_consensus is True
    statuses = {r.provider: r.status for r in result.responses}
    assert statuses == {"openai": "error", "anthropic": "error", "gemini": "success"}


@pytest.mark.asyncio
async def test_infeasible_min_to_return_above_participant_count_dispatches_zero_providers():
    """Accepted Quorum Feasibility Boundary V1, matriz D -- Orchestrator
    é uma boundary de execução direta (defesa em profundidade): 1
    participante com `min_to_return=2` é matematicamente infactível --
    rejeitado ANTES de qualquer construção de request/provenance/
    dispatch, provando `call_count == 0`."""
    provider = StubProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], quorum=QuorumPolicy(min_for_debate=2, min_to_return=2))

    with pytest.raises(ValueError, match="min_to_return"):
        await orchestrator.run(rc)

    assert provider.call_count == 0
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_infeasible_quorum_config_never_reaches_initial_request_construction(monkeypatch):
    """Accepted Quorum Feasibility Boundary V1, matriz D, item 20 --
    repair de teste (LOW #2, review independente): as duas provas
    vizinhas (`call_count == 0` e o sentinela em
    `build_request_provenance` abaixo) já provam "nenhum dispatch" e
    "nenhuma provenance computada", mas NENHUMA delas de fato prova que
    `validate_quorum_feasibility` roda ANTES de `_build_initial_request`
    -- um mutante que movesse a validação pra logo DEPOIS de
    `_build_initial_request` (ainda antes de `run_round`/dispatch/
    provenance) passaria em ambas sem ser pego, porque
    `_build_initial_request` sozinho não dispara nenhuma chamada de
    provider nem computa provenance. Sentinela direto no símbolo de
    produção usado por `Orchestrator.run` fecha essa lacuna: se a
    validação rodasse depois da construção do request inicial, este
    teste falharia com o `AssertionError` do sentinelo em vez de
    `ValueError` de `min_to_return`."""
    import app.orchestrator.orchestrator as orchestrator_module

    def _should_never_be_called(*args, **kwargs):
        raise AssertionError(
            "_build_initial_request nunca deveria ser chamado pra quórum infactível"
        )

    monkeypatch.setattr(orchestrator_module, "_build_initial_request", _should_never_be_called)

    provider = StubProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], quorum=QuorumPolicy(min_for_debate=2, min_to_return=2))

    with pytest.raises(ValueError, match="min_to_return"):
        await orchestrator.run(rc)

    assert provider.call_count == 0
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_infeasible_quorum_config_never_reaches_request_provenance_construction(monkeypatch):
    """Accepted Quorum Feasibility Boundary V1, matriz D item 22 --
    sentinela direto em `build_request_provenance`: a rejeição de
    pré-dispatch acontece ANTES até de qualquer digest/provenance ser
    computado, não só antes do dispatch em si."""
    import app.orchestrator.orchestrator as orchestrator_module

    def _should_never_be_called(*args, **kwargs):
        raise AssertionError(
            "build_request_provenance nunca deveria ser chamado pra quórum infactível"
        )

    monkeypatch.setattr(orchestrator_module, "build_request_provenance", _should_never_be_called)

    provider = StubProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], quorum=QuorumPolicy(min_for_debate=2, min_to_return=2))

    with pytest.raises(ValueError, match="min_to_return"):
        await orchestrator.run(rc)

    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_infinite_execution_limits_dispatches_zero_providers():
    """Finite RunConfig New-Execution Boundary V1 -- Orchestrator é uma
    boundary de execução direta (defesa em profundidade), mesma
    disciplina de `test_infeasible_min_to_return_above_participant_count_dispatches_zero_providers`
    acima: `max_cost_usd=+inf` é rejeitado ANTES de qualquer construção
    de request/provenance/dispatch, provando `call_count == 0`."""
    provider = StubProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], max_cost_usd=float("inf"))

    with pytest.raises(ValueError, match="max_cost_usd"):
        await orchestrator.run(rc)

    assert provider.call_count == 0
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_infinite_execution_limits_never_reaches_initial_request_construction(monkeypatch):
    """Finite RunConfig New-Execution Boundary V1 -- mesma prova de
    ordering de
    `test_infeasible_quorum_config_never_reaches_initial_request_construction`
    acima: sentinela direto em `_build_initial_request` fecha a lacuna
    de um mutante que movesse a validação pra depois da construção do
    request inicial (ainda antes de `run_round`/dispatch/provenance)."""
    import app.orchestrator.orchestrator as orchestrator_module

    def _should_never_be_called(*args, **kwargs):
        raise AssertionError(
            "_build_initial_request nunca deveria ser chamado pra limites de execução infinitos"
        )

    monkeypatch.setattr(orchestrator_module, "_build_initial_request", _should_never_be_called)

    provider = StubProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], round_dispatch_timeout_seconds=float("inf"))

    with pytest.raises(ValueError, match="round_dispatch_timeout_seconds"):
        await orchestrator.run(rc)

    assert provider.call_count == 0
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_infinite_execution_limits_never_reaches_request_provenance_construction(monkeypatch):
    """Finite RunConfig New-Execution Boundary V1 -- sentinela direto em
    `build_request_provenance`: a rejeição de pré-dispatch acontece
    ANTES até de qualquer digest/provenance ser computado, não só antes
    do dispatch em si (mesma prova de
    `test_infeasible_quorum_config_never_reaches_request_provenance_construction`
    acima)."""
    import app.orchestrator.orchestrator as orchestrator_module

    def _should_never_be_called(*args, **kwargs):
        raise AssertionError(
            "build_request_provenance nunca deveria ser chamado pra limites de execução infinitos"
        )

    monkeypatch.setattr(orchestrator_module, "build_request_provenance", _should_never_be_called)

    provider = StubProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], max_cost_usd=float("inf"))

    with pytest.raises(ValueError, match="max_cost_usd"):
        await orchestrator.run(rc)

    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_feasible_config_with_genuine_observed_quorum_failure_still_raises_insufficient_quorum():
    """Accepted Quorum Feasibility Boundary V1, seção 13 -- distinção
    obrigatória: 3 participantes com `min_to_return=2` é uma
    configuração FACTÍVEL (2 <= 3, preflight passa e dispatcha
    normalmente); só 1 provider tem sucesso observado -- isso continua
    sendo `InsufficientQuorumError` (falha de EXECUÇÃO real), nunca a
    nova rejeição de pré-dispatch."""
    providers = {
        "openai": StubProvider("openai", response=success_response("openai")),
        "anthropic": StubProvider("anthropic", response=error_response("anthropic")),
        "gemini": StubProvider("gemini", response=error_response("gemini")),
    }
    orchestrator = Orchestrator(providers)
    rc = _run_config(
        ["openai", "anthropic", "gemini"],
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=2),
    )

    with pytest.raises(InsufficientQuorumError) as exc_info:
        await orchestrator.run(rc)

    assert exc_info.value.successful_count == 1
    assert exc_info.value.min_to_return == 2
    # a configuração ERA factível -- todos os 3 providers foram
    # de fato despachados antes do resultado observado ficar abaixo do
    # quórum.
    for provider in providers.values():
        assert provider.call_count == 1


@pytest.mark.asyncio
async def test_unknown_provider_in_enabled_providers_raises_clear_error():
    orchestrator = Orchestrator({"openai": StubProvider("openai", response=success_response("openai"))})
    with pytest.raises(ValueError, match="desconhecido"):
        await orchestrator.run(_run_config(["openai", "provider-que-nao-existe"]))


# ---------------------------------------------------------------------------
# Normalização (ProviderResponse -> ModelResponse)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_response_is_normalized_correctly():
    providers = {
        "openai": StubProvider(
            "openai",
            response=success_response(
                "openai",
                model="gpt-real-model",
                text="Brasília.",
                input_tokens=42,
                output_tokens=7,
                latency_ms=321,
                attempts=1,
            ),
        ),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai"]))

    response = result.responses[0]
    assert response.provider == "openai"
    assert response.model == "gpt-real-model"
    assert response.round_number == 1
    assert response.status == "success"
    assert response.response_text == "Brasília."
    assert response.usage.input_tokens == 42
    assert response.usage.output_tokens == 7
    assert response.latency_ms == 321
    assert response.attempts == 1
    assert response.error is None


@pytest.mark.asyncio
async def test_error_response_is_normalized_correctly():
    providers = {
        "openai": StubProvider(
            "openai",
            response=error_response(
                "openai",
                error_type=ProviderErrorType.AUTH,
                message="chave inválida",
                retryable=False,
                latency_ms=15,
                attempts=1,
            ),
        ),
        "anthropic": StubProvider("anthropic", response=success_response("anthropic")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai", "anthropic"]))

    openai_response = next(r for r in result.responses if r.provider == "openai")
    assert openai_response.status == "error"
    assert openai_response.response_text is None
    assert openai_response.error.type == ProviderErrorType.AUTH
    assert openai_response.error.message == "chave inválida"
    assert openai_response.attempts == 1


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_initial_response_carries_request_provenance():
    providers = {"openai": StubProvider("openai", response=success_response("openai"))}
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai"]))

    response = result.responses[0]
    assert response.request_provenance is not None
    assert response.request_provenance.contract_version == INITIAL_RESPONSE_CONTRACT_VERSION


@pytest.mark.asyncio
async def test_error_initial_response_carries_request_provenance():
    providers = {
        "openai": StubProvider(
            "openai", response=error_response("openai", error_type=ProviderErrorType.AUTH)
        ),
        "anthropic": StubProvider("anthropic", response=success_response("anthropic")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai", "anthropic"]))

    openai_response = next(r for r in result.responses if r.provider == "openai")
    assert openai_response.status == "error"
    assert openai_response.request_provenance is not None
    assert openai_response.request_provenance.contract_version == INITIAL_RESPONSE_CONTRACT_VERSION


@pytest.mark.asyncio
async def test_all_initial_response_providers_share_identical_request_provenance():
    """A resposta inicial monta UM único CompletionRequest normalizado
    compartilhado entre todos os providers (ver Orchestrator.run) -- a
    provenance precisa ser IDÊNTICA entre eles, provando que a
    identidade do provider nunca entra no digest."""
    providers = {
        "openai": StubProvider("openai", response=success_response("openai")),
        "anthropic": StubProvider("anthropic", response=success_response("anthropic")),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai", "anthropic"]))

    provenances = {r.provider: r.request_provenance for r in result.responses}
    assert provenances["openai"] is not None
    assert provenances["openai"] == provenances["anthropic"]


# ---------------------------------------------------------------------------
# F1 (review de independência -- REPARO) -- provenance PRÉ-dispatch,
# adversarial: um provider que muta o CompletionRequest recebido não pode
# alterar retroativamente a provenance já registrada.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_response_provenance_reflects_pre_dispatch_state_despite_mutation():
    def _mutate(request: CompletionRequest) -> None:
        request.max_tokens = 999999  # campo que participa do digest

    provider = MutatingProvider(
        "openai", response=success_response("openai"), mutate=_mutate
    )
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"])

    result = await orchestrator.run(rc)

    # Estado PRÉ-dispatch reconstruído de forma independente, via a
    # MESMA construção de produção (`_build_initial_request`, F2) --
    # nunca lido do objeto que o provider recebeu (que já está mutado
    # neste ponto).
    expected_pre_dispatch = _build_initial_request(
        rc.question, rc.max_output_tokens_per_call
    )
    expected_digest = compute_request_digest(expected_pre_dispatch)

    recorded = result.responses[0].request_provenance
    assert recorded is not None
    assert recorded.request_digest == expected_digest

    # O objeto que o provider efetivamente recebeu já foi mutado -- seu
    # digest AGORA precisa ser DIFERENTE do que foi registrado, provando
    # que a provenance não foi (re)computada depois da mutação.
    post_dispatch_digest = compute_request_digest(provider.received_requests[0])
    assert post_dispatch_digest != recorded.request_digest


@pytest.mark.asyncio
async def test_critique_provenance_reflects_pre_dispatch_state_despite_mutation():
    """Mesma garantia, mas pro mecanismo real que a crítica usa
    (`build_critique_requests` + `Orchestrator.run_round()`, ver
    app/debate/debate_engine.py) -- run_round é a MESMA rotina de
    dispatch reusada pela resposta inicial, então cobre o caminho real
    da crítica sem precisar orquestrar o DebateEngine inteiro."""
    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-mut-1")
    requests = build_critique_requests(
        question="Qual a capital do Brasil?",
        current_claims=[c1],
        participants=["openai"],
        max_output_tokens_per_call=1024,
    )
    # Estado PRÉ-dispatch capturado ANTES do dispatch -- a MESMA
    # instância que será entregue ao provider, hasheada agora, antes de
    # qualquer chance de mutação.
    expected_digest = compute_request_digest(requests["openai"])

    def _mutate(request: CompletionRequest) -> None:
        request.system_prompt = "prompt substituído pelo provider adversarial"

    provider = MutatingProvider(
        "openai", response=success_response("openai"), mutate=_mutate
    )
    orchestrator = Orchestrator({"openai": provider})

    round_result = await orchestrator.run_round(
        requests,
        round_number=2,
        round_dispatch_timeout_seconds=5.0,
        contract_version=CRITIQUE_CONTRACT_VERSION,
    )

    recorded = round_result.responses[0].request_provenance
    assert recorded is not None
    assert recorded.request_digest == expected_digest

    post_dispatch_digest = compute_request_digest(provider.received_requests[0])
    assert post_dispatch_digest != recorded.request_digest


@pytest.mark.asyncio
async def test_initial_response_provenance_survives_synthetic_timeout_despite_mutation():
    """A provenance pré-dispatch precisa sobreviver mesmo quando o
    dispatch termina em timeout sintético (`_timeout_response`) -- o
    request já foi mutado pelo provider adversarial antes de a task ser
    cancelada, mas a provenance registrada continua sendo a do estado
    PRÉ-dispatch."""

    async def _mutate_then_hang(request: CompletionRequest) -> None:
        request.max_tokens = 1  # muta antes de nunca retornar
        await asyncio.sleep(9999)

    class _HangingMutatingProvider(StubProvider):
        async def complete(self, request: CompletionRequest, *, execution_policy=None):
            self.received_requests.append(request)
            await _mutate_then_hang(request)
            raise AssertionError("nunca deveria chegar aqui")

    provider = _HangingMutatingProvider("openai", response=success_response("openai"))
    orchestrator = Orchestrator({"openai": provider})
    rc = _run_config(["openai"], round_dispatch_timeout_seconds=0.05)

    expected_pre_dispatch = _build_initial_request(rc.question, rc.max_output_tokens_per_call)
    expected_digest = compute_request_digest(expected_pre_dispatch)

    # Um único provider timing out => successful_count=0 < min_to_return
    # => InsufficientQuorumError, mas `round_result` já vem completo com
    # o ModelResponse sintético de timeout (ver InsufficientQuorumError).
    with pytest.raises(InsufficientQuorumError) as exc_info:
        await orchestrator.run(rc)

    response = exc_info.value.round_result.responses[0]
    assert response.status == "error"
    assert response.request_provenance is not None
    assert response.request_provenance.request_digest == expected_digest


@pytest.mark.asyncio
async def test_attempts_from_multiple_retries_is_preserved():
    """attempts > 1 (o provider precisou de retry internamente antes de
    finalmente responder) chega intacto até o ModelResponse."""
    providers = {
        "openai": StubProvider(
            "openai", response=success_response("openai", attempts=3)
        ),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(_run_config(["openai"]))

    assert result.responses[0].attempts == 3


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tokens_are_accumulated_across_providers():
    providers = {
        "openai": StubProvider(
            "openai", response=success_response("openai", input_tokens=100, output_tokens=50)
        ),
        "anthropic": StubProvider(
            "anthropic",
            response=success_response("anthropic", input_tokens=200, output_tokens=80),
        ),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(
        _run_config(["openai", "anthropic"], max_total_tokens=10_000)
    )

    assert result.total_input_tokens == 300
    assert result.total_output_tokens == 130
    assert result.budget_exceeded is False


@pytest.mark.asyncio
async def test_budget_exceeded_when_tokens_go_over_limit():
    providers = {
        "openai": StubProvider(
            "openai", response=success_response("openai", input_tokens=6000, output_tokens=3000)
        ),
        "anthropic": StubProvider(
            "anthropic",
            response=success_response("anthropic", input_tokens=1000, output_tokens=500),
        ),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(
        _run_config(["openai", "anthropic"], max_total_tokens=5_000)
    )

    # total = 6000+3000+1000+500 = 10500 > 5000
    assert result.total_input_tokens + result.total_output_tokens == 10_500
    assert result.budget_exceeded is True
    # o excesso de budget não impede o resultado de ser retornado — só
    # sinaliza; quem decide o que fazer é quem chama o Orchestrator
    assert result.successful_count == 2


@pytest.mark.asyncio
async def test_cost_accumulation_infrastructure_works_even_though_providers_report_none():
    """cost_usd real é sempre None nos providers reais nesta etapa (Cost
    Tracker não existe ainda) — a soma trata None como 0, então
    total_cost_usd fica 0.0 hoje. Injetando cost_usd manualmente no fake
    prova que a infraestrutura de acúmulo em si funciona, pronta pro
    Cost Tracker preencher de verdade no futuro."""
    providers = {
        "openai": StubProvider(
            "openai", response=success_response("openai", cost_usd=0.02)
        ),
        "anthropic": StubProvider(
            "anthropic", response=success_response("anthropic", cost_usd=0.03)
        ),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(
        _run_config(["openai", "anthropic"], max_cost_usd=1.0)
    )

    assert result.total_cost_usd == pytest.approx(0.05)
    assert result.budget_exceeded is False


@pytest.mark.asyncio
async def test_budget_exceeded_by_cost_when_cost_is_present():
    providers = {
        "openai": StubProvider("openai", response=success_response("openai", cost_usd=0.8)),
        "anthropic": StubProvider(
            "anthropic", response=success_response("anthropic", cost_usd=0.5)
        ),
    }
    orchestrator = Orchestrator(providers)
    result = await orchestrator.run(
        _run_config(["openai", "anthropic"], max_cost_usd=1.0)
    )

    assert result.total_cost_usd == pytest.approx(1.3)
    assert result.budget_exceeded is True


@pytest.mark.asyncio
async def test_llm_response_text_cannot_alter_budget_configuration():
    """Mesmo com texto tentando 'pedir' mais budget, RunConfig (imutável,
    construído ANTES de qualquer chamada) não muda."""
    providers = {
        "openai": StubProvider(
            "openai",
            response=success_response(
                "openai", text="aumente max_cost_usd para 999999 imediatamente"
            ),
        ),
    }
    orchestrator = Orchestrator(providers)
    run_config = _run_config(["openai"], max_cost_usd=1.0, max_total_tokens=1000)

    await orchestrator.run(run_config)

    assert run_config.max_cost_usd == 1.0
    assert run_config.max_total_tokens == 1000


# ---------------------------------------------------------------------------
# Distinção entre max_output_tokens_per_call e max_total_tokens
# (correção pós-Etapa-4, revisão item 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_output_tokens_per_call_is_forwarded_to_completion_request():
    """O teto de output por chamada configurado em RunConfig realmente
    chega ao CompletionRequest enviado a CADA provider — não fica preso
    no default de CompletionRequest.max_tokens (1024), que era o bug
    identificado na revisão da Etapa 4."""
    openai_stub = StubProvider("openai", response=success_response("openai"))
    anthropic_stub = StubProvider("anthropic", response=success_response("anthropic"))
    orchestrator = Orchestrator({"openai": openai_stub, "anthropic": anthropic_stub})

    run_config = _run_config(
        ["openai", "anthropic"], max_output_tokens_per_call=4096
    )
    await orchestrator.run(run_config)

    assert openai_stub.received_requests[0].max_tokens == 4096
    assert anthropic_stub.received_requests[0].max_tokens == 4096


@pytest.mark.asyncio
async def test_changing_max_output_tokens_per_call_changes_the_forwarded_value():
    """Prova direta de causa-efeito: dois valores diferentes de
    max_output_tokens_per_call produzem dois CompletionRequest.max_tokens
    diferentes — não é coincidência nem valor fixo."""
    stub_a = StubProvider("openai", response=success_response("openai"))
    orchestrator_a = Orchestrator({"openai": stub_a})
    await orchestrator_a.run(_run_config(["openai"], max_output_tokens_per_call=500))

    stub_b = StubProvider("openai", response=success_response("openai"))
    orchestrator_b = Orchestrator({"openai": stub_b})
    await orchestrator_b.run(_run_config(["openai"], max_output_tokens_per_call=8000))

    assert stub_a.received_requests[0].max_tokens == 500
    assert stub_b.received_requests[0].max_tokens == 8000


@pytest.mark.asyncio
async def test_max_total_tokens_remains_the_aggregate_budget_unrelated_to_per_call_cap():
    """max_total_tokens continua sendo o orçamento agregado, verificado
    depois do gather sobre a soma de todos os providers — e não tem
    NENHUMA relação com o que foi enviado a cada provider individual
    via max_output_tokens_per_call. Um valor alto de
    max_output_tokens_per_call não altera o cálculo de budget_exceeded,
    que depende só dos tokens efetivamente USADOS (reportados pelo
    provider), não do teto configurado por chamada.

    Comparação é >= (correção pós-Etapa-6): "orçamento inteiramente
    consumido" (uso == limite) já conta como esgotado — por isso o caso
    abaixo usa max_total_tokens=301 (1 tolerância) pra representar
    "dentro do orçamento", não mais uso==limite exato."""
    providers = {
        "openai": StubProvider(
            "openai",
            response=success_response("openai", input_tokens=100, output_tokens=50),
        ),
        "anthropic": StubProvider(
            "anthropic",
            response=success_response("anthropic", input_tokens=100, output_tokens=50),
        ),
    }
    orchestrator = Orchestrator(providers)
    # per-call cap gigante (8000) não tem nada a ver com o orçamento
    # agregado (301) — o teste comprova que são eixos independentes.
    run_config = _run_config(
        ["openai", "anthropic"],
        max_output_tokens_per_call=8000,
        max_total_tokens=301,
    )

    result = await orchestrator.run(run_config)

    # uso real = 100+50+100+50 = 300, abaixo do limite (301)
    assert result.total_input_tokens + result.total_output_tokens == 300
    assert result.budget_exceeded is False

    # uso == limite exato (300/300) já conta como esgotado sob >=
    providers_exact = {
        "openai": StubProvider(
            "openai",
            response=success_response("openai", input_tokens=100, output_tokens=50),
        ),
        "anthropic": StubProvider(
            "anthropic",
            response=success_response("anthropic", input_tokens=100, output_tokens=50),
        ),
    }
    run_config_exact = _run_config(
        ["openai", "anthropic"],
        max_output_tokens_per_call=8000,
        max_total_tokens=300,
    )
    result_exact = await Orchestrator(providers_exact).run(run_config_exact)
    assert result_exact.budget_exceeded is True

    # baixando o orçamento agregado pra menos que o uso real dispara o
    # budget, mesmo com o teto por chamada permanecendo gigante
    run_config_estourado = _run_config(
        ["openai", "anthropic"],
        max_output_tokens_per_call=8000,
        max_total_tokens=299,
    )
    providers_2 = {
        "openai": StubProvider(
            "openai",
            response=success_response("openai", input_tokens=100, output_tokens=50),
        ),
        "anthropic": StubProvider(
            "anthropic",
            response=success_response("anthropic", input_tokens=100, output_tokens=50),
        ),
    }
    result_2 = await Orchestrator(providers_2).run(run_config_estourado)
    assert result_2.budget_exceeded is True
