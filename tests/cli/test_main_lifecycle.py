from __future__ import annotations

import json

import pytest

from app.cli import main as cli_main
from app.cli.commands import EXIT_INVALID_INPUT
from app.config import Settings
from tests.api.helpers import build_test_components
from tests.storage.fixtures import full_council_run_result


def _settings() -> Settings:
    return Settings(_env_file=None)


class _DisposeTrackingEngine:
    """Encapsula o AsyncEngine real -- `AsyncEngine.dispose` é um método
    real da classe (não um atributo de instância comum), então
    `components.engine.dispose = ...` falha com AttributeError
    ("attribute is read-only"). `AppComponents.engine` em si não é
    frozen, então trocar o objeto INTEIRO funciona -- nada além do
    `finally` de `_run_async` lê `components.engine` depois que os
    componentes já foram montados (repository/session_factory já
    capturaram o engine real internamente antes da troca)."""

    def __init__(self, real_engine, calls: list[bool]):
        self._real_engine = real_engine
        self._calls = calls

    async def dispose(self):
        self._calls.append(True)
        await self._real_engine.dispose()


def _patch_build_components(monkeypatch, **kwargs):
    """Substitui `build_app_components` (usado por `main._run_async`) por
    uma versão que devolve `AppComponents` de teste -- mesmo padrão de
    `make_components_factory`, adaptado pra CLI. Devolve a lista de
    chamadas a `engine.dispose()` pra inspeção posterior."""
    dispose_calls: list[bool] = []

    async def _fake_build(settings):
        components = await build_test_components(settings, **kwargs)
        components.engine = _DisposeTrackingEngine(components.engine, dispose_calls)
        return components

    monkeypatch.setattr(cli_main, "build_app_components", _fake_build)
    return dispose_calls


@pytest.mark.asyncio
async def test_successful_run_disposes_engine(monkeypatch, capsys):
    result = full_council_run_result()
    dispose_calls = _patch_build_components(
        monkeypatch,
        debate_result=result.debate_result,
        judge_result=result.judge_result,
        editor_result=result.editor_result,
    )

    exit_code = await cli_main._run_async(["run", "pergunta", "--providers", "openai,anthropic"])

    assert exit_code == 0
    assert dispose_calls == [True]


@pytest.mark.asyncio
async def test_unexpected_error_returns_exit_1_and_still_disposes(monkeypatch, capsys):
    """FakeDebateEngine com uma exceção não-mapeada (ValueError, o mesmo
    tipo que Orchestrator.run_round() levantaria pra um bug real de
    programação) -- nunca capturada pelos except específicos de
    cmd_run, propaga até o catch-all de _run_async."""
    dispose_calls = _patch_build_components(
        monkeypatch, debate_result=None, judge_result=None, editor_result=None
    )

    async def _broken_debate_engine_factory(settings):
        components = await build_test_components(settings)
        components.service._runner._debate_engine._exc = ValueError("bug real de programação")
        components.engine = _DisposeTrackingEngine(components.engine, dispose_calls)
        return components

    monkeypatch.setattr(cli_main, "build_app_components", _broken_debate_engine_factory)

    exit_code = await cli_main._run_async(["run", "pergunta", "--providers", "openai,anthropic"])

    assert exit_code == cli_main.EXIT_INTERNAL_ERROR
    assert dispose_calls == [True]
    out = capsys.readouterr()
    assert out.out == ""
    assert "Erro interno inesperado." in out.err
    assert "ValueError" not in out.err
    assert "bug real de programação" not in out.err


@pytest.mark.asyncio
async def test_unexpected_error_json_mode_emits_stable_error_shape(monkeypatch, capsys):
    async def _broken_factory(settings):
        components = await build_test_components(settings)
        components.service._runner._debate_engine._exc = RuntimeError("falha interna")
        return components

    monkeypatch.setattr(cli_main, "build_app_components", _broken_factory)

    exit_code = await cli_main._run_async(
        ["run", "pergunta", "--providers", "openai,anthropic", "--json"]
    )

    assert exit_code == cli_main.EXIT_INTERNAL_ERROR
    out = capsys.readouterr()
    body = json.loads(out.err)
    assert body["error"]["code"] == "internal_error"


@pytest.mark.asyncio
async def test_stdout_reserved_for_result_stderr_for_errors(monkeypatch, capsys):
    dispose_calls = _patch_build_components(monkeypatch, provider_names=("openai", "anthropic"))
    assert dispose_calls == []  # ainda não chamado

    exit_code = await cli_main._run_async(
        ["run", "pergunta", "--providers", "provider-fake"]
    )

    assert exit_code == EXIT_INVALID_INPUT
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err != ""
    assert dispose_calls == [True]


def test_main_returns_int_for_normal_exit(monkeypatch, capsys):
    """`main()` (síncrono, entrypoint real do console script) devolve um
    int -- nunca levanta -- pra qualquer fluxo que não seja erro de
    parsing do argparse (esse usa SystemExit nativamente, ver
    test_parser.py)."""

    async def _fake_build(settings):
        return await build_test_components(settings, provider_names=("openai", "anthropic"))

    monkeypatch.setattr(cli_main, "build_app_components", _fake_build)

    exit_code = cli_main.main(["providers"])

    assert isinstance(exit_code, int)
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Blocker 1 (patch de revisão): bootstrap (Settings()/build_app_components())
# precisa estar DENTRO do error boundary -- uma falha aqui (Settings
# malformado, init_db, construção de provider, validação interna de
# provider em bootstrap.py) deve virar exit 1 genérico como qualquer
# outro erro inesperado, nunca um traceback cru nem um AttributeError
# secundário tentando dispose() sobre components que nunca existiu.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_failure_returns_exit_1_generic_no_traceback(monkeypatch, capsys):
    """Cenário A: build_app_components levanta ANTES de devolver
    components -- exit 1, stdout vazio, stderr genérico, sem traceback,
    sem conteúdo de str(exc)."""

    async def _broken_build(settings):
        raise RuntimeError("segredo interno de bootstrap que nunca deveria vazar")

    monkeypatch.setattr(cli_main, "build_app_components", _broken_build)

    exit_code = await cli_main._run_async(["providers"])

    assert exit_code == cli_main.EXIT_INTERNAL_ERROR
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.strip() == "Erro interno inesperado."
    assert "RuntimeError" not in out.err
    assert "segredo interno de bootstrap" not in out.err
    assert "Traceback" not in out.err
    assert "File \"" not in out.err


@pytest.mark.asyncio
async def test_bootstrap_failure_json_mode_emits_clean_json(monkeypatch, capsys):
    """Cenário B: mesmo cenário com --json -- exit 1, stderr contém JSON
    válido, error.code == internal_error, nada de texto extra
    antes/depois (json.loads falharia se houvesse)."""

    async def _broken_build(settings):
        raise RuntimeError("falha de bootstrap")

    monkeypatch.setattr(cli_main, "build_app_components", _broken_build)

    exit_code = await cli_main._run_async(["providers", "--json"])

    assert exit_code == cli_main.EXIT_INTERNAL_ERROR
    out = capsys.readouterr()
    assert out.out == ""
    body = json.loads(out.err)  # levanta se houver qualquer texto extra
    assert body["error"]["code"] == "internal_error"
    assert "RuntimeError" not in out.err
    assert "falha de bootstrap" not in out.err


@pytest.mark.asyncio
async def test_bootstrap_failure_never_touches_nonexistent_components(monkeypatch, capsys):
    """Cenário C: uma falha durante Settings/bootstrap não tenta acessar
    `components.engine` (que seria None) -- se `_run_async` tentasse
    `None.engine.dispose()`, isso levantaria AttributeError DENTRO do
    finally, mascarando o RuntimeError original com um erro totalmente
    diferente. O teste passa silenciosamente (sem exceção nenhuma
    escapando) exatamente porque isso não acontece."""

    async def _broken_build(settings):
        raise RuntimeError("bootstrap falhou antes de qualquer components existir")

    monkeypatch.setattr(cli_main, "build_app_components", _broken_build)

    exit_code = await cli_main._run_async(["run", "pergunta"])  # nenhuma exceção deve escapar daqui

    assert exit_code == cli_main.EXIT_INTERNAL_ERROR


@pytest.mark.asyncio
async def test_post_bootstrap_failure_still_disposes_exactly_once(monkeypatch, capsys):
    """Cenário D: quando components SÃO construídos com sucesso e uma
    falha ocorre DEPOIS (durante o dispatch), engine.dispose() continua
    ocorrendo exatamente uma vez -- não regride o comportamento já
    coberto por test_unexpected_error_returns_exit_1_and_still_disposes,
    reafirmado aqui explicitamente como parte do conjunto do Blocker 1."""
    dispose_calls = _patch_build_components(monkeypatch)

    async def _broken_after_bootstrap(settings):
        components = await build_test_components(settings)
        components.service._runner._debate_engine._exc = ValueError("bug pós-bootstrap")
        components.engine = _DisposeTrackingEngine(components.engine, dispose_calls)
        return components

    monkeypatch.setattr(cli_main, "build_app_components", _broken_after_bootstrap)

    exit_code = await cli_main._run_async(["run", "pergunta", "--providers", "openai,anthropic"])

    assert exit_code == cli_main.EXIT_INTERNAL_ERROR
    assert dispose_calls == [True]


# ---------------------------------------------------------------------------
# Etapa 16 -- flag --source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cli_run_with_source_reaches_run_config(monkeypatch, capsys):
    from app.orchestrator.config import RunConfig

    captured: dict[str, RunConfig] = {}

    async def _fake_build(settings):
        components = await build_test_components(settings, provider_names=("openai", "anthropic"))
        original_run = components.service._runner._debate_engine.run

        async def _tracking_run(run_config):
            captured["run_config"] = run_config
            return await original_run(run_config)

        components.service._runner._debate_engine.run = _tracking_run
        return components

    monkeypatch.setattr(cli_main, "build_app_components", _fake_build)

    exit_code = await cli_main._run_async(
        ["run", "pergunta", "--providers", "openai,anthropic", "--source", "texto da fonte"]
    )

    # FakeDebateEngine sem result=... configurado levanta AssertionError
    # interna, capturada pelo catch-all de _run_async como erro
    # inesperado (exit 1) -- não importa aqui, só precisamos confirmar
    # que o run_config foi construído com o source_text certo ANTES
    # disso acontecer.
    assert exit_code == cli_main.EXIT_INTERNAL_ERROR
    assert captured["run_config"].source_text == "texto da fonte"


@pytest.mark.asyncio
async def test_cli_run_oversized_source_exits_2_no_call(monkeypatch, capsys):
    from app.orchestrator.config import MAX_SOURCE_TEXT_CHARACTERS

    dispose_calls = _patch_build_components(monkeypatch, provider_names=("openai", "anthropic"))

    exit_code = await cli_main._run_async(
        [
            "run",
            "pergunta",
            "--providers",
            "openai,anthropic",
            "--source",
            "x" * (MAX_SOURCE_TEXT_CHARACTERS + 1),
        ]
    )

    assert exit_code == EXIT_INVALID_INPUT
    out = capsys.readouterr()
    assert out.out == ""
    assert dispose_calls == [True]  # bootstrap aconteceu, mas nenhuma chamada de provider
