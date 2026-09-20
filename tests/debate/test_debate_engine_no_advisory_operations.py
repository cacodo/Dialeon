"""
Contrato do pipeline CORRENTE (`DebateEngine.run()`): agrupamento intra-round
(claim_grouping_v4) e reconciliação cross-round
(cross_round_claim_reconciliation_v2) NÃO são mais executados.

Ambas eram operações consultivas e não-destrutivas cujas propostas nenhum
consumidor semântico lia (só ficavam auditáveis). Removê-las da execução:
- NÃO altera as claims atuais (as extraídas continuam autoritativas; só a
  revisão explícita `parent_claim_id` da extração aposenta uma claim);
- NÃO cria registros fictícios (`ClaimProcessingAttempt`, digest, uso/custo)
  para operações que não rodaram;
- libera o orçamento antes gasto nelas para as etapas seguintes.

Histórico (runs antigas com agrupamento/reconciliação) segue legível --
tests/debate/test_advisory_operations_history.py. Nenhum provider real aqui.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.debate import claim_extraction as ce
from app.debate import schemas as debate_schemas
from app.debate.claims import get_current_claims
from app.debate.debate_engine import DebateEngine
from app.debate.processing_record import ClaimProcessingAttempt
from app.debate.schemas import MAX_EXTRACTED_CLAIMS
from app.judge.context import build_judge_request
from app.models.provider_models import ModelIdentitySource, ProviderResponse, TokenUsage
from app.orchestrator.config import QuorumPolicy, RunConfig
from tests.debate.fakes import CallableProvider

REPO = Path(__file__).resolve().parents[2]
PROCESSOR = "claude-processor"


def _run_config(enabled_providers, **overrides) -> RunConfig:
    fields = dict(
        question="Qual é o valor correto?",
        enabled_providers=enabled_providers,
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=4096,  # campo mantido por compat; nunca usado numa chamada
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        round_dispatch_timeout_seconds=5.0,
        claim_processor_provider=PROCESSOR,
        judge_provider=PROCESSOR,
        editor_provider=PROCESSOR,
        source_analyzer_provider=PROCESSOR,
    )
    fields.update(overrides)
    return RunConfig(**fields)


def _ok(provider: str, text: str, *, input_tokens: int = 10, output_tokens: int = 5) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model="fake-model",
        model="fake-model",
        model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
        status="success",
        text=text,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        cost_usd=None,
        latency_ms=10,
        attempts=1,
    )


def _extraction(*texts: str) -> str:
    return json.dumps({"claims": [{"text": t, "revises_claim_id": None} for t in texts]})


def _participant(provider_name: str, initial_text: str, critique_text: str):
    async def handler(call_index: int, request) -> ProviderResponse:
        return _ok(provider_name, initial_text if call_index == 1 else critique_text)

    return handler


def _known_claims(content: str) -> list[dict]:
    marker = "só pode referenciar um destes ids):"
    return json.loads(content.split(marker, 1)[1].strip()) if marker in content else []


def _processor(extraction_map: dict[str, str], *, revise: tuple[str, str] | None = None):
    """Processor dedicado: SÓ extração existe. Qualquer outra chamada (o que
    seria agrupamento/reconciliação) devolve um payload inofensivo, mas fica
    registrada em `received_requests` -- os testes afirmam depois que nenhuma
    chegou (uma exceção aqui poderia ser engolida como falha de transporte)."""

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" in content:
            if revise is not None and revise[0] in content and _known_claims(content):
                parent_id = next(c["id"] for c in _known_claims(content) if c["text"].startswith(revise[0]))
                payload = {"claims": [{"text": revise[1], "revises_claim_id": parent_id}]}
                return _ok(PROCESSOR, json.dumps(payload))
            for marker, extraction_json in extraction_map.items():
                if marker in content:
                    return _ok(PROCESSOR, extraction_json)
        return _ok(PROCESSOR, json.dumps({"claims": []}))

    return handler


def _providers(participant, processor) -> dict[str, CallableProvider]:
    return {
        "openai": CallableProvider("openai", participant),
        PROCESSOR: CallableProvider(PROCESSOR, processor),
    }


def _all_requests(providers) -> list:
    return [r for p in providers.values() for r in p.received_requests]


def _is_advisory_request(request) -> bool:
    body = request.messages[0].content
    return (
        "CLAIMS_BRUTAS" in body
        or "CLAIMS_ATUAIS" in body
        or '"clusters"' in (request.system_prompt or "")
        or '"equivalence_clusters"' in (request.system_prompt or "")
    )


def _fingerprint(claims):
    return sorted(
        (
            c.text,
            c.round_introduced,
            tuple(sorted(s.provider for s in c.supporting_model_response_ids)),
            c.parent_claim_id is not None,
            len(c.merged_from_claim_ids),
            c.superseded_by is not None,
            c.source_model_response_id is None,
            c.support_scope_model_count,
        )
        for c in claims
    )


# ---------------------------------------------------------------------------
# 1. Nenhuma requisição de agrupamento/reconciliação; nenhum registro fictício
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_normal_run_makes_zero_grouping_or_reconciliation_provider_requests():
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor(
            {
                "resposta inicial": _extraction("Claim do Round 1."),
                "resposta de crítica": _extraction("Claim do Round 2."),
            }
        ),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    requests = _all_requests(providers)
    assert requests  # a execução realmente aconteceu
    assert not [r for r in requests if _is_advisory_request(r)]
    # o processor recebeu EXATAMENTE uma requisição por resposta extraída
    # (R1 + R2) -- nenhuma chamada extra
    assert providers[PROCESSOR].call_count == 2
    assert providers["openai"].call_count == 2  # rodada inicial + crítica
    assert result.debate_skipped_reason is None and result.critique_round is not None
    assert {c.text for c in get_current_claims(result.claims)} == {"Claim do Round 1.", "Claim do Round 2."}


@pytest.mark.asyncio
async def test_no_grouping_or_reconciliation_attempt_or_provenance_exists_for_new_runs():
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor(
            {
                "resposta inicial": _extraction("Claim A."),
                "resposta de crítica": _extraction("Claim B."),
            }
        ),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    attempts = result.claim_processing_attempts
    assert len(attempts) == 2  # só as 2 extrações reais -- nenhum registro "pulado"
    assert {a.operation for a in attempts} == {"extraction"}
    assert {a.request_provenance.contract_version for a in attempts} == {"claim_extraction_v2"}
    dumped = json.dumps([a.model_dump(mode="json") for a in attempts])
    for absent in (
        "claim_grouping",
        "cross_round_claim_reconciliation",
        "accepted_normalized",
        '"grouping"',
        '"reconciliation"',
    ):
        assert absent not in dumped


@pytest.mark.asyncio
async def test_run_accounting_contains_only_calls_that_really_ran():
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor(
            {
                "resposta inicial": _extraction("Claim A."),
                "resposta de crítica": _extraction("Claim B."),
            }
        ),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    real_calls = sum(p.call_count for p in providers.values())
    assert real_calls == 4  # 2 participante + 2 extração
    # 10 in + 5 out por chamada real: nada de uso sintético de operação que não rodou
    assert result.cumulative_input_tokens == 10 * real_calls
    assert result.cumulative_output_tokens == 5 * real_calls
    assert sum(a.usage.output_tokens for a in result.claim_processing_attempts) == 5 * 2


@pytest.mark.asyncio
async def test_max_output_tokens_grouping_is_kept_in_run_config_but_never_sent():
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor({"resposta inicial": _extraction("Claim A.")}),
    )
    config = _run_config(["openai"], max_output_tokens_grouping=7777)

    await DebateEngine(providers).run(config)

    assert config.max_output_tokens_grouping == 7777  # campo preservado (compat de runs antigas)
    assert 7777 not in {r.max_tokens for r in _all_requests(providers)}


# ---------------------------------------------------------------------------
# 2. Autoridade das claims: as EXTRAÍDAS seguem autoritativas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extracted_claims_are_the_current_claims_with_no_canonical_or_merge():
    r1 = ["O céu é azul devido ao espalhamento de Rayleigh.", "A cor do céu é azul."]
    r2 = "A cor azul do céu vem do espalhamento de Rayleigh da luz solar."
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor({"resposta inicial": _extraction(*r1), "resposta de crítica": _extraction(r2)}),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    # semanticamente parecidas (mesma ou entre rodadas) COEXISTEM: nada é deduplicado
    assert sorted(c.text for c in result.claims) == sorted([*r1, r2])
    assert sorted(c.text for c in get_current_claims(result.claims)) == sorted([*r1, r2])
    for claim in result.claims:
        assert claim.source_model_response_id is not None  # nenhuma canônica (essas têm None)
        assert not claim.merged_from_claim_ids
        assert claim.parent_claim_id is None
        assert claim.superseded_by is None
        assert claim.support_scope_model_count is None
        assert len(claim.supporting_model_response_ids) == 1  # nenhuma união de suporte


@pytest.mark.asyncio
async def test_identical_claims_from_different_providers_never_union_or_transfer_support():
    async def processor(call_index: int, request):
        content = request.messages[0].content
        assert "CLAIMS_BRUTAS" not in content and "CLAIMS_ATUAIS" not in content
        return _ok(PROCESSOR, _extraction("Todos concordam: Brasília é a capital."))

    providers = {
        "openai": CallableProvider("openai", _participant("openai", "resposta openai", "crítica openai")),
        "gemini": CallableProvider("gemini", _participant("gemini", "resposta gemini", "crítica gemini")),
        PROCESSOR: CallableProvider(PROCESSOR, processor),
    }

    result = await DebateEngine(providers).run(_run_config(["openai", "gemini"]))

    assert len(result.claims) == 4  # 2 respostas x 2 rodadas: sem nenhuma redução de cardinalidade
    for claim in result.claims:
        assert len({s.model_response_id for s in claim.supporting_model_response_ids}) == 1
        assert claim.support_scope_model_count is None
    assert {
        (c.round_introduced, next(iter(c.supporting_model_response_ids)).provider) for c in result.claims
    } == {(1, "openai"), (1, "gemini"), (2, "openai"), (2, "gemini")}


@pytest.mark.asyncio
async def test_no_hidden_cardinality_cap_beyond_the_extraction_contract_limit():
    """Cada resposta pode extrair até MAX_EXTRACTED_CLAIMS claims (limite do
    contrato de extração, imposto na PRÓPRIA extração). Nada depois disso
    reduz a cardinalidade: R1 + R2 = 2 * MAX claims atuais."""
    r1 = [f"Afirmação distinta da rodada 1 número {i}." for i in range(MAX_EXTRACTED_CLAIMS)]
    r2 = [f"Afirmação distinta da rodada 2 número {i}." for i in range(MAX_EXTRACTED_CLAIMS)]
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor({"resposta inicial": _extraction(*r1), "resposta de crítica": _extraction(*r2)}),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    assert len(result.claims) == 2 * MAX_EXTRACTED_CLAIMS
    assert [c.text for c in get_current_claims(result.claims)] == [*r1, *r2]


@pytest.mark.asyncio
async def test_claim_set_matches_what_extraction_alone_produced_whatever_the_texts():
    """Mesmas respostas extraídas => mesmo conjunto de claims atuais que o
    pipeline antigo entregava (agrupamento v4 e reconciliação v2 eram
    não-destrutivos): só as extraídas, na ordem de extração."""
    r1 = ["Claim A.", "Claim B."]
    r2 = ["Claim C."]
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor({"resposta inicial": _extraction(*r1), "resposta de crítica": _extraction(*r2)}),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    assert [(c.text, c.round_introduced) for c in get_current_claims(result.claims)] == [
        ("Claim A.", 1),
        ("Claim B.", 1),
        ("Claim C.", 2),
    ]
    assert _fingerprint(result.claims) == _fingerprint(get_current_claims(result.claims))


@pytest.mark.asyncio
async def test_judge_receives_the_original_extracted_claims_unchanged():
    r1_text = "O SaaS reduz a carga de manutenção."
    r2_text = "O SaaS tem custo previsível por assinatura."
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor({"resposta inicial": _extraction(r1_text), "resposta de crítica": _extraction(r2_text)}),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    body = build_judge_request("Pergunta?", result, current, 8192).messages[0].content
    assert r1_text in body and r2_text in body
    assert len(current) == 2


# ---------------------------------------------------------------------------
# 3. Revisão explícita: única coisa que aposenta uma claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_extraction_revision_is_the_only_thing_that_retires_a_claim():
    r1_kept = "Uma claim independente do Round 1."
    providers = _providers(
        _participant("openai", "resposta inicial", "resposta de crítica"),
        _processor(
            {"resposta inicial": _extraction("O total é 264.", r1_kept)},
            revise=("O total é 264", "O total é 270, corrigindo a afirmação anterior de 264."),
        ),
    )

    result = await DebateEngine(providers).run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    assert {c.text for c in current} == {
        "O total é 270, corrigindo a afirmação anterior de 264.",
        r1_kept,
    }
    revision = next(c for c in current if c.parent_claim_id is not None)
    assert revision.merged_from_claim_ids == []
    parent = next(c for c in result.claims if c.id == revision.parent_claim_id)
    assert parent.text == "O total é 264." and parent.parent_claim_id is None
    assert parent.id not in {c.id for c in current}
    # exatamente UMA claim tem parent_claim_id; nenhuma linhagem criada por outra via
    assert len([c for c in result.claims if c.parent_claim_id is not None]) == 1
    assert all(not c.merged_from_claim_ids for c in result.claims)
    assert not [r for r in _all_requests(providers) if _is_advisory_request(r)]


# ---------------------------------------------------------------------------
# 4. Orçamento: o gasto consultivo economizado libera etapas seguintes
# ---------------------------------------------------------------------------


def _budget_handler(participant_name: str):
    """Uso por chamada (in+out): rodada inicial 4000, extração 2000; crítica
    e extração da crítica só existem se o gate deixar. Um agrupamento R1
    antigo gastava mais 1000 (700+300) entre a extração e o gate."""

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        assert "CLAIMS_BRUTAS" not in content and "CLAIMS_ATUAIS" not in content
        if call_index == 1:
            return _ok(participant_name, "Brasília é a capital.", input_tokens=3000, output_tokens=1000)
        if "RESPOSTA_A_ANALISAR" in content:
            text = _extraction("Brasília é a capital.")
            return _ok(participant_name, text, input_tokens=1500, output_tokens=500)
        return _ok(participant_name, "crítica: mantenho a resposta.", input_tokens=200, output_tokens=100)

    return handler


@pytest.mark.asyncio
async def test_saved_advisory_spend_lets_critique_proceed_within_the_same_budget():
    """6.500 tokens: rodada inicial (4.000) + extração (2.000) = 6.000 < 6.500,
    então a crítica RODA. Com o agrupamento antigo (+1.000) o total antes do
    gate seria 7.000 >= 6.500 e a crítica seria pulada
    (`budget_exhausted_before_critique`) -- gasto consultivo removido não
    consome mais o orçamento das etapas seguintes."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_handler("anthropic"))}
    config = _run_config(
        ["anthropic"], claim_processor_provider="anthropic", max_total_tokens=6500
    )

    result = await DebateEngine(providers).run(config)

    assert result.debate_skipped_reason is None
    assert result.critique_round is not None
    # o total real inclui SÓ chamadas que rodaram: inicial + extração + crítica + extração da crítica
    assert result.cumulative_input_tokens == 3000 + 1500 + 200 + 1500
    assert result.cumulative_output_tokens == 1000 + 500 + 100 + 500
    assert not [r for r in providers["anthropic"].received_requests if _is_advisory_request(r)]
    # rodada inicial + extração + crítica + extração da crítica -- e nada mais
    assert providers["anthropic"].call_count == 4


@pytest.mark.asyncio
async def test_budget_gate_semantics_are_unchanged_at_the_exact_boundary():
    """A semântica do gate não mudou: 6.000 gastos com max_total_tokens=6.000
    continuam esgotando (`>=`) e pulam a crítica; o que muda é só QUANTO já
    foi gasto quando o gate é avaliado."""
    providers = {"anthropic": CallableProvider("anthropic", _budget_handler("anthropic"))}
    config = _run_config(
        ["anthropic"], claim_processor_provider="anthropic", max_total_tokens=6000
    )

    result = await DebateEngine(providers).run(config)

    assert result.debate_skipped_reason == "budget_exhausted_before_critique"
    assert result.critique_round is None
    assert result.cumulative_budget_exceeded is True
    assert result.cumulative_input_tokens == 3000 + 1500
    assert result.cumulative_output_tokens == 1000 + 500
    assert providers["anthropic"].call_count == 2


# ---------------------------------------------------------------------------
# 5. Guardas estruturais: nenhum caminho de execução alcançável
# ---------------------------------------------------------------------------


def test_the_execution_entry_points_no_longer_exist():
    for name in (
        "group_claims",
        "reconcile_claims",
        "_run_structured_grouping_call",
        "_parse_and_validate_grouping_partition",
        "_parse_and_validate_reconciliation_equivalence",
    ):
        assert not hasattr(ce, name), name
    for name in ("ClaimGroupingPartitionOutput", "CrossRoundEquivalenceProposalOutput"):
        assert not hasattr(debate_schemas, name), name


def test_debate_engine_never_references_advisory_operation_machinery():
    source = (REPO / "app/debate/debate_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    code_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    imported_from_extraction = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.debate.claim_extraction"
        for alias in node.names
    }

    assert imported_from_extraction == {"extract_claims"}
    for name in (
        "group_claims",
        "reconcile_claims",
        "max_output_tokens_grouping",
        "CLAIM_GROUPING_CONTRACT_VERSION",
        "CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION",
        "_build_grouping_request",
        "_build_reconciliation_request",
    ):
        assert name not in code_names, name


def test_no_production_module_outside_claim_extraction_builds_advisory_requests():
    offenders = []
    for path in (REPO / "app").rglob("*.py"):
        if path.name == "claim_extraction.py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("_build_grouping_request", "_build_reconciliation_request", "group_claims(", "reconcile_claims("):
            if needle in text:
                offenders.append((str(path.relative_to(REPO)), needle))
    assert offenders == []


def test_no_execution_flag_exists_for_the_removed_operations():
    from app.config import Settings
    from app.presentation.schemas import CreateRunRequest

    for model in (Settings, RunConfig, CreateRunRequest):
        assert not any(
            ("grouping" in n or "reconciliation" in n) and ("enabled" in n or "skip" in n or "disable" in n)
            for n in model.model_fields
        ), model.__name__


def test_historical_vocabulary_is_retained_for_reading_old_runs():
    import typing

    operations = set(typing.get_args(ClaimProcessingAttempt.model_fields["operation"].annotation))
    statuses = set(typing.get_args(ClaimProcessingAttempt.model_fields["parse_status"].annotation))

    assert {"extraction", "grouping", "reconciliation"} <= operations
    assert "accepted_normalized" in statuses
    assert "max_output_tokens_grouping" in RunConfig.model_fields


def test_extraction_schema_keeps_its_whitespace_stripping_config():
    from app.debate.schemas import ClaimExtractionOutput

    assert ClaimExtractionOutput.model_config.get("str_strip_whitespace") is True
