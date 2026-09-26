"""CLI -- apresentação ANSWER FIRST da saída humana de `run`/`get`.

A saída humana de uma run concluída põe a resposta primeiro, depois as
limitações, um bloco curto de detalhes, como a resposta foi montada e onde
ver a auditoria completa. A escolha da resposta é a mesma ordem de fallback
de sempre. A saída `--json` NÃO faz parte desta apresentação: continua sendo
exatamente o schema público serializado.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.cli import commands, output
from app.config import Settings
from app.presentation.mappers import completed_run_response, quorum_failure_run_response
from app.text_safety import terminal_safe_text
from tests.api.helpers import build_test_components
from tests.storage.fixtures import full_council_run_result, now, quorum_failure_exception, run_config
from tests.storage.test_linguistic_realization_persistence import _with_linguistic_realization
from tests.storage.test_natural_answer_persistence import _with_primary_and_natural_answer
from tests.storage.test_primary_answer_persistence import _with_primary_answer

_CREATED = []

ANSWER_LABELS = {"resposta:", "resposta principal:", "resposta (avaliação completa):"}


async def _components(**kwargs):
    components = await build_test_components(Settings(_env_file=None), **kwargs)
    _CREATED.append(components)
    return components


@pytest_asyncio.fixture(autouse=True)
async def _dispose_created_engines():
    yield
    while _CREATED:
        await _CREATED.pop().engine.dispose()


def _response(result):
    return completed_run_response(result, provider_execution_policy=None, default_model_authority_snapshot=None)


def _realization():
    return _with_linguistic_realization(full_council_run_result())[0]


def _natural():
    return _with_primary_and_natural_answer(full_council_run_result())[0]


def _primary():
    return _with_primary_answer(full_council_run_result())[0]


# (fixture, rótulo da resposta, texto que É a resposta)
VARIANTS = {
    "realization": (
        _realization,
        "resposta:",
        lambda fa: fa.linguistic_realization.rendered_text,
    ),
    "natural": (_natural, "resposta:", lambda fa: fa.natural_answer.rendered_text),
    "primary": (_primary, "resposta principal:", lambda fa: fa.primary_answer.rendered_text),
    "complete": (full_council_run_result, "resposta (avaliação completa):", lambda fa: fa.answer_text),
}


@pytest.mark.parametrize("variant", list(VARIANTS))
def test_the_answer_comes_first_verbatim_and_metadata_comes_after(variant):
    make, label, answer_of = VARIANTS[variant]
    response = _response(make())
    lines = output.human_run_result(response).split("\n")

    # 1. a resposta é a primeira coisa impressa, com o texto canônico (só
    #    neutralizado pro terminal, como sempre)
    assert lines[0] == label
    assert lines[1] == terminal_safe_text(answer_of(response.final_answer))

    # 2. metadado da execução só depois da resposta
    first_metadata = min(
        lines.index(line)
        for line in lines
        if line.startswith(("status:", "run_id:", "custo estimado:", "editor_model:", "confiança_do_juiz:"))
    )
    assert first_metadata > 1
    assert lines.index("detalhes:") < lines.index("status: concluída") < lines.index("como a resposta foi montada:")

    # 3. limitações visíveis entre a resposta e os detalhes
    assert response.final_answer.limitations
    assert lines.index(label) < lines.index("limitações:") < lines.index("detalhes:")
    for item in response.final_answer.limitations:
        assert f"  - {terminal_safe_text(item)}" in lines

    # 4. onde aprofundar, no fim
    assert lines[-1] == f"auditoria completa: dialeon audit {response.id}"


@pytest.mark.parametrize("variant", ["realization", "natural", "primary"])
def test_the_complete_assessment_stays_available_below_the_answer(variant):
    make, label, _ = VARIANTS[variant]
    response = _response(make())
    lines = output.human_run_result(response).split("\n")

    assert lines.index(label) < lines.index("detalhes:") < lines.index("avaliação completa:")
    assert lines[lines.index("avaliação completa:") + 1] == terminal_safe_text(response.final_answer.answer_text)
    if variant in ("realization", "natural"):
        assert lines.index("detalhes:") < lines.index("resposta principal (estruturada):")


def test_complete_fallback_prints_the_complete_assessment_once_as_the_answer():
    response = _response(full_council_run_result())
    text = output.human_run_result(response)

    assert "avaliação completa:" not in text.split("\n")
    assert text.count(terminal_safe_text(response.final_answer.answer_text)) == 1


def test_details_block_holds_only_concise_run_context():
    response = _response(_natural())
    lines = output.human_run_result(response).split("\n")
    details = lines[lines.index("detalhes:") + 1 : lines.index("como a resposta foi montada:") - 1]

    assert details == [
        "status: concluída",
        f"run_id: {response.id}",
        "modelos: " + ", ".join(response.config.enabled_providers),
        f"custo estimado: {response.accounting.estimated_cost_usd:.6f} USD (contabilidade completa: sim)",
        f"concluída em: {response.completed_at.isoformat()}",
    ]


@pytest.mark.asyncio
async def test_run_and_get_print_the_same_answer_first_output_for_the_same_run(capsys):
    result = _natural()
    components = await _components(
        debate_result=result.debate_result, judge_result=result.judge_result, editor_result=result.editor_result
    )

    assert await commands.cmd_run(
        components, question="Qual a capital do Brasil?", providers=None, source_text=None, as_json=False
    ) == commands.EXIT_OK
    run_out = capsys.readouterr().out
    run_id = run_out.rsplit("dialeon audit ", 1)[1].strip()

    assert await commands.cmd_get(components, run_id=run_id, as_json=False) == commands.EXIT_OK
    get_out = capsys.readouterr().out

    assert run_out == get_out
    assert run_out.startswith("resposta:\n")


@pytest.mark.asyncio
async def test_quorum_failure_keeps_the_outcome_primary_and_never_fabricates_an_answer(capsys):
    components = await _components()
    failure_id = await components.repository.save_quorum_failure(
        quorum_failure_exception(), run_config=run_config(), started_at=now(), failed_at=now()
    )

    await commands.cmd_get(components, run_id=failure_id, as_json=False)
    lines = capsys.readouterr().out.rstrip("\n").split("\n")

    assert lines[0] == "status: quórum insuficiente"
    assert lines[1] == "Nenhuma resposta final foi composta -- o quórum mínimo não foi atingido."
    assert not ANSWER_LABELS & set(lines)
    assert lines.index("detalhes:") < lines.index(f"run_id: {failure_id}")
    assert lines[-1] == f"motivo de cada modelo: dialeon audit {failure_id}"


@pytest.mark.asyncio
async def test_running_and_failed_runs_keep_status_first_and_have_no_answer(capsys):
    components = await _components()
    for run_id in ("run-sem-desfecho", "run-falhou"):
        await components.repository.save_accepted(
            run_id,
            run_config=run_config(),
            started_at=now(),
            provider_execution_policy=components.provider_execution_policy,
        )
    await components.repository.save_unexpected_failure(
        "run-falhou", failed_at=now(), failure_classification="WeirdBug", failure_message="Erro interno."
    )

    for run_id, status in (("run-sem-desfecho", "status: sem desfecho registrado"), ("run-falhou", "status: falhou")):
        await commands.cmd_get(components, run_id=run_id, as_json=False)
        out = capsys.readouterr().out
        assert out.split("\n")[0] == status
        assert not ANSWER_LABELS & set(out.split("\n"))


# ---------------------------------------------------------------------------
# JSON: fronteira de compatibilidade -- exatamente o schema serializado,
# sem nenhum campo/ordem da apresentação humana.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", list(VARIANTS))
@pytest.mark.asyncio
async def test_get_json_is_exactly_the_serialized_public_schema(variant, capsys):
    result = VARIANTS[variant][0]()
    components = await _components()
    await components.repository.save_success(result)
    record = await components.repository.get_run(result.id)
    expected = completed_run_response(
        record.council_run_result,
        provider_execution_policy=record.provider_execution_policy,
        default_model_authority_snapshot=record.default_model_authority_snapshot,
    ).model_dump_json()

    await commands.cmd_get(components, run_id=result.id, as_json=True)

    out = capsys.readouterr().out
    assert out == expected + "\n"
    assert "detalhes" not in out and "auditoria completa" not in out


@pytest.mark.asyncio
async def test_quorum_get_json_is_exactly_the_serialized_public_schema(capsys):
    components = await _components()
    failure_id = await components.repository.save_quorum_failure(
        quorum_failure_exception(), run_config=run_config(), started_at=now(), failed_at=now()
    )
    expected = quorum_failure_run_response(await components.repository.get_run(failure_id)).model_dump_json()

    await commands.cmd_get(components, run_id=failure_id, as_json=True)

    assert capsys.readouterr().out == expected + "\n"


@pytest.mark.asyncio
async def test_run_json_keeps_the_public_schema_keys_and_order(capsys):
    result = _natural()
    components = await _components(
        debate_result=result.debate_result, judge_result=result.judge_result, editor_result=result.editor_result
    )

    await commands.cmd_run(components, question="q", providers=None, source_text=None, as_json=True)

    body = json.loads(capsys.readouterr().out)
    assert list(body) == [
        "status",
        "id",
        "started_at",
        "completed_at",
        "final_answer",
        "accounting",
        "config",
        "provider_execution_policy",
        "default_model_authority_snapshot",
    ]


@pytest.mark.asyncio
async def test_providers_json_and_human_output_are_untouched(capsys):
    components = await _components(provider_names=("openai", "anthropic"))

    await commands.cmd_providers(components, as_json=True)
    assert capsys.readouterr().out == '{"providers":["anthropic","openai"]}\n'

    await commands.cmd_providers(components, as_json=False)
    assert capsys.readouterr().out == "anthropic\nopenai\n"
