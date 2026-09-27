"""Direct Accepted Effective Model Choice V1 -- CLI (`dialeon run --direct
--model PROVIDER=MODELO`): a mesma sintaxe do Conselho, no máximo uma vez e
nomeando o provider escolhido."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.cli import commands
from app.cli.main import _build_parser, main
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from app.providers.errors import ProviderAPIError
from app.providers.pricing import ModelRate, PricingRegistry
from tests.api.helpers import build_test_components
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import full_council_run_result

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
_CREATED = []


@pytest_asyncio.fixture(autouse=True)
async def _dispose():
    yield
    while _CREATED:
        await _CREATED.pop().engine.dispose()


async def _components(openai_script=None, pricing=None):
    result = full_council_run_result()
    components = await build_test_components(
        Settings(_env_file=None),
        provider_instances={
            "openai": ScriptedApiProvider("openai", openai_script or [ok()], default_model="gpt-conf", pricing=pricing),
            "gemini": ScriptedApiProvider("gemini", [ok()], default_model="gemini-conf"),
            "anthropic": ScriptedApiProvider("anthropic", [ok()], default_model="claude-conf"),
        },
        provider_execution_policy=POLICY,
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    _CREATED.append(components)
    return components


async def _run(components, models, *, providers=("openai",), as_json=True):
    return await commands.cmd_run_direct(
        components,
        question="Qual a capital?",
        providers=list(providers) if providers is not None else None,
        source_text=None,
        as_json=as_json,
        model_overrides=models,
    )


def _all_requests(components):
    return [r for p in components.providers.values() for r in p.requests]


def test_the_parser_accepts_model_with_direct():
    args = _build_parser().parse_args(["run", "q", "--direct", "--providers", "openai", "--model", "openai=gpt-x"])
    assert (args.direct, args.models) == (True, ["openai=gpt-x"])


def test_help_explains_the_direct_model_option(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    assert "Com --direct: no máximo um --model" in " ".join(capsys.readouterr().out.split())


@pytest.mark.asyncio
async def test_direct_model_is_frozen_as_a_run_override(capsys):
    components = await _components()

    code = await _run(components, ["openai=gpt-explicit"])
    body = json.loads(capsys.readouterr().out)

    assert code == commands.EXIT_OK
    assert body["kind"] == "direct"
    assert (body["config"]["requested_model"], body["config"]["requested_model_origin"]) == (
        "gpt-explicit",
        "run_override",
    )
    assert [r.model for r in _all_requests(components)] == ["gpt-explicit"]


@pytest.mark.asyncio
async def test_without_model_the_configured_default_is_used(capsys):
    components = await _components()

    await _run(components, None)
    body = json.loads(capsys.readouterr().out)

    assert (body["config"]["requested_model"], body["config"]["requested_model_origin"]) == (
        "gpt-conf",
        "configured_default",
    )


@pytest.mark.asyncio
async def test_human_output_names_the_origin_of_the_requested_model(capsys):
    components = await _components()

    await _run(components, ["openai=gpt-explicit"], as_json=False)
    chosen = capsys.readouterr().out.split("\n")
    await _run(components, None, as_json=False)
    default = capsys.readouterr().out.split("\n")

    assert "modelo solicitado: gpt-explicit (escolhido nesta pergunta)" in chosen
    assert "modelo solicitado: gpt-conf (padrão configurado)" in default


@pytest.mark.asyncio
async def test_get_and_audit_show_the_frozen_origin(capsys):
    components = await _components()
    await _run(components, ["openai=gpt-explicit"])
    run_id = json.loads(capsys.readouterr().out)["id"]

    await commands.cmd_get(components, run_id=run_id, as_json=False)
    get_out = capsys.readouterr().out.split("\n")
    await commands.cmd_audit(components, run_id=run_id, as_json=False)
    audit_out = capsys.readouterr().out.split("\n")

    assert "modelo solicitado: gpt-explicit (escolhido nesta pergunta)" in get_out
    assert "modelo solicitado (aceite): gpt-explicit (escolhido nesta pergunta)" in audit_out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "models, error_type",
    [
        (["openai"], "model_option_syntax"),
        (["=gpt-x"], "model_option_syntax"),
        (["openai=a", "openai=b"], "model_option_syntax"),
        (["openai=a", "gemini=b"], "direct_model_option_count"),
        (["gemini=gemini-x"], "direct_model_provider_mismatch"),  # não é o provider escolhido
        (["openai="], "value_error"),
        (["openai=gpt x"], "value_error"),
        (["openai=gpt‮-x"], "value_error"),
        (["openai=gpt͏x"], "value_error"),
        (["openai=" + "g" * 257], "value_error"),
    ],
)
async def test_invalid_direct_model_options_exit_2_without_any_run_or_call(capsys, models, error_type):
    components = await _components()

    code = await _run(components, models)
    error = json.loads(capsys.readouterr().err)["error"]

    assert code == commands.EXIT_INVALID_INPUT
    assert error["code"] == "invalid_request"
    [detail] = error["details"]["errors"]
    assert detail["type"] == error_type
    assert detail["loc"][-1] == "requested_model"
    assert await components.repository.list_runs() == []
    assert _all_requests(components) == []


@pytest.mark.asyncio
async def test_a_mismatch_is_explained_in_human_output(capsys):
    components = await _components()

    code = await _run(components, ["gemini=gemini-x"], as_json=False)

    assert code == commands.EXIT_INVALID_INPUT
    assert "--model precisa nomear o provider escolhido em --providers" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_remote_rejection_exits_5_with_the_chosen_model_and_no_substitution(capsys):
    components = await _components([ProviderAPIError("openai: status=404: model not found", retryable=False)])

    code = await _run(components, ["openai=gpt-nope"])
    body = json.loads(capsys.readouterr().out)

    assert code == commands.EXIT_PROVIDER_FAILED
    assert body["status"] == "failed" and body["failure_reason"] == "api_error"
    assert body["config"]["requested_model"] == "gpt-nope"
    assert [r.model for r in _all_requests(components)] == ["gpt-nope"]


@pytest.mark.asyncio
async def test_an_unpriced_chosen_model_is_never_shown_as_zero_cost(capsys):
    pricing = PricingRegistry(
        {("openai", "gpt-conf"): ModelRate(input_usd_per_million_tokens=1, output_usd_per_million_tokens=1)}
    )
    components = await _components(pricing=pricing)

    await _run(components, ["openai=gpt-sem-preco"], as_json=False)
    out = capsys.readouterr().out

    cost_lines = [line for line in out.split("\n") if "custo" in line]
    assert cost_lines and all("desconhecido" in line for line in cost_lines)
    assert "0.00" not in out and "0,00" not in out


@pytest.mark.asyncio
async def test_readiness_is_unchanged_and_still_council_only(capsys):
    components = await _components()

    code = await commands.cmd_readiness(
        components, providers=["openai"], source_text=None, as_json=True, model_overrides=["openai=gpt-x"]
    )
    body = json.loads(capsys.readouterr().out)

    assert code == commands.EXIT_OK
    assert body["contract_version"] == "council_local_readiness_v2"
    assert await components.repository.list_runs() == []
