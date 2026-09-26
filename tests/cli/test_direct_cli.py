"""Direct Answer Execution V1 -- CLI (`dialeon run --direct`)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.cli import commands, output
from app.cli.main import _build_parser
from app.config import Settings
from app.models.provider_models import ProviderExecutionPolicy
from app.providers.errors import ProviderTimeoutError
from app.text_safety import terminal_safe_text
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


async def _components(**providers):
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


async def _run(components, *, providers=("openai",), source_text=None, as_json=False, question="Qual a capital?"):
    return await commands.cmd_run_direct(
        components,
        question=question,
        providers=list(providers) if providers is not None else None,
        source_text=source_text,
        as_json=as_json,
    )


def test_parser_direct_is_opt_in():
    parser = _build_parser()
    assert parser.parse_args(["run", "q"]).direct is False
    args = parser.parse_args(["run", "q", "--direct", "--providers", "openai", "--json"])
    assert (args.direct, args.providers, args.as_json) == (True, "openai", True)


@pytest.mark.asyncio
async def test_direct_human_output_is_answer_first_and_says_what_it_is(capsys):
    components = await _components(openai=ScriptedApiProvider("openai", [ok("Brasília.")], default_model="gpt-conf"))

    assert await _run(components) == commands.EXIT_OK
    lines = capsys.readouterr().out.rstrip("\n").split("\n")

    assert lines[0] == "resposta direta (openai):"
    assert lines[1] == "Brasília."
    assert "não é consenso, veredito do juiz nem verificação" in lines[3]
    assert lines.index("detalhes:") > 1
    assert "tipo: resposta direta" in lines
    assert "modelo solicitado: gpt-conf" in lines
    assert sum(1 for line in lines if line.startswith("status:")) == 1
    assert not any(line.startswith(("resposta:", "resposta principal", "limitações:", "avaliação completa")) for line in lines)
    assert lines[-1].endswith("--json")


@pytest.mark.asyncio
async def test_direct_json_has_an_explicit_kind(capsys):
    components = await _components(openai=ScriptedApiProvider("openai", [ok("Brasília.")]))

    await _run(components, as_json=True)
    body = json.loads(capsys.readouterr().out)

    assert body["kind"] == "direct" and body["status"] == "completed" and body["answer"] == "Brasília."
    assert "final_answer" not in body


@pytest.mark.asyncio
async def test_get_and_audit_of_a_direct_run(capsys):
    components = await _components(openai=ScriptedApiProvider("openai", [ok("Brasília.")]))
    await _run(components, as_json=True)
    run_json = capsys.readouterr().out
    run_id = json.loads(run_json)["id"]

    await commands.cmd_get(components, run_id=run_id, as_json=True)
    assert capsys.readouterr().out == run_json
    await commands.cmd_audit(components, run_id=run_id, as_json=True)
    assert capsys.readouterr().out == run_json

    await commands.cmd_get(components, run_id=run_id, as_json=False)
    assert capsys.readouterr().out.startswith("resposta direta (openai):\nBrasília.\n")
    await commands.cmd_audit(components, run_id=run_id, as_json=False)
    audit = capsys.readouterr().out.split("\n")
    assert audit[0] == "status: concluída"
    assert "tipo: resposta direta" in audit
    assert "contrato_do_request: direct_answer_v1" in audit
    assert not any("claims" in line or "judge" in line for line in audit)


@pytest.mark.asyncio
async def test_run_and_get_print_the_same_direct_output(capsys):
    components = await _components(openai=ScriptedApiProvider("openai", [ok("Brasília.")]))
    await _run(components)
    run_out = capsys.readouterr().out
    run_id = run_out.split("resumo da auditoria: dialeon audit ", 1)[1].split("\n", 1)[0]

    await commands.cmd_get(components, run_id=run_id, as_json=False)

    assert capsys.readouterr().out == run_out


@pytest.mark.asyncio
async def test_provider_failure_exits_5_with_status_first(capsys):
    openai = ScriptedApiProvider("openai", [ProviderTimeoutError("timeout")])
    anthropic = ScriptedApiProvider("anthropic", [ok()])
    components = await _components(openai=openai, anthropic=anthropic)

    assert await _run(components) == commands.EXIT_PROVIDER_FAILED
    lines = capsys.readouterr().out.split("\n")

    assert lines[0] == "status: falhou"
    assert lines[1] == "nenhuma resposta foi produzida: A chamada ao provider excedeu o tempo limite."
    assert "tentativa_anterior_incerta: não" in lines
    assert anthropic.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "providers, source_text",
    [(None, None), (("openai", "anthropic"), None), ((), None), (("openai",), "uma fonte")],
)
async def test_invalid_direct_invocations_exit_2_without_any_attempt(providers, source_text, capsys):
    openai = ScriptedApiProvider("openai", [ok()])
    anthropic = ScriptedApiProvider("anthropic", [ok()])
    components = await _components(openai=openai, anthropic=anthropic)

    code = await _run(components, providers=providers, source_text=source_text, as_json=True)

    assert code == commands.EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_request"
    assert openai.requests == [] and anthropic.requests == []
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_missing_prerequisite_and_unknown_provider_exit_2(capsys):
    components = await _components(openai=ScriptedApiProvider("openai", [ok()], api_key=None))

    assert await _run(components, as_json=True) == commands.EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "provider_prerequisites_missing"
    assert await _run(components, providers=("x",), as_json=True) == commands.EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_provider"
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_list_marks_only_direct_runs(capsys):
    components = await _components(
        openai=ScriptedApiProvider("openai", [ok()]), anthropic=ScriptedApiProvider("anthropic", [])
    )
    await commands.cmd_run(components, question="q", providers=["openai", "anthropic"], source_text=None, as_json=True)
    await _run(components, as_json=True)
    capsys.readouterr()

    await commands.cmd_list(components, limit=10, offset=0, as_json=False)
    lines = capsys.readouterr().out.rstrip("\n").split("\n")

    assert sum(line.endswith("(resposta direta)") for line in lines) == 1
    council_line = next(line for line in lines if not line.endswith("(resposta direta)"))
    assert council_line.split()[1] == "concluída"


def test_direct_human_output_is_terminal_safe():
    from app.presentation.schemas import DirectCompletedRunResponse

    evil = "resposta\x1b[31m\nstatus: forjado"
    run = DirectCompletedRunResponse.model_validate(
        {
            "id": "r",
            "started_at": "2026-09-01T00:00:00Z",
            "completed_at": "2026-09-01T00:00:01Z",
            "config": {"question": "q", "provider": "p\x1b[2J", "requested_model": "m", "max_output_tokens": 1},
            "answer": evil,
            "response": {
                "id": "x", "provider": "p", "requested_model": "m", "model": "m\x1b[0m",
                "model_identity_source": None, "round_number": 1, "status": "success",
                "response_text": evil, "usage": None, "cost_usd": None, "pricing_provenance": None,
                "latency_ms": 1, "attempts": 1, "error": None, "had_uncertain_prior_attempts": False,
                "provider_finish_reason": None, "request_provenance": None,
                "created_at": "2026-09-01T00:00:00Z",
            },
            "accounting": {"total_input_tokens": 0, "total_output_tokens": 0, "estimated_cost_usd": 0.0,
                           "has_unknown_accounting_components": True},
            "provider_execution_policy": {"attempt_timeout_seconds": 1.0, "max_transport_attempts_per_completion": 1},
        }
    )
    text = output.human_direct_run(run)

    assert "\x1b" not in text
    assert text.split("\n")[1] == terminal_safe_text(evil)
    assert sum(1 for line in text.split("\n") if line.startswith("status:")) == 1


@pytest.mark.asyncio
async def test_unknown_single_call_cost_is_never_shown_as_zero(capsys):
    components = await _components(openai=ScriptedApiProvider("openai", [ok()], default_model="sem-preco"))

    await _run(components)

    assert "custo estimado: desconhecido (contabilidade completa: não)" in capsys.readouterr().out.split("\n")
