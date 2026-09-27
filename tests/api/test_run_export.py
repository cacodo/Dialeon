"""Provenance-Preserving Human-Readable Run Export -- `GET /runs/{id}/export`.

Um documento de texto para pessoas, só de runs CONCLUÍDAS, com a resposta
primeiro e a proveniência mínima para interpretá-la. Nenhuma chamada a
provider, nenhuma escrita no banco. Fonte, respostas brutas, tentativas e
erros de provider ficam fora por padrão."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.app import create_app
from app.config import Settings
from app.editor.result import EditorResult, FinalAnswer
from app.debate.result import CritiqueResult, DebateResult
from app.judge.result import JudgeResult
from app.models.domain import ClaimAssessment, ClaimSupport, JudgeVerdict
from app.models.provider_models import ModelIdentitySource
from app.orchestrator.result import InitialResponsesResult, RoundResult
from app.editor.natural_answer import (
    NATURAL_ANSWER_CONTRACT_VERSION,
    NaturalAnswer,
    _render_natural_answer_text_v1,
)
from app.orchestrator.participant_models import ParticipantModelChoice
from app.models.provider_models import ProviderExecutionPolicy, TokenUsage
from app.presentation.mappers import completed_run_audit
from app.presentation.run_export import export_filename, render_council_run_export
from app.providers.errors import ProviderTimeoutError
from app.text_safety import terminal_safe_text
from app.version import get_product_version
from tests.api.helpers import make_components_factory
from tests.direct.fakes import ScriptedApiProvider, ok
from tests.storage.fixtures import (
    claim,
    full_council_run_result,
    model_response,
    now,
    quorum_failure_exception,
    run_config,
    very_rich_council_run_result,
    with_recomputed_reconciliation,
)
from tests.storage.test_linguistic_realization_persistence import _with_linguistic_realization
from tests.storage.test_natural_answer_persistence import (
    _with_primary_and_natural_answer,
    _with_unsafe_primary_answer,
)
from tests.storage.test_primary_answer_persistence import _with_primary_answer

POLICY = ProviderExecutionPolicy(attempt_timeout_seconds=5.0, max_transport_attempts_per_completion=1)
GENERATED_AT = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _client(providers=None, **kwargs):
    factory = make_components_factory(
        provider_instances=providers or {"openai": ScriptedApiProvider("openai", [ok()], default_model="gpt-conf")},
        provider_execution_policy=POLICY,
        **kwargs,
    )
    return TestClient(create_app(settings=Settings(_env_file=None), components_factory=factory))


def _save(client, result):
    repository = client.app.state.components.repository
    client.portal.call(lambda: repository.save_success(result))
    return result.id


def _export(client, run_id):
    return client.get(f"/runs/{run_id}/export")


def _render(result) -> str:
    audit = completed_run_audit(result, provider_execution_policy=None)
    return render_council_run_export(audit, generated_at=GENERATED_AT, product_version="9.9.9")


def _db_fingerprint(client) -> str:
    """Todas as linhas de todas as tabelas -- nada pode mudar ao exportar."""
    engine = client.app.state.components.engine

    async def dump():
        async with engine.connect() as conn:
            tables = [
                r[0]
                for r in await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                )
            ]
            parts = []
            for table in tables:
                rows = (await conn.execute(text(f"SELECT * FROM {table}"))).all()
                parts.append(f"{table}:{sorted(map(repr, rows))}")
            return "\n".join(parts)

    return hashlib.sha256(client.portal.call(dump).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Resposta direta
# ---------------------------------------------------------------------------


def test_completed_direct_run_exports_the_answer_first_with_direct_provenance():
    provider = ScriptedApiProvider("openai", [ok("Brasília é a capital.", observed_model="gpt-x-2026-09-01")])
    with _client({"openai": provider}) as client:
        created = client.post(
            "/runs",
            json={"question": "Qual a capital?", "enabled_providers": ["openai"], "kind": "direct",
                  "requested_model": "gpt-x"},
        ).json()
        resp = _export(client, created["id"])

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert resp.headers["content-disposition"] == f'attachment; filename="dialeon-direta-{created["id"]}.txt"'
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"
    body = resp.text
    assert body.index("Pergunta") < body.index("Qual a capital?") < body.index("Resposta\n")
    assert body.index("Brasília é a capital.") < body.index("Como esta resposta foi produzida")
    assert "Resposta direta: a resposta de UM modelo" in body
    assert "Provider: openai" in body
    assert "Modelo pedido: gpt-x (escolhido nesta pergunta)" in body
    assert "Modelo reportado: gpt-x-2026-09-01 (reportado pelo provider)" in body
    assert f"pelo Dialeon {get_product_version()}" in body
    # nada do Conselho é fabricado
    for council_only in ("Participantes", "Juiz e editor", "Limitações registradas", "Fonte fornecida"):
        assert council_only not in body
    # uso/custo não entram
    assert "custo" not in body.split("Sobre esta exportação")[0].lower()
    assert "tokens" not in body.lower()


def test_direct_fallback_identity_is_never_presented_as_provider_confirmation():
    provider = ScriptedApiProvider("openai", [ok("Brasília.", observed_model=None)], default_model="gpt-conf")
    with _client({"openai": provider}) as client:
        created = client.post(
            "/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}
        ).json()
        body = _export(client, created["id"]).text

    assert "Modelo pedido: gpt-conf (padrão configurado quando a pergunta foi aceita)" in body
    assert "Modelo reportado: não informado pelo provider (o registro repete o modelo pedido, sem confirmação)" in body
    assert "gpt-conf (reportado pelo provider)" not in body


def test_a_known_output_truncation_is_stated():
    provider = ScriptedApiProvider(
        "openai", [("Resposta cortada", TokenUsage(input_tokens=1, output_tokens=1), None, "length")]
    )
    with _client({"openai": provider}) as client:
        created = client.post(
            "/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}
        ).json()
        body = _export(client, created["id"]).text

    assert "pode estar incompleta" in body


def test_a_later_default_change_never_rewrites_the_exported_direct_model():
    provider = ScriptedApiProvider("openai", [ok()], default_model="gpt-then")
    with _client({"openai": provider}) as client:
        created = client.post(
            "/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}
        ).json()
        provider._default_model_name = "gpt-now"  # configuração atual mudou
        body = _export(client, created["id"]).text

    assert "Modelo pedido: gpt-then (padrão configurado quando a pergunta foi aceita)" in body
    assert "gpt-now" not in body


def test_a_pre_origin_direct_row_exports_as_configured_default_from_its_contract():
    with _client() as client:
        created = client.post(
            "/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}
        ).json()
        engine = client.app.state.components.engine

        async def drop_origin():
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE direct_runs SET run_config_json = json_remove(run_config_json, "
                         "'$.requested_model_origin') WHERE id = :id"),
                    {"id": created["id"]},
                )

        client.portal.call(drop_origin)
        body = _export(client, created["id"]).text

    assert "Modelo pedido: gpt-conf (padrão configurado quando a pergunta foi aceita)" in body


# ---------------------------------------------------------------------------
# Conselho: escolha da forma apresentada (a MESMA regra da web e da CLI)
# ---------------------------------------------------------------------------


VARIANTS = {
    "linguistic_realization": (
        lambda: _with_linguistic_realization(full_council_run_result())[0],
        lambda fa: fa.linguistic_realization.rendered_text,
        "texto redigido a partir da resposta principal",
    ),
    "natural_answer": (
        lambda: _with_primary_and_natural_answer(full_council_run_result())[0],
        lambda fa: fa.natural_answer.rendered_text,
        "resposta em texto corrido gerada da resposta principal",
    ),
    "primary_answer": (
        lambda: _with_primary_answer(full_council_run_result())[0],
        lambda fa: fa.primary_answer.rendered_text,
        "resposta principal estruturada",
    ),
    "complete_assessment": (full_council_run_result, lambda fa: fa.answer_text, "avaliação completa"),
}


@pytest.mark.parametrize("kind", list(VARIANTS))
def test_council_export_presents_the_same_selected_form(kind):
    make, answer_of, label = VARIANTS[kind]
    result = make()
    body = _render(result)
    answer = answer_of(result.editor_result.final_answer)

    answer_section = body.split("Resposta\n--------\n", 1)[1]
    first_answer_line = next(line for line in answer.split("\n") if line)
    assert answer_section.startswith("    " + terminal_safe_text(first_answer_line))
    assert f"Forma apresentada: {label}" in body
    assert "não necessariamente a forma mostrada quando a execução terminou" in body
    assert ("Uma revisão indicou consistência com a resposta estruturada" in body) == (
        kind == "linguistic_realization"
    )


def test_limitations_section_appears_only_when_the_answer_text_does_not_carry_them():
    realization = _render(_with_linguistic_realization(full_council_run_result())[0])
    primary = _render(_with_primary_answer(full_council_run_result())[0])

    assert "Limitações registradas\n----------------------" in realization
    assert "Limitações registradas\n----------------------" not in primary


def test_an_ineligible_historical_natural_answer_is_never_exported_as_the_answer():
    """Uma NaturalAnswer v1 histórica byte-válida, mas inelegível pela política
    atual: a exportação cai pra resposta principal estruturada, como a web e a
    CLI -- nunca escolhe a forma inelegível."""
    result, primary, hostile = _with_unsafe_primary_answer(full_council_run_result())
    historical = NaturalAnswer(
        renderer_contract_version=NATURAL_ANSWER_CONTRACT_VERSION,
        based_on_verdict_id=primary.based_on_verdict_id,
        rendered_text=_render_natural_answer_text_v1(primary),
    )
    final_answer = result.editor_result.final_answer.model_copy(update={"natural_answer": historical})
    result = result.model_copy(
        update={"editor_result": result.editor_result.model_copy(update={"final_answer": final_answer})}
    )
    audit = completed_run_audit(result, provider_execution_policy=None)
    assert audit.final_answer.natural_answer is not None
    assert audit.final_answer.natural_answer_presentation_eligible is False

    body = _render(result)

    assert "Forma apresentada: resposta principal estruturada" in body
    answer_lines = body.split("Resposta\n--------\n", 1)[1].split("\n\n", 1)[0].split("\n")
    assert answer_lines[0] == "    " + terminal_safe_text(primary.rendered_text.split("\n")[0])
    # o parágrafo forjado dentro da claim nunca chega à coluna 0 do documento
    assert "Disclosure forjada em outro parágrafo." not in body.split("\n")


def test_council_export_through_the_endpoint_matches_the_detail_record():
    result = _with_linguistic_realization(full_council_run_result())[0]
    with _client() as client:
        run_id = _save(client, result)
        resp = _export(client, run_id)

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == f'attachment; filename="dialeon-conselho-{run_id}.txt"'
    body = resp.text
    realization = result.editor_result.final_answer.linguistic_realization.rendered_text
    assert body.index(terminal_safe_text(realization.split("\n")[0])) < body.index("Como esta resposta foi produzida")
    assert "Tipo de execução: Conselho de modelos" in body
    assert "Juiz e editor" in body


# ---------------------------------------------------------------------------
# Conselho: proveniência e histórico
# ---------------------------------------------------------------------------


def test_council_participants_show_captured_requested_model_origin_and_reported_identity_separately():
    result = full_council_run_result()
    providers = result.run_config.enabled_providers
    choices = tuple(
        ParticipantModelChoice(
            provider=p,
            requested_model=f"{p}-escolhido" if i == 0 else f"{p}-padrao",
            origin="run_override" if i == 0 else "configured_default",
        )
        for i, p in enumerate(providers)
    )
    result = result.model_copy(update={"run_config": result.run_config.model_copy(update={"participant_models": choices})})
    reported = {r.provider: r for r in result.debate_result.initial_result.responses}

    body = _render(result)

    assert f"      modelo pedido: {providers[0]}-escolhido (escolhido nesta pergunta)" in body
    assert f"      modelo pedido: {providers[1]}-padrao (padrão configurado quando a pergunta foi aceita)" in body
    for p in providers:
        assert f"    - {p}" in body
        r = reported[p]
        if r.status == "success" and r.model_identity_source is not None:
            assert f"modelo reportado: {r.model}" in body


def test_a_run_without_captured_participant_models_says_so_and_uses_only_recorded_facts():
    result = full_council_run_result()
    result = result.model_copy(update={"run_config": result.run_config.model_copy(update={"participant_models": None})})

    body = _render(result)

    assert "origem não registrada: execução anterior a este registro" in body
    assert "Os modelos pedidos e a origem da escolha não foram registrados" in body
    assert "(padrão configurado quando a pergunta foi aceita)" not in body.split("Participantes", 1)[1].split("Juiz", 1)[0]


def test_historical_identity_source_is_not_captured_never_invented():
    result = full_council_run_result()
    responses = [
        r.model_copy(update={"model_identity_source": None}) for r in result.debate_result.initial_result.responses
    ]
    initial = result.debate_result.initial_result.model_copy(update={"responses": responses})
    result = result.model_copy(
        update={"debate_result": result.debate_result.model_copy(update={"initial_result": initial})}
    )

    body = _render(result)

    assert "origem da identidade não registrada: execução anterior a este registro" in body


# ---------------------------------------------------------------------------
# Fronteira de privacidade
# ---------------------------------------------------------------------------


def _with_markers(result):
    """Planta marcadores em material que NUNCA pode ir pra exportação."""
    debate = result.debate_result
    responses = [
        r.model_copy(
            update={
                "response_text": (f"RAW-PARTICIPANT-MARKER-{i}" if r.response_text is not None else None),
                "error": (
                    r.error.model_copy(update={"message": "PROVIDER-ERROR-MARKER"}) if r.error is not None else None
                ),
            }
        )
        for i, r in enumerate(debate.initial_result.responses)
    ]
    attempts = [a.model_copy(update={"raw_output_text": "RAW-ATTEMPT-MARKER"}) for a in debate.claim_processing_attempts]
    debate = debate.model_copy(
        update={
            "initial_result": debate.initial_result.model_copy(update={"responses": responses}),
            "claim_processing_attempts": attempts,
        }
    )
    judge_attempts = [
        a.model_copy(update={"raw_output_text": "RAW-JUDGE-ATTEMPT-MARKER"}) for a in result.judge_result.attempts
    ]
    editor_attempts = [
        a.model_copy(update={"raw_output_text": "RAW-EDITOR-ATTEMPT-MARKER"}) for a in result.editor_result.attempts
    ]
    return result.model_copy(
        update={
            "debate_result": debate,
            "judge_result": result.judge_result.model_copy(update={"attempts": judge_attempts}),
            "editor_result": result.editor_result.model_copy(update={"attempts": editor_attempts}),
            "run_config": result.run_config.model_copy(update={"source_text": "SEGREDO-DA-FONTE fornecido pelo usuário"}),
        }
    )


def test_raw_outputs_attempts_provider_errors_and_source_text_never_leak():
    result = _with_markers(full_council_run_result())
    with _client() as client:
        run_id = _save(client, result)
        body = _export(client, run_id).text

    for marker in (
        "RAW-PARTICIPANT-MARKER",
        "PROVIDER-ERROR-MARKER",
        "RAW-ATTEMPT-MARKER",
        "RAW-JUDGE-ATTEMPT-MARKER",
        "RAW-EDITOR-ATTEMPT-MARKER",
        "SEGREDO-DA-FONTE",
    ):
        assert marker not in body, marker


def test_source_presence_and_source_analysis_status_are_stated_without_the_text_or_excerpts():
    result = very_rich_council_run_result()
    assert result.run_config.source_text
    audit = completed_run_audit(result, provider_execution_policy=None)
    excerpts = [
        c.excerpt for c in (audit.source_analysis.claim_results if audit.source_analysis else []) if getattr(c, "excerpt", None)
    ]

    body = _render(result)

    flat = " ".join(body.split())
    assert "Fonte fornecida pelo usuário" in body
    assert "O texto completo da fonte não é incluído como parte separada desta exportação" in flat
    assert "trechos dele podem aparecer dentro da própria resposta" in flat
    assert "não é verificação externa" in flat
    assert "Sem o texto completo, esta exportação não permite conferir essa comparação." in flat
    assert "Análise da fonte: " in body
    assert result.run_config.source_text not in body
    for excerpt in excerpts:
        assert excerpt not in body


def test_no_source_means_no_source_section():
    body = _render(full_council_run_result())

    assert "Fonte fornecida pelo usuário" not in body


# ---------------------------------------------------------------------------
# Status sem resposta concluída
# ---------------------------------------------------------------------------


def test_missing_run_is_404():
    with _client() as client:
        resp = _export(client, "nao-existe")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"


def test_running_and_failed_council_runs_are_not_exportable():
    with _client() as client:
        repository = client.app.state.components.repository
        client.portal.call(
            lambda: repository.save_accepted("rodando", run_config=run_config(), started_at=now(), provider_execution_policy=POLICY)
        )
        client.portal.call(
            lambda: repository.save_accepted("falhou", run_config=run_config(), started_at=now(), provider_execution_policy=POLICY)
        )
        client.portal.call(
            lambda: repository.save_unexpected_failure(
                "falhou", failed_at=now(), failure_classification="RuntimeError", failure_message="x"
            )
        )
        running, failed = _export(client, "rodando"), _export(client, "falhou")

    for resp, status in ((running, "running"), (failed, "failed")):
        assert resp.status_code == 409
        assert resp.json()["error"] == {
            "code": "run_not_exportable",
            "message": "Só uma pergunta concluída, com resposta, pode ser exportada.",
            "details": {"status": status},
        }


def test_insufficient_quorum_is_not_exportable():
    providers = {n: ScriptedApiProvider(n, [ok()]) for n in ("openai", "anthropic")}
    with _client(providers, quorum_exc=quorum_failure_exception()) as client:
        failed = client.post("/runs", json={"question": "q", "enabled_providers": ["openai", "anthropic"]})
        run_id = failed.json()["error"]["details"]["run_id"]
        resp = _export(client, run_id)

    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {"status": "insufficient_quorum"}


def test_failed_and_running_direct_runs_are_not_exportable():
    provider = ScriptedApiProvider("openai", [ProviderTimeoutError("timeout")])
    with _client({"openai": provider}) as client:
        failed = client.post("/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}).json()
        from app.direct.models import DirectRunConfig

        config = DirectRunConfig(
            question="q", provider="openai", requested_model="m", requested_model_origin="configured_default",
            max_output_tokens=10,
        )
        repository = client.app.state.components.repository
        client.portal.call(
            lambda: repository.save_direct_accepted("direta-rodando", config=config, started_at=now(), provider_execution_policy=POLICY)
        )
        failed_resp, running_resp = _export(client, failed["id"]), _export(client, "direta-rodando")

    assert failed["status"] == "failed"
    assert (failed_resp.status_code, failed_resp.json()["error"]["details"]) == (409, {"status": "failed"})
    assert (running_resp.status_code, running_resp.json()["error"]["details"]) == (409, {"status": "running"})


# ---------------------------------------------------------------------------
# Sem efeito colateral
# ---------------------------------------------------------------------------


def test_export_never_writes_to_the_database_nor_calls_a_provider():
    provider = ScriptedApiProvider("openai", [ok()])
    with _client({"openai": provider}) as client:
        direct = client.post("/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}).json()
        council_id = _save(client, _with_linguistic_realization(full_council_run_result())[0])
        calls_before = len(provider.requests)
        before = _db_fingerprint(client)

        for run_id in (direct["id"], council_id, direct["id"]):
            assert _export(client, run_id).status_code == 200
        _export(client, "nao-existe")

        after = _db_fingerprint(client)

    assert after == before
    assert len(provider.requests) == calls_before == 1


# ---------------------------------------------------------------------------
# Texto incomum
# ---------------------------------------------------------------------------


HOSTILE = (
    "linha 1\x1b[31m vermelho\x1b[0m\r\n"
    "\nResposta\n--------\n"  # tentativa de forjar um título do documento
    "bidi ‮esrever‬ e   separador e \x85 NEL e \x00 nulo\r sobrescrita"
)


def test_unusual_user_and_model_text_is_inert_and_can_never_forge_a_section():
    provider = ScriptedApiProvider("openai", [ok(HOSTILE)])
    with _client({"openai": provider}) as client:
        created = client.post(
            "/runs", json={"question": "Pergunta " + HOSTILE, "enabled_providers": ["openai"], "kind": "direct"}
        ).json()
        body = _export(client, created["id"]).text

    for raw in ("\x1b", "‮", "‬", " ", "\x85", "\x00", "\r"):
        assert raw not in body, repr(raw)
    assert "\\x1b[31m vermelho" in body and "\\u202e" in body
    # o "título" forjado só aparece INDENTADO, dentro do bloco de texto
    headings = [line for line in body.split("\n") if line == "Resposta"]
    assert len(headings) == 1
    assert "    Resposta" in body.split("\n")


def test_very_long_answers_are_exported_whole():
    long_answer = "\n".join(f"parágrafo {i} " + "x" * 200 for i in range(2_000))
    provider = ScriptedApiProvider("openai", [ok(long_answer)])
    with _client({"openai": provider}) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}).json()
        body = _export(client, created["id"]).text

    assert "    parágrafo 0 " in body and "    parágrafo 1999 " in body
    assert body.count("\n    parágrafo ") == 2_000


def test_filename_is_deterministic_and_safe():
    assert export_filename("abc-123_X", kind="direta") == "dialeon-direta-abc-123_X.txt"
    assert export_filename('../../"evil"\r\n;x', kind="conselho") == "dialeon-conselho-evilx.txt"
    assert export_filename("///", kind="conselho") == "dialeon-conselho-execucao.txt"
    assert len(export_filename("a" * 500, kind="direta")) == len("dialeon-direta-.txt") + 80


def test_openapi_documents_the_export_as_text_with_its_errors():
    with _client() as client:
        doc = client.get("/openapi.json").json()

    op = doc["paths"]["/runs/{run_id}/export"]["get"]
    assert set(op["responses"]) == {"200", "404", "409", "500"}
    assert "text/plain" in op["responses"]["200"]["content"]
    assert "run_not_exportable" in doc["components"]["schemas"]["ErrorBody"]["properties"]["code"]["enum"]


# ---------------------------------------------------------------------------
# Repair (revisão independente) -- configurado != executado na atribuição do
# juiz/editor; avisos de omissão que não contradizem a própria resposta.
# ---------------------------------------------------------------------------


def _no_verdict_result():
    """Caminho determinístico SEM veredito: o juiz não produziu avaliação e o
    editor nunca foi chamado (nenhuma tentativa) -- um estado válido e
    persistível, com juiz/editor apenas CONFIGURADOS."""
    base = full_council_run_result()
    judge = base.judge_result.model_copy(
        update={"verdict": None, "attempts": [], "verdict_unavailable_reason": "judge_transport_failed"}
    )
    final_answer = FinalAnswer(
        answer_text="Não foi possível concluir a avaliação das afirmações.",
        limitations=["A avaliação do juiz não pôde ser concluída."],
        status="deterministic_no_verdict",
    )
    editor = EditorResult(
        final_answer=final_answer,
        attempts=[],
        fallback_reason="judge_verdict_unavailable",
        editor_provider=base.editor_result.editor_provider,
        cumulative_budget_exceeded=False,
    )
    return with_recomputed_reconciliation(base.model_copy(update={"judge_result": judge, "editor_result": editor}))


def _budget_fallback_result():
    """Veredito registrado, mas o orçamento acabou ANTES do editor: resposta
    montada deterministicamente, nenhuma tentativa de editor."""
    base = full_council_run_result()
    verdict = base.judge_result.verdict
    final_answer = FinalAnswer(
        answer_text="Brasília é a capital do Brasil.",
        limitations=list(verdict.debate_limitations),
        status="deterministic_from_verdict",
        based_on_verdict_id=verdict.id,
        judge_confidence=verdict.confidence,
    )
    editor = EditorResult(
        final_answer=final_answer,
        attempts=[],
        fallback_reason="budget_exhausted_before_editor",
        editor_provider=base.editor_result.editor_provider,
        cumulative_budget_exceeded=True,
    )
    return base.model_copy(update={"editor_result": editor})


@pytest.mark.parametrize("make", [_no_verdict_result, _budget_fallback_result])
def test_an_editor_that_did_not_run_is_never_credited_with_the_presentation(make):
    result = make()
    configured_editor = result.run_config.editor_provider
    with _client() as client:
        run_id = _save(client, result)  # estado persistível real
        body = _export(client, run_id).text

    assert "Editor:" not in body
    assert "Juiz e editor" not in body
    assert "responsável pela apresentação" not in body
    assert f"Editor: {configured_editor}" not in body
    assert "montada automaticamente" in body
    assert "Composição final por fallback:" in body


def test_no_verdict_export_does_not_credit_the_configured_judge_either():
    result = _no_verdict_result()
    body = _render(result)

    assert "Juiz: nenhuma avaliação registrada nesta execução" in body
    assert f"Juiz: {result.run_config.judge_provider}" not in body
    assert "O juiz avaliou" not in body
    assert "Avaliação do juiz indisponível: Falha de comunicação com o modelo juiz." in body
    assert "Como a resposta foi montada: montada automaticamente sem avaliação do juiz" in body


def test_an_editor_with_a_recorded_accepted_attempt_is_credited_from_the_record():
    result = full_council_run_result()
    fa = result.editor_result.final_answer
    assert fa.editor_model is not None

    body = _render(result)

    assert "Juiz e editor" in body
    assert f"modelo da tentativa aceita: {fa.editor_model}" in body


def test_an_answer_containing_a_source_excerpt_is_exported_verbatim_with_a_truthful_notice():
    excerpt = "a receita cresceu 12% em 2025"
    base = full_council_run_result()
    fa = base.editor_result.final_answer
    answer = fa.answer_text + f"\nNota da fonte: trecho “{excerpt}”."
    result = base.model_copy(
        update={
            "run_config": base.run_config.model_copy(update={"source_text": f"Relatório: {excerpt}. Outros dados."}),
            "editor_result": base.editor_result.model_copy(
                update={"final_answer": fa.model_copy(update={"answer_text": answer})}
            ),
        }
    )

    body = _render(result)
    flat = " ".join(body.split())

    # a resposta sai INTEIRA, sem redação -- o trecho da fonte continua nela
    answer_block = body.split("Resposta\n--------\n", 1)[1].split("\n\n", 1)[0]
    assert answer_block == "\n".join("    " + terminal_safe_text(line) for line in answer.split("\n"))
    assert excerpt in answer_block
    # e o aviso não nega isso
    assert "trechos dele podem aparecer dentro da própria resposta" in flat
    assert "não está incluído nesta exportação" not in flat
    assert "pode conter trechos desse material" in flat
    # o texto completo da fonte, como campo separado, continua fora
    assert "Relatório: " not in body


def test_direct_omission_wording_matches_that_the_answer_is_the_provider_response():
    provider = ScriptedApiProvider("openai", [ok("Resposta do provider.")])
    with _client({"openai": provider}) as client:
        created = client.post("/runs", json={"question": "q", "enabled_providers": ["openai"], "kind": "direct"}).json()
        body = _export(client, created["id"]).text
    flat = " ".join(body.split())

    assert "Resposta do provider." in body
    assert "A resposta acima é a própria resposta registrada do provider, exportada inteira." in flat
    assert "respostas completas" not in flat
    assert "respostas individuais" not in flat
    assert "texto completo da fonte" not in flat  # a resposta direta nem aceita fonte


def _single_participant_council_result():
    """Conselho COERENTE com UM participante selecionado (openai) e UMA
    resposta de participante -- construído só com construtores validados
    (sem `model_copy(update=...)` em estado material). Uma run assim é válida
    (tests/direct/test_direct_execution.py::test_one_provider_council_runs_stay_council);
    a reconciliação é recalculada pela implementação de produção dentro de
    `full_council_run_result`."""
    response = model_response("openai")
    critique = model_response("openai", round_number=2, response_text="resposta de crítica")
    support = ClaimSupport(
        model_response_id=response.id,
        provider="openai",
        model=response.model,
        model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
    )
    only_claim = claim(response.id, [support], text="Brasília é a capital do Brasil.")
    debate = DebateResult(
        initial_result=InitialResponsesResult(
            responses=[response],
            successful_count=1,
            total_providers=1,
            insufficient_data_for_consensus=False,
            total_input_tokens=100,
            total_output_tokens=20,
            total_cost_usd=0.001,
            has_unknown_accounting_components=False,
            budget_exceeded=False,
        ),
        critique_round=CritiqueResult(
            round_result=RoundResult(
                round_number=2,
                responses=[critique],
                successful_count=1,
                total_participants=1,
                total_input_tokens=100,
                total_output_tokens=20,
                total_cost_usd=0.001,
                has_unknown_accounting_components=False,
            )
        ),
        claims=[only_claim],
        claim_processing_attempts=[],
        claim_processor_provider="anthropic",
        debate_skipped_reason=None,
        cumulative_budget_exceeded=False,
    )
    base = full_council_run_result()
    judge_attempt = base.judge_result.attempts[0]
    verdict = JudgeVerdict(
        evaluated_through_round=2,
        judge_model="claude-sonnet-5",
        judge_model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
        claim_assessments=[
            ClaimAssessment(claim_id=only_claim.id, verdict="supported", explanation="Sustentada pela resposta.")
        ],
        best_arguments_by={"openai/gpt-5.5": "Argumento direto."},
        debate_limitations=["Só um participante."],
        confidence=0.8,
        reasoning="Um único participante respondeu.",
    )
    judge = JudgeResult(
        verdict=verdict,
        attempts=[judge_attempt],
        verdict_unavailable_reason=None,
        judge_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    editor = EditorResult(
        final_answer=FinalAnswer(
            answer_text="Brasília é a capital do Brasil.",
            limitations=["Só um participante."],
            status="llm_composed",
            editor_model="claude-sonnet-5",
            editor_model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
            based_on_verdict_id=verdict.id,
            judge_confidence=0.8,
        ),
        attempts=list(base.editor_result.attempts),
        fallback_reason=None,
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    return full_council_run_result(
        run_config=run_config(enabled_providers=["openai"]),
        debate_result=debate,
        judge_result=judge,
        editor_result=editor,
    )


def test_a_single_participant_council_export_does_not_claim_plurality():
    """Uma run do Conselho com UM participante é válida (e o quórum padrão
    permite concluir com uma única resposta utilizável): a exportação nunca
    afirma que a resposta veio de vários modelos."""
    result = _single_participant_council_result()
    # o estado persistido é mesmo de um participante só -- nenhum segundo
    # provider escondido nas respostas, nos apoios nem nas rodadas
    assert result.run_config.enabled_providers == ("openai",)
    assert [r.provider for r in result.debate_result.initial_result.responses] == ["openai"]
    assert {s.provider for c in result.debate_result.claims for s in c.supporting_model_response_ids} == {"openai"}
    assert [r.provider for r in result.debate_result.critique_round.round_result.responses] == ["openai"]

    with _client() as client:
        run_id = _save(client, result)
        detail = client.get(f"/runs/{run_id}").json()
        resp = _export(client, run_id)

    assert detail["status"] == "completed"
    assert detail["config"]["enabled_providers"] == ["openai"]
    assert resp.status_code == 200
    body = resp.text
    flat = " ".join(body.split())
    participants = body.split("Participantes\n-------------\n", 1)[1].split("\n\n", 1)[0]
    assert [line.strip() for line in participants.split("\n") if line.startswith("    - ")] == ["- openai"]
    assert "vários modelos" not in flat
    assert "respostas dos modelos participantes" in flat
