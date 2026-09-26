"""Council Local Execution Readiness & Admission V1 -- CLI (`dialeon
readiness`, `dialeon run --strict-readiness`)."""

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
from tests.storage.fixtures import full_council_run_result

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
SECRET = "sk-live-SECRET-never-shown-0123456789"
_CREATED = []


@pytest_asyncio.fixture(autouse=True)
async def _dispose():
    yield
    while _CREATED:
        await _CREATED.pop().engine.dispose()


async def _components(**states):
    """Os papéis internos padrão de `Settings` são todos "anthropic"."""
    providers = {
        name: ScriptedApiProvider(
            name, [], api_key=SECRET, default_model=f"{name}-configured", prerequisite=states.get(name)
        )
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


def _no_calls(components):
    return all(p.requests == [] for p in components.providers.values())


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_readiness_and_strict_flag_are_opt_in():
    parser = _build_parser()
    assert parser.parse_args(["run", "q"]).strict_readiness is False
    assert parser.parse_args(["run", "q", "--strict-readiness"]).strict_readiness is True
    args = parser.parse_args(["readiness", "--providers", "openai,gemini", "--source", "s", "--json"])
    assert (args.command, args.providers, args.source, args.as_json) == ("readiness", "openai,gemini", "s", True)
    assert parser.parse_args(["readiness"]).providers is None


def test_top_level_help_lists_readiness():
    help_text = " ".join(_build_parser().format_help().split())
    assert "readiness Mostra a prontidão local do Conselho para uma pergunta, sem executá-la." in help_text


# ---------------------------------------------------------------------------
# dialeon readiness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_human_output_separates_participants_from_internal_stages(capsys):
    components = await _components(anthropic="missing", gemini="unknown")

    code = await commands.cmd_readiness(
        components, providers=["openai", "gemini"], source_text=None, as_json=False
    )
    out = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert out.startswith(
        "prontidão local do Conselho: falta configuração local em dependência(s) do caminho pedido"
    )
    assert "não testa credenciais, serviços nem modelos" in out
    participants, internal = out.split("etapas internas")
    assert "  - participante: openai (modelo pedido: openai-configured, o padrão configurado) -- configuração local presente" in participants
    assert "  - participante: gemini (modelo pedido: gemini-configured, o padrão configurado) -- configuração local não verificável (não quer dizer que falte)" in participants
    assert "(podem ser alcançadas, conforme o andamento da pergunta)" in internal
    assert "  - extração de afirmações: anthropic (modelo configurado: anthropic-configured) -- falta a configuração local" in internal
    assert "  - análise de fonte: anthropic (modelo configurado: anthropic-configured) -- não se aplica a esta pergunta (sem fonte)" in internal
    assert "  - revisão semântica da redação: anthropic (modelo configurado: anthropic-configured) -- falta a configuração local" in internal
    assert "O Dialeon não troca de provider nem de modelo." in out
    assert out.rstrip().endswith(
        "admissão estrita (dialeon run --strict-readiness): a configuração local bloquearia a pergunta"
    )
    assert SECRET not in out
    assert _no_calls(components)
    assert await components.repository.list_runs() == []


@pytest.mark.asyncio
async def test_readiness_json_is_the_public_schema_and_uses_the_run_defaults(capsys):
    components = await _components()

    # sem --providers: todos os providers, o mesmo default de `dialeon run`
    code = await commands.cmd_readiness(components, providers=None, source_text="uma fonte", as_json=True)
    body = json.loads(capsys.readouterr().out)

    assert code == commands.EXIT_OK
    assert list(body) == [
        "contract_version",
        "summary",
        "strict_admission",
        "known_degradation_fingerprint",
        "dependencies",
    ]
    assert body["known_degradation_fingerprint"] is None
    assert body["summary"] == "all_met"
    assert [d["provider"] for d in body["dependencies"] if d["role"] == "participant"] == [
        "anthropic",
        "gemini",
        "openai",
    ]
    source = [d for d in body["dependencies"] if d["role"] == "source_analysis"]
    assert source[0]["applicability"] == "potential"


@pytest.mark.asyncio
@pytest.mark.parametrize("states", [{}, {"anthropic": "unknown"}])
async def test_readiness_without_blockers_never_claims_the_question_would_be_admitted(capsys, states):
    """A prévia só decide a parte LOCAL da admissão estrita: outras validações
    do pedido (pergunta, fonte, quórum...) ainda podem recusar a execução."""
    components = await _components(**states)

    await commands.cmd_readiness(components, providers=["openai", "gemini"], source_text=None, as_json=False)
    out = capsys.readouterr().out

    last = out.rstrip().splitlines()[-1]
    assert last == (
        "admissão estrita (dialeon run --strict-readiness): a configuração local não bloquearia "
        "a pergunta (as demais validações do pedido continuam valendo)"
    )
    assert "admitiria" not in out and "admite" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("source_text", ["", "   "])
async def test_readiness_treats_a_blank_source_as_absent_like_run(capsys, source_text):
    components = await _components()

    await commands.cmd_readiness(components, providers=["openai"], source_text=source_text, as_json=True)
    body = json.loads(capsys.readouterr().out)

    assert [d["applicability"] for d in body["dependencies"] if d["role"] == "source_analysis"] == ["not_applicable"]


@pytest.mark.asyncio
async def test_readiness_rejects_an_unknown_provider(capsys):
    components = await _components()

    code = await commands.cmd_readiness(components, providers=["nope"], source_text=None, as_json=True)

    assert code == commands.EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_provider"


@pytest.mark.asyncio
async def test_readiness_rejects_an_oversized_source(capsys):
    components = await _components()

    code = await commands.cmd_readiness(components, providers=["openai"], source_text="x" * 20_001, as_json=True)

    assert code == commands.EXIT_INVALID_INPUT
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# dialeon run --strict-readiness
# ---------------------------------------------------------------------------


async def _run(components, *, strict, as_json):
    return await commands.cmd_run(
        components,
        question="Qual a capital do Brasil?",
        providers=["openai", "gemini"],
        source_text=None,
        as_json=as_json,
        strict_readiness=strict,
    )


@pytest.mark.asyncio
async def test_strict_run_is_refused_before_any_run_or_call(capsys):
    components = await _components(anthropic="missing")

    code = await _run(components, strict=True, as_json=True)
    error = json.loads(capsys.readouterr().err)["error"]

    assert code == commands.EXIT_INVALID_INPUT
    assert error["code"] == "council_prerequisites_missing"
    assert error["details"]["readiness"]["strict_admission"] == "blocked"
    assert await components.repository.list_runs() == []
    assert _no_calls(components)


@pytest.mark.asyncio
async def test_strict_run_refusal_human_output_shows_what_is_missing(capsys):
    components = await _components(anthropic="missing")

    code = await _run(components, strict=True, as_json=False)
    err = capsys.readouterr().err

    assert code == commands.EXIT_INVALID_INPUT
    assert "Nenhuma execução foi criada." in err
    assert "  - juiz: anthropic (modelo configurado: anthropic-configured) -- falta a configuração local" in err


@pytest.mark.asyncio
async def test_strict_run_with_everything_met_records_strict_admission(capsys):
    components = await _components()

    code = await _run(components, strict=True, as_json=True)
    body = json.loads(capsys.readouterr().out)

    assert code == commands.EXIT_OK
    assert body["council_admission"]["mode"] == "strict"
    assert body["council_admission"]["readiness"]["summary"] == "all_met"


@pytest.mark.asyncio
async def test_ordinary_run_keeps_the_v130_acceptance_and_discloses_it_afterwards(capsys):
    components = await _components(anthropic="missing")

    code = await _run(components, strict=False, as_json=False)
    out = capsys.readouterr().out

    assert code == commands.EXIT_OK
    assert (
        "prontidão_local_no_aceite: falta configuração local em dependência(s) do caminho pedido "
        "(admissão padrão)"
    ) in out
    assert "  - extração de afirmações: anthropic (modelo configurado: anthropic-configured) -- falta a configuração local" in out


@pytest.mark.asyncio
async def test_get_and_audit_show_the_acceptance_facts(capsys):
    components = await _components(gemini="unknown")
    await _run(components, strict=True, as_json=True)
    run_id = json.loads(capsys.readouterr().out)["id"]

    await commands.cmd_get(components, run_id=run_id, as_json=True)
    detail = json.loads(capsys.readouterr().out)
    await commands.cmd_audit(components, run_id=run_id, as_json=False)
    audit_human = capsys.readouterr().out

    assert detail["council_admission"]["readiness"]["summary"] == "some_unknown"
    assert (
        "prontidão_local_no_aceite: nenhuma ausência conhecida; alguma configuração local não pôde "
        "ser verificada (admissão estrita)"
    ) in audit_human
    assert "  - participante: gemini (modelo pedido: gemini-configured, o padrão configurado) -- configuração local não verificável" in audit_human


@pytest.mark.asyncio
async def test_direct_rejects_the_strict_readiness_flag(capsys):
    components = await _components()

    code = await commands.cmd_run_direct(
        components,
        question="q",
        providers=["openai"],
        source_text=None,
        as_json=True,
        strict_readiness=True,
    )
    error = json.loads(capsys.readouterr().err)["error"]

    assert code == commands.EXIT_INVALID_INPUT
    assert error["details"]["errors"][0]["type"] == "direct_readiness_admission_not_supported"
    assert await components.repository.list_runs() == []


def test_admission_line_names_the_acknowledged_degradation_only_when_it_was_captured():
    from app.cli.output import _human_council_admission_lines
    from app.council.readiness import CouncilAdmission, CouncilExecutionDependencies, evaluate_council_readiness
    from app.presentation.mappers import council_admission_public
    from tests.storage.fixtures import run_config

    rc = run_config()
    readiness = evaluate_council_readiness(
        CouncilExecutionDependencies.from_run_config(rc),
        local_prerequisites={"openai": "met", "anthropic": "missing"},
        configured_default_models={"openai": "gpt-configured", "anthropic": "claude-configured"},
    )
    v2 = CouncilAdmission(
        mode="standard",
        known_degradation_acknowledged=True,
        acknowledged_degradation_fingerprint=readiness.known_degradation_fingerprint,
        readiness=readiness,
    )
    v1 = CouncilAdmission(
        contract_version="council_admission_v1", mode="standard", known_degradation_acknowledged=True, readiness=readiness
    )

    assert _human_council_admission_lines(council_admission_public(v2))[0].endswith(
        "(admissão padrão, degradação reconhecida no envio: exatamente a avaliada no aceite)"
    )
    assert _human_council_admission_lines(council_admission_public(v1))[0].endswith(
        "(admissão padrão, degradação reconhecida no envio; qual situação foi reconhecida não foi registrado)"
    )
