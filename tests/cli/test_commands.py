from __future__ import annotations

import json

import pytest

from app.cli import commands
from app.config import Settings
from app.models.request_provenance import REQUEST_DIGEST_PREFIX, RequestProvenance
from tests.api.helpers import SelfMutatingRegistryProvider, _FakeRegistryProvider, build_test_components
from tests.storage.fixtures import (
    full_council_run_result,
    now,
    quorum_failure_exception,
    run_config,
    very_rich_council_run_result,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


async def _components(**kwargs):
    return await build_test_components(_settings(), **kwargs)


# ---------------------------------------------------------------------------
# run — sucesso
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_run_success_returns_exit_0(capsys):
    result = full_council_run_result()
    components = await _components(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )

    exit_code = await commands.cmd_run(
        components, question="Qual a capital do Brasil?", providers=None, source_text=None, as_json=False
    )

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    assert result.final_answer.answer_text in out.out
    assert out.err == ""


@pytest.mark.asyncio
async def test_cmd_run_json_reports_accepted_time_default_model_not_live_registry(capsys):
    """F1 (repair pós-revisão independente, MEDIUM) -- mesma garantia de
    `test_create_run_immediate_response_reports_accepted_time_default_model_not_live_registry`
    (tests/api/test_create_run.py), agora pro caminho síncrono da CLI
    (`dialeon run --json`)."""
    result = full_council_run_result()
    openai_provider = SelfMutatingRegistryProvider("model-A", "model-B")
    components = await _components(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
        provider_instances={
            "openai": openai_provider,
            "anthropic": _FakeRegistryProvider("claude-fixed"),
        },
    )

    exit_code = await commands.cmd_run(
        components, question="Qual a capital do Brasil?", providers=["openai"], source_text=None,
        as_json=True,
    )

    assert exit_code == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["default_model_authority_snapshot"]["configured_default_models"]["openai"] == (
        "model-A"
    )
    assert openai_provider.read_count == 1

    # cmd_get subsequente também precisa reportar o valor ACEITO.
    exit_code_get = await commands.cmd_get(components, run_id=body["id"], as_json=True)
    assert exit_code_get == commands.EXIT_OK
    get_body = json.loads(capsys.readouterr().out)
    assert get_body["default_model_authority_snapshot"]["configured_default_models"][
        "openai"
    ] == "model-A"
    assert openai_provider.read_count == 1


@pytest.mark.asyncio
async def test_cmd_run_provider_selection_is_forwarded(capsys):
    result = full_council_run_result()
    components = await _components(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
        provider_names=("openai", "anthropic", "gemini"),
    )

    await commands.cmd_run(
        components, question="pergunta", providers=["openai"], source_text=None, as_json=False
    )

    debate_engine = components.service._runner._debate_engine
    used_config = debate_engine.calls[0]
    assert used_config.enabled_providers == ("openai",)


@pytest.mark.asyncio
async def test_cmd_run_no_providers_flag_uses_all_available(capsys):
    result = full_council_run_result()
    components = await _components(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
        provider_names=("openai", "anthropic", "gemini"),
    )

    await commands.cmd_run(components, question="pergunta", providers=None, source_text=None, as_json=False)

    debate_engine = components.service._runner._debate_engine
    used_config = debate_engine.calls[0]
    assert sorted(used_config.enabled_providers) == ["anthropic", "gemini", "openai"]


# ---------------------------------------------------------------------------
# run — provider inválido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_run_invalid_provider_returns_exit_2(capsys):
    components = await _components(provider_names=("openai", "anthropic"))

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai", "provider-fake"], source_text=None, as_json=False
    )

    assert exit_code == commands.EXIT_INVALID_INPUT
    out = capsys.readouterr()
    assert "provider-fake" not in out.out  # nada de erro no stdout
    assert out.out == ""


@pytest.mark.asyncio
async def test_cmd_run_invalid_provider_message_on_stderr(capsys):
    components = await _components(provider_names=("openai", "anthropic"))

    await commands.cmd_run(
        components, question="pergunta", providers=["provider-fake"], source_text=None, as_json=False
    )

    out = capsys.readouterr()
    assert out.err != ""


@pytest.mark.asyncio
async def test_cmd_run_invalid_provider_never_calls_debate_engine(capsys):
    components = await _components(provider_names=("openai", "anthropic"))

    await commands.cmd_run(
        components, question="pergunta", providers=["provider-fake"], source_text=None, as_json=False
    )

    debate_engine = components.service._runner._debate_engine
    assert debate_engine.calls == []


@pytest.mark.asyncio
async def test_cmd_run_invalid_provider_json_has_stable_error_shape(capsys):
    components = await _components(provider_names=("openai", "anthropic"))

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["provider-fake"], source_text=None, as_json=True
    )

    assert exit_code == commands.EXIT_INVALID_INPUT
    out = capsys.readouterr()
    body = json.loads(out.err)
    assert body["error"]["code"] == "invalid_provider"
    assert "provider-fake" in body["error"]["details"]["unknown_providers"]


# ---------------------------------------------------------------------------
# run — Accepted Quorum Feasibility Boundary V1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_run_infeasible_quorum_returns_exit_2(capsys):
    """Matriz E, itens 25/26 -- `quorum.min_to_return > len(providers
    selecionados)` mapeia pro mesmo vocabulário de invalid-input já
    usado por `InvalidQuestionError`/`UnknownProviderError`:
    `EXIT_INVALID_INPUT` (2), NUNCA `EXIT_INSUFFICIENT_QUORUM` (3) --
    esse é reservado pra uma execução FACTÍVEL despachada de verdade.
    `provider_names=("openai", "anthropic")` cobre os papéis internos
    default (judge/editor/claim processor/source analyzer, todos
    "anthropic" por Settings) -- só `enabled_providers=["openai"]" (1
    participante) fica abaixo de `min_to_return=2`."""
    components = await build_test_components(
        _settings(quorum_min_to_return=2), provider_names=("openai", "anthropic")
    )

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai"], source_text=None, as_json=False
    )

    assert exit_code == commands.EXIT_INVALID_INPUT
    debate_engine = components.service._runner._debate_engine
    assert debate_engine.calls == []


@pytest.mark.asyncio
async def test_cmd_run_infeasible_quorum_json_has_invalid_request_vocabulary(capsys):
    components = await build_test_components(
        _settings(quorum_min_to_return=2), provider_names=("openai", "anthropic")
    )

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai"], source_text=None, as_json=True
    )

    assert exit_code == commands.EXIT_INVALID_INPUT
    out = capsys.readouterr()
    body = json.loads(out.err)
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["details"]["min_to_return"] == 2
    assert body["error"]["details"]["participant_count"] == 1


# ---------------------------------------------------------------------------
# run — insufficient quorum
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_run_insufficient_quorum_returns_exit_3(capsys):
    exc = quorum_failure_exception()
    components = await _components(quorum_exc=exc)

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai", "anthropic"], source_text=None, as_json=False
    )

    assert exit_code == commands.EXIT_INSUFFICIENT_QUORUM
    out = capsys.readouterr()
    assert "quórum" in out.err.lower() or "quorum" in out.err.lower()


@pytest.mark.asyncio
async def test_cmd_run_insufficient_quorum_json_preserves_details(capsys):
    exc = quorum_failure_exception()
    components = await _components(quorum_exc=exc)

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai", "anthropic"], source_text=None, as_json=True
    )

    assert exit_code == commands.EXIT_INSUFFICIENT_QUORUM
    out = capsys.readouterr()
    body = json.loads(out.err)
    assert body["error"]["code"] == "insufficient_quorum"
    assert body["error"]["details"]["successful_count"] == exc.successful_count
    assert body["error"]["details"]["total_providers"] == exc.total_providers
    assert body["error"]["details"]["run_id"] is not None  # persisted_failure_id preservado


# ---------------------------------------------------------------------------
# run — --json válido / null preservado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_run_json_is_valid_and_matches_schema_fields(capsys):
    result = full_council_run_result()
    components = await _components(
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai", "anthropic"], source_text=None, as_json=True
    )

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["status"] == "completed"
    assert body["final_answer"]["answer_text"] == result.final_answer.answer_text
    assert "id" in body


@pytest.mark.asyncio
async def test_cmd_run_json_preserves_null_editor_model_and_confidence(capsys):
    """final_answer.status='deterministic_no_verdict' -> editor_model e
    judge_confidence são None -- --json precisa preservar null, nunca
    reescrever como 0/false/string."""
    from tests.council.fixtures import debate_result, editor_result, model_response, raw_claim
    from tests.council.fixtures import judge_result as jr_fixture

    mr = model_response("openai")
    dr = debate_result([raw_claim("A", mr.id, provider="openai")], [mr])
    er = editor_result()  # default: status="deterministic_no_verdict", editor_model=None
    components = await _components(
        debate_result=dr,
        judge_result=jr_fixture(None, verdict_unavailable_reason="no_claims_to_judge"),
        editor_result=er,
    )

    exit_code = await commands.cmd_run(
        components, question="pergunta", providers=["openai", "anthropic"], source_text=None, as_json=True
    )

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["final_answer"]["editor_model"] is None
    assert body["final_answer"]["judge_confidence"] is None


# ---------------------------------------------------------------------------
# list / get / audit / providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_list_shows_persisted_runs(capsys):
    components = await _components()
    result = full_council_run_result()
    await components.repository.save_success(result)

    exit_code = await commands.cmd_list(components, limit=50, offset=0, as_json=False)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    assert result.id in out.out


@pytest.mark.asyncio
async def test_cmd_list_shows_running_and_failed_labels(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-list-running",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )

    exit_code = await commands.cmd_list(components, limit=50, offset=0, as_json=False)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr().out
    assert "run-cli-list-running" in out
    assert "sem desfecho registrado" in out
    assert "em andamento" not in out


@pytest.mark.asyncio
async def test_cmd_list_json_has_stable_shape(capsys):
    components = await _components()
    result = full_council_run_result()
    await components.repository.save_success(result)

    exit_code = await commands.cmd_list(components, limit=50, offset=0, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["runs"][0]["id"] == result.id
    # History Investigation-Identity V1 -- aditivo ao contrato JSON do
    # CLI (mesmo RunSummaryResponse do HTTP), pergunta canônica exata.
    assert body["runs"][0]["question"] == result.run_config.question


@pytest.mark.asyncio
async def test_cmd_get_json_historical_infinite_cost_and_timeout_render_as_positive_infinity_token(
    capsys,
):
    """Historical Non-Finite Execution-Limit Public Representation V1,
    T17 -- `dialeon get --json` pra um run histórico com
    `max_cost_usd`/`round_dispatch_timeout_seconds` = +inf expõe o
    token exato `"positive_infinity"` -- NUNCA `null`, NUNCA
    `Infinity` (token JSON não-standard que `json.dumps` normal
    emitiria pra um float bruto), NUNCA a string `"Infinity"`."""
    components = await _components()
    result = full_council_run_result(
        run_config=run_config(
            max_cost_usd=float("inf"), round_dispatch_timeout_seconds=float("inf")
        )
    )
    await components.repository.save_success(result)

    exit_code = await commands.cmd_get(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    raw = out.out
    assert "Infinity" not in raw  # nem token JSON não-standard, nem string "Infinity"
    body = json.loads(raw)
    assert body["config"]["max_cost_usd"] == "positive_infinity"
    assert body["config"]["round_dispatch_timeout_seconds"] == "positive_infinity"
    assert body["config"]["max_cost_usd"] is not None
    assert body["config"]["round_dispatch_timeout_seconds"] is not None


@pytest.mark.asyncio
async def test_cmd_get_completed_run(capsys):
    components = await _components()
    result = full_council_run_result()
    await components.repository.save_success(result)

    exit_code = await commands.cmd_get(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["status"] == "completed"
    assert body["id"] == result.id
    # T02.2 -- sem accepted_runs prévio, honestamente None.
    assert body["provider_execution_policy"] is None


@pytest.mark.asyncio
async def test_cmd_get_completed_run_with_known_policy_json_and_human(capsys):
    """T02.2, teste P -- fluxo completo aceite->sucesso expõe a
    política conhecida tanto em --json quanto no texto humano."""
    components = await _components()
    result = full_council_run_result()
    await components.repository.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=components.provider_execution_policy,
    )
    await components.repository.save_success(result)

    exit_code = await commands.cmd_get(components, run_id=result.id, as_json=True)
    assert exit_code == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["provider_execution_policy"] == {
        "attempt_timeout_seconds": 30.0,
        "max_transport_attempts_per_completion": 2,
        "judge_override": None,  # política de teste sem override do Judge
    }

    exit_code = await commands.cmd_get(components, run_id=result.id, as_json=False)
    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr().out
    assert "30.0" in out
    assert "2" in out


@pytest.mark.asyncio
async def test_cmd_get_completed_run_with_judge_override_distinguishes_it_in_json_and_human(capsys):
    """Judge Transport Execution Policy V1 -- o snapshot aceito com override
    do Judge aparece distinguível do default, em --json e no texto humano
    (a linha nunca diz que o default vale pro run inteiro)."""
    from app.models.provider_models import ProviderExecutionPolicy, TransportAttemptPolicy

    components = await _components()
    result = full_council_run_result()
    policy = ProviderExecutionPolicy(
        attempt_timeout_seconds=60.0,
        max_transport_attempts_per_completion=3,
        judge_override=TransportAttemptPolicy(
            attempt_timeout_seconds=120.0, max_transport_attempts_per_completion=1
        ),
    )
    await components.repository.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=policy,
    )
    await components.repository.save_success(result)

    assert await commands.cmd_get(components, run_id=result.id, as_json=True) == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["provider_execution_policy"]["judge_override"] == {
        "attempt_timeout_seconds": 120.0,
        "max_transport_attempts_per_completion": 1,
    }

    assert await commands.cmd_get(components, run_id=result.id, as_json=False) == commands.EXIT_OK
    line = next(
        l for l in capsys.readouterr().out.split("\n") if "política_de_execução_do_provider" in l
    )
    assert "timeout_por_tentativa=60.0s" in line
    assert "tentativas_de_transporte_max=3" in line
    assert "padrão, exceto Judge" in line
    assert "juiz: timeout_por_tentativa=120.0s" in line
    assert "tentativas_de_transporte_max=1" in line


@pytest.mark.asyncio
async def test_cmd_get_completed_run_without_policy_renders_unknown_human(capsys):
    """T02.2 -- historical null precisa renderizar honestamente como
    "desconhecida", nunca inventar 60s/3 tentativas (defaults atuais)."""
    components = await _components()
    result = full_council_run_result()
    await components.repository.save_success(result)  # sem accepted_runs -> policy=None

    exit_code = await commands.cmd_get(components, run_id=result.id, as_json=False)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr().out
    assert "desconhecida" in out
    assert "60" not in out.split("política_de_execução_do_provider")[-1].split("\n")[0]


@pytest.mark.asyncio
async def test_cmd_list_json_keeps_machine_readable_running_status_and_null_ended_at(capsys):
    """A mudança de rótulo é SÓ do texto humano: o JSON continua com
    `status="running"` e `ended_at=null` (semântica de máquina inalterada)."""
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-list-json-running",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )

    exit_code = await commands.cmd_list(components, limit=50, offset=0, as_json=True)

    assert exit_code == commands.EXIT_OK
    (run,) = json.loads(capsys.readouterr().out)["runs"]
    assert run["status"] == "running"
    assert run["ended_at"] is None


@pytest.mark.asyncio
async def test_cmd_get_running_run_json(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-running",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )

    exit_code = await commands.cmd_get(components, run_id="run-cli-running", as_json=True)

    assert exit_code == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "running"
    assert body["id"] == "run-cli-running"


@pytest.mark.asyncio
async def test_cmd_get_running_run_human_output(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-running-2",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )

    exit_code = await commands.cmd_get(components, run_id="run-cli-running-2", as_json=False)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr().out
    assert "status: sem desfecho registrado" in out
    assert "em andamento" not in out.split("--")[0]  # nunca no rótulo de status
    assert "run-cli-running-2" in out
    assert "concluída" not in out and "falhou" not in out  # nenhum desfecho fabricado


@pytest.mark.asyncio
async def test_cmd_get_failed_run_json_sanitized(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-failed",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )
    await components.repository.save_unexpected_failure(
        "run-cli-failed",
        failed_at=now(),
        failure_classification="WeirdBug",
        failure_message="Erro interno inesperado durante a execução.",
    )

    exit_code = await commands.cmd_get(components, run_id="run-cli-failed", as_json=True)

    assert exit_code == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "failed"
    assert body["failure_reason"] == "WeirdBug"
    assert "Traceback" not in json.dumps(body)


@pytest.mark.asyncio
async def test_cmd_get_not_found_returns_exit_4(capsys):
    components = await _components()

    exit_code = await commands.cmd_get(components, run_id="run-inexistente", as_json=False)

    assert exit_code == commands.EXIT_NOT_FOUND
    out = capsys.readouterr()
    assert out.out == ""
    assert "run-inexistente" in out.err


@pytest.mark.asyncio
async def test_cmd_get_not_found_json_error_shape(capsys):
    components = await _components()

    exit_code = await commands.cmd_get(components, run_id="run-inexistente", as_json=True)

    assert exit_code == commands.EXIT_NOT_FOUND
    out = capsys.readouterr()
    body = json.loads(out.err)
    assert body["error"]["code"] == "run_not_found"


@pytest.mark.asyncio
async def test_cmd_audit_completed_run(capsys):
    components = await _components()
    result = full_council_run_result()
    await components.repository.save_success(result)

    exit_code = await commands.cmd_audit(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["status"] == "completed"
    assert len(body["claims"]) == len(result.debate_result.claims)


@pytest.mark.asyncio
async def test_cmd_audit_json_exposes_default_model_authority_snapshot_exact_value(capsys):
    """C21 -- `dialeon audit --json` deriva do MESMO schema público/de
    audit da API (app.presentation), então o snapshot de autoridade de
    modelo padrão aparece com o valor EXATO persistido, nunca
    regenerado do registry de provider atual."""
    from app.models.provider_models import DefaultModelAuthoritySnapshot

    components = await _components()
    result = full_council_run_result()
    snapshot = DefaultModelAuthoritySnapshot(
        configured_default_models={"openai": "gpt-cli-audit-value"}
    )
    await components.repository.save_accepted(
        result.id,
        run_config=result.run_config,
        started_at=result.started_at,
        provider_execution_policy=components.provider_execution_policy,
        default_model_authority_snapshot=snapshot,
    )
    await components.repository.save_success(result)

    exit_code = await commands.cmd_audit(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["default_model_authority_snapshot"] == {
        "configured_default_models": {"openai": "gpt-cli-audit-value"}
    }


@pytest.mark.asyncio
async def test_cmd_audit_json_historical_infinite_cost_and_timeout_render_as_positive_infinity_token(
    capsys,
):
    """T18 -- `dialeon audit --json` pra o MESMO cenário histórico
    acima -- mesma garantia (token exato, nunca null/Infinity)."""
    components = await _components()
    result = full_council_run_result(
        run_config=run_config(
            max_cost_usd=float("inf"), round_dispatch_timeout_seconds=float("inf")
        )
    )
    await components.repository.save_success(result)

    exit_code = await commands.cmd_audit(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    raw = out.out
    assert "Infinity" not in raw
    body = json.loads(raw)
    assert body["config"]["max_cost_usd"] == "positive_infinity"
    assert body["config"]["round_dispatch_timeout_seconds"] == "positive_infinity"


@pytest.mark.asyncio
async def test_cmd_audit_json_exposes_request_provenance_same_schema_as_api(capsys):
    """`dialeon audit --json` deriva do MESMO schema público/de audit da
    API (app.presentation) -- nenhuma segunda autoridade de serialização
    -- então request_provenance persistida aparece idêntica: valor
    concreto pra uma resposta nova, null pra uma histórica."""
    components = await _components()
    result = full_council_run_result()
    concrete = RequestProvenance(
        contract_version="initial_response_v1",
        request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
    )
    mr1 = result.debate_result.initial_result.responses[0].model_copy(
        update={"request_provenance": concrete}
    )
    mr2 = result.debate_result.initial_result.responses[1].model_copy(
        update={"request_provenance": None}
    )
    initial = result.debate_result.initial_result.model_copy(update={"responses": [mr1, mr2]})
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})
    await components.repository.save_success(result)

    exit_code = await commands.cmd_audit(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    responses_by_id = {r["id"]: r for r in body["initial_round"]["responses"]}
    assert responses_by_id[mr1.id]["request_provenance"] == {
        "contract_version": "initial_response_v1",
        "request_digest": REQUEST_DIGEST_PREFIX + "a" * 64,
    }
    assert responses_by_id[mr2.id]["request_provenance"] is None


@pytest.mark.asyncio
async def test_cmd_audit_human_output_surfaces_source_analysis(capsys):
    """Patch de visibilidade humana (pós-Etapa-16): `dialeon audit`
    (texto humano, sem --json) precisa mostrar a análise de fonte já
    computada -- `very_rich_council_run_result()` já traz os 6 cenários
    reais (supports/contradicts/unresolved + 3 rejeições) usados no
    round-trip de fidelidade total da Etapa 16."""
    components = await _components()
    result = very_rich_council_run_result()
    await components.repository.save_success(result)

    exit_code = await commands.cmd_audit(components, run_id=result.id, as_json=False)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr().out

    assert "análise_de_fonte: concluída" in out
    assert "relações: 3" in out
    assert "entradas rejeitadas: 3" in out
    assert "segundo a análise, a fonte apoia esta claim" in out
    assert "a receita cresceu 12% em 2025" in out  # excerpt verbatim
    assert "segundo a análise, a fonte contradiz esta claim" in out
    assert "Não há menção a lucro líquido neste trecho." in out  # excerpt verbatim
    assert "a análise não conseguiu determinar a relação com a fonte" in out
    assert "a análise não endereçou esta claim" in out  # omitted_by_model
    assert "a análise devolveu mais de uma entrada" in out  # duplicate_claim_id
    assert "a entrada da análise não pôde ser validada" in out  # invalid_entry
    # nunca confunde rejeitada com unresolved -- rótulos permanecem distintos
    assert out.count("a análise não conseguiu determinar a relação com a fonte") == 1


@pytest.mark.asyncio
async def test_cmd_audit_exposes_deterministic_verification_attempts(capsys):
    """Etapa 15: dialeon audit --json expõe a MESMA semântica que a API
    -- via a mesma camada app.presentation compartilhada, sem comando
    novo."""
    from app.debate.numeric_verification import build_verification_attempt

    components = await _components()
    result = full_council_run_result()
    claim = result.debate_result.claims[0]
    attempt = build_verification_attempt(
        claim.id, {"left": "2", "operator": "+", "right": "2", "asserted_result": "5"}
    )
    debate = result.debate_result.model_copy(update={"numeric_verification_attempts": [attempt]})
    result = result.model_copy(update={"debate_result": debate})
    await components.repository.save_success(result)

    exit_code = await commands.cmd_audit(components, run_id=result.id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    reloaded = body["numeric_verification_attempts"][0]
    assert reloaded["claim_id"] == claim.id
    assert reloaded["state"] == "contradicts"
    assert reloaded["computed_result"] == "4"


@pytest.mark.asyncio
async def test_cmd_audit_quorum_failure_run(capsys):
    components = await _components()
    exc = quorum_failure_exception()
    rc = run_config()
    failure_id = await components.repository.save_quorum_failure(
        exc, run_config=rc, started_at=now(), failed_at=now()
    )

    exit_code = await commands.cmd_audit(components, run_id=failure_id, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body["status"] == "insufficient_quorum"


@pytest.mark.asyncio
async def test_cmd_audit_running_run_never_invents_detail(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-audit-running",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )

    exit_code = await commands.cmd_audit(components, run_id="run-cli-audit-running", as_json=True)

    assert exit_code == commands.EXIT_OK
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "running"
    assert set(body.keys()) == {
        "status", "id", "started_at", "config", "provider_execution_policy",
        "default_model_authority_snapshot",
    }


@pytest.mark.asyncio
async def test_cmd_audit_running_run_human_output(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "run-cli-audit-running-2",
        run_config=run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )

    exit_code = await commands.cmd_audit(
        components, run_id="run-cli-audit-running-2", as_json=False
    )

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr().out
    assert "status: sem desfecho registrado" in out
    assert "status: em andamento" not in out
    assert "juiz" not in out.lower() and "claims:" not in out  # nenhum detalhe de auditoria fabricado


@pytest.mark.asyncio
async def test_cmd_audit_not_found_returns_exit_4(capsys):
    components = await _components()

    exit_code = await commands.cmd_audit(components, run_id="run-inexistente", as_json=False)

    assert exit_code == commands.EXIT_NOT_FOUND


@pytest.mark.asyncio
async def test_cmd_providers_lists_available_providers(capsys):
    components = await _components(provider_names=("openai", "anthropic", "gemini"))

    exit_code = await commands.cmd_providers(components, as_json=False)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    assert "openai" in out.out
    assert "anthropic" in out.out
    assert "gemini" in out.out


@pytest.mark.asyncio
async def test_cmd_providers_json(capsys):
    components = await _components(provider_names=("openai", "anthropic"))

    exit_code = await commands.cmd_providers(components, as_json=True)

    assert exit_code == commands.EXIT_OK
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert sorted(body["providers"]) == ["anthropic", "openai"]


# ---------------------------------------------------------------------------
# Segurança — nenhum secret aparece em nenhuma saída
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_secrets_leak_in_run_output(capsys):
    result = full_council_run_result()
    settings = Settings(_env_file=None, openai_api_key="sk-super-secreta-nao-deveria-aparecer")
    components = await build_test_components(
        settings,
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )

    await commands.cmd_run(components, question="pergunta", providers=None, source_text=None, as_json=True)
    out = capsys.readouterr()
    assert "sk-super-secreta-nao-deveria-aparecer" not in out.out
    assert "sk-super-secreta-nao-deveria-aparecer" not in out.err


@pytest.mark.asyncio
async def test_no_secrets_leak_in_providers_output(capsys):
    settings = Settings(_env_file=None, anthropic_api_key="sk-outra-secreta")
    components = await build_test_components(settings, provider_names=("openai", "anthropic"))

    await commands.cmd_providers(components, as_json=True)
    out = capsys.readouterr()
    assert "sk-outra-secreta" not in out.out
