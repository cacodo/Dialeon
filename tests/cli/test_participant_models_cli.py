"""Council Accepted Effective Participant Model Choice V1 -- CLI
(`--model PROVIDER=MODELO`, repetível, em `run` e `readiness`)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.cli import commands
from app.cli.main import _build_parser
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from tests.api.helpers import build_test_components
from tests.direct.fakes import ScriptedApiProvider
from tests.storage.fixtures import full_council_run_result, now, run_config

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
_CREATED = []


@pytest_asyncio.fixture(autouse=True)
async def _dispose():
    yield
    while _CREATED:
        await _CREATED.pop().engine.dispose()


async def _components():
    providers = {
        name: ScriptedApiProvider(name, [], default_model=f"{name}-configured")
        for name in ("openai", "anthropic", "gemini")
    }
    result = full_council_run_result()
    components = await build_test_components(
        Settings(_env_file=None),
        provider_instances=providers,
        provider_execution_policy=POLICY,
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )
    _CREATED.append(components)
    return components


async def _run(components, models, *, as_json=True, providers=("openai", "gemini")):
    return await commands.cmd_run(
        components,
        question="Qual a capital?",
        providers=list(providers),
        source_text=None,
        as_json=as_json,
        model_overrides=models,
    )


def test_parser_model_is_repeatable_and_optional():
    parser = _build_parser()
    assert parser.parse_args(["run", "q"]).models is None
    args = parser.parse_args(["run", "q", "--model", "openai=gpt-x", "--model", "gemini=g-y"])
    assert args.models == ["openai=gpt-x", "gemini=g-y"]
    assert parser.parse_args(["readiness", "--model", "openai=gpt-x"]).models == ["openai=gpt-x"]


@pytest.mark.asyncio
async def test_run_freezes_the_override_and_the_defaults(capsys):
    components = await _components()

    code = await _run(components, ["gemini=gemini-explicit"])
    body = json.loads(capsys.readouterr().out)

    assert code == commands.EXIT_OK
    assert body["config"]["participant_models"] == [
        {"provider": "openai", "requested_model": "openai-configured", "origin": "configured_default"},
        {"provider": "gemini", "requested_model": "gemini-explicit", "origin": "run_override"},
    ]


@pytest.mark.asyncio
async def test_run_without_model_keeps_the_default_behaviour(capsys):
    components = await _components()

    await _run(components, None, as_json=False)
    out = capsys.readouterr().out

    assert (
        "modelos_pedidos_aos_participantes: openai=openai-configured (padrão configurado), "
        "gemini=gemini-configured (padrão configurado)"
    ) in out


@pytest.mark.asyncio
async def test_human_run_output_names_the_chosen_model_and_its_origin(capsys):
    components = await _components()

    await _run(components, ["openai=gpt-x"], as_json=False)

    assert "openai=gpt-x (escolhido nesta pergunta)" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "models, error_type",
    [
        (["openai"], "model_option_syntax"),
        (["=gpt-x"], "model_option_syntax"),
        (["openai=a", "openai=b"], "model_option_syntax"),
        (["anthropic=claude-x"], "value_error"),  # não é participante selecionado
        (["openai=gpt x"], "value_error"),
        (["openai="], "value_error"),
    ],
)
async def test_invalid_model_options_fail_before_any_run_or_call(capsys, models, error_type):
    components = await _components()

    code = await _run(components, models)
    error = json.loads(capsys.readouterr().err)["error"]

    assert code == commands.EXIT_INVALID_INPUT
    assert error["code"] == "invalid_request"
    assert error["details"]["errors"][0]["type"] == error_type
    assert await components.repository.list_runs() == []
    assert all(p.requests == [] for p in components.providers.values())


@pytest.mark.asyncio
async def test_readiness_shows_the_planned_model_without_claiming_availability(capsys):
    components = await _components()

    code = await commands.cmd_readiness(
        components, providers=["openai", "gemini"], source_text=None, as_json=False, model_overrides=["openai=gpt-x"]
    )
    out = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert (
        "  - participante: openai (modelo pedido: gpt-x, escolhido nesta pergunta; padrão configurado: "
        "openai-configured) -- configuração local presente"
    ) in out
    assert "  - participante: gemini (modelo pedido: gemini-configured, o padrão configurado)" in out
    assert "não testa credenciais, serviços nem modelos" in out
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_readiness_json_carries_planned_models(capsys):
    components = await _components()

    await commands.cmd_readiness(
        components, providers=["openai"], source_text=None, as_json=True, model_overrides=["openai=gpt-x"]
    )
    body = json.loads(capsys.readouterr().out)

    [participant] = [d for d in body["dependencies"] if d["role"] == "participant"]
    assert (participant["planned_model"], participant["planned_model_origin"]) == ("gpt-x", "run_override")


@pytest.mark.asyncio
async def test_direct_rejects_the_model_option(capsys):
    components = await _components()

    code = await commands.cmd_run_direct(
        components, question="q", providers=["openai"], source_text=None, as_json=True, model_overrides=["openai=x"]
    )
    error = json.loads(capsys.readouterr().err)["error"]

    assert code == commands.EXIT_INVALID_INPUT
    assert error["details"]["errors"][0]["type"] == "direct_participant_model_not_supported"
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_a_legacy_run_shows_participant_models_as_not_recorded(capsys):
    components = await _components()
    await components.repository.save_accepted(
        "legacy-run", run_config=run_config(), started_at=now(), provider_execution_policy=POLICY
    )

    await commands.cmd_get(components, run_id="legacy-run", as_json=False)

    assert (
        "modelos_pedidos_aos_participantes: não registrado (execução anterior a este registro)"
        in capsys.readouterr().out
    )
