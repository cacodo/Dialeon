from __future__ import annotations

import json

import pytest

from app.orchestrator.config import QuorumPolicy, RunConfig
from app.source_analysis.analyzer import SourceAnalyzer
from app.source_analysis.models import RejectedSourceEntry, ValidSourceRelation
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response
from tests.judge.fixtures import debate_result, model_response, raw_claim

_SOURCE = (
    "O relatório anual confirma que a receita cresceu 12% em 2025. "
    "Não há menção a lucro líquido neste trecho."
)


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Qual foi a receita em 2025?",
        enabled_providers=["openai", "anthropic"],
        max_cost_usd=100.0,
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
        source_text=_SOURCE,
    )
    fields.update(overrides)
    return RunConfig(**fields)


def _payload(relations: list[dict]) -> str:
    return json.dumps({"claim_relations": relations})


# ---------------------------------------------------------------------------
# Skips estruturais
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_source_returns_none_no_call_made():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config(source_text=None))

    assert result is None
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_zero_current_claims_skips_with_reason_no_call():
    dr = debate_result([], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert result is not None
    assert result.skipped_reason == "no_claims_to_analyze"
    assert result.attempts == []
    assert result.claim_results == []
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_budget_exhausted_skips_zero_attempts_explicit_reason():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    rc = _run_config(max_total_tokens=1)  # já estourado pelas respostas iniciais
    provider = ScriptedProvider("anthropic", [])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, rc)

    assert result.skipped_reason == "budget_exhausted_before_source_analysis"
    assert result.attempts == []
    assert result.cumulative_budget_exceeded is True
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_unknown_source_analyzer_provider_raises_before_any_call():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    analyzer = SourceAnalyzer({"anthropic": ScriptedProvider("anthropic", [])})

    with pytest.raises(ValueError, match="source_analyzer_provider desconhecido"):
        await analyzer.analyze(dr, _run_config(source_analyzer_provider="provider-fake"))


# ---------------------------------------------------------------------------
# Granularidade — UMA chamada, todas as claims, resultado misto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_call_covers_multiple_claims_supports_contradicts_unresolved():
    c1 = raw_claim("A receita cresceu 12%.", "resp-1", provider="openai")
    c2 = raw_claim("Não houve crescimento de receita.", "resp-2", provider="anthropic")
    c3 = raw_claim("O lucro líquido foi de 5%.", "resp-3", provider="openai")
    dr = debate_result([c1, c2, c3], [model_response("openai"), model_response("anthropic")])

    payload = _payload(
        [
            {"claim_id": c1.id, "relation": "supports", "excerpt": "a receita cresceu 12% em 2025"},
            {"claim_id": c2.id, "relation": "contradicts", "excerpt": "a receita cresceu 12% em 2025"},
            {"claim_id": c3.id, "relation": "unresolved"},
        ]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert len(provider.received_requests) == 1  # UMA chamada, não uma por claim
    assert len(result.attempts) == 1
    assert result.attempts[0].parse_status == "accepted"
    assert len(result.claim_results) == 3

    by_claim = {r.claim_id: r for r in result.claim_results}
    assert isinstance(by_claim[c1.id], ValidSourceRelation)
    assert by_claim[c1.id].relation == "supports"
    assert by_claim[c1.id].excerpt_start is not None
    assert isinstance(by_claim[c2.id], ValidSourceRelation)
    assert by_claim[c2.id].relation == "contradicts"
    assert isinstance(by_claim[c3.id], ValidSourceRelation)
    assert by_claim[c3.id].relation == "unresolved"
    assert by_claim[c3.id].excerpt is None
    assert by_claim[c3.id].excerpt_start is None


# ---------------------------------------------------------------------------
# Verificação mecânica de excerpt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fabricated_excerpt_is_rejected_never_becomes_valid_relation():
    c1 = raw_claim("claim qualquer", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload(
        [{"claim_id": c1.id, "relation": "supports", "excerpt": "isso não existe na fonte"}]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert len(result.claim_results) == 1
    entry = result.claim_results[0]
    assert isinstance(entry, RejectedSourceEntry)
    assert entry.reason == "invalid_entry"
    assert entry.claim_id == c1.id


@pytest.mark.asyncio
async def test_excerpt_offsets_computed_by_application_not_trusted_from_model():
    c1 = raw_claim("claim qualquer", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    excerpt = "a receita cresceu 12% em 2025"
    expected_start = _SOURCE.find(excerpt)
    payload = _payload([{"claim_id": c1.id, "relation": "supports", "excerpt": excerpt}])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    relation = result.claim_results[0]
    assert isinstance(relation, ValidSourceRelation)
    assert relation.excerpt_start == expected_start
    assert relation.excerpt_end == expected_start + len(excerpt)


@pytest.mark.asyncio
async def test_duplicate_excerpt_occurrence_uses_first_match():
    source = "banana banana banana"
    c1 = raw_claim("claim sobre banana", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload([{"claim_id": c1.id, "relation": "supports", "excerpt": "banana"}])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config(source_text=source))

    relation = result.claim_results[0]
    assert relation.excerpt_start == 0  # primeira ocorrência, não a 2a/3a


@pytest.mark.asyncio
async def test_supports_or_contradicts_without_excerpt_is_rejected():
    c1 = raw_claim("claim qualquer", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload([{"claim_id": c1.id, "relation": "supports"}])  # sem excerpt
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    entry = result.claim_results[0]
    assert isinstance(entry, RejectedSourceEntry)
    assert entry.reason == "invalid_entry"


# ---------------------------------------------------------------------------
# Casos B-F de completude de claim_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_b_omitted_claim_produces_rejected_entry():
    c1 = raw_claim("claim endereçada", "resp-1", provider="openai")
    c2 = raw_claim("claim omitida", "resp-2", provider="anthropic")
    dr = debate_result([c1, c2], [model_response("openai"), model_response("anthropic")])
    payload = _payload(
        [{"claim_id": c1.id, "relation": "supports", "excerpt": "a receita cresceu 12% em 2025"}]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    by_claim = {r.claim_id: r for r in result.claim_results}
    assert isinstance(by_claim[c2.id], RejectedSourceEntry)
    assert by_claim[c2.id].reason == "omitted_by_model"
    assert by_claim[c2.id].raw_entry is None


@pytest.mark.asyncio
async def test_case_c_duplicate_claim_id_produces_single_rejected_no_valid_relation():
    c1 = raw_claim("claim duplicada", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload(
        [
            {"claim_id": c1.id, "relation": "supports", "excerpt": "a receita cresceu 12% em 2025"},
            {"claim_id": c1.id, "relation": "contradicts", "excerpt": "a receita cresceu 12% em 2025"},
        ]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert len(result.claim_results) == 1  # UM registro, não dois
    entry = result.claim_results[0]
    assert isinstance(entry, RejectedSourceEntry)
    assert entry.reason == "duplicate_claim_id"
    assert len(entry.raw_entry) == 2  # preserva os dois fragmentos originais


@pytest.mark.asyncio
async def test_case_d_unknown_claim_id_produces_rejected_with_none_claim_id():
    c1 = raw_claim("única claim corrente", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload(
        [
            {"claim_id": c1.id, "relation": "unresolved"},
            {"claim_id": "claim-que-nao-existe-123", "relation": "supports", "excerpt": "x"},
        ]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    unknown_entries = [r for r in result.claim_results if isinstance(r, RejectedSourceEntry) and r.claim_id is None]
    assert len(unknown_entries) == 1
    assert unknown_entries[0].reason == "invalid_entry"
    assert unknown_entries[0].raw_entry["claim_id"] == "claim-que-nao-existe-123"


@pytest.mark.asyncio
async def test_case_e_known_claim_malformed_relation_is_rejected():
    c1 = raw_claim("claim qualquer", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload([{"claim_id": c1.id, "relation": "operador_invalido_qualquer"}])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    entry = result.claim_results[0]
    assert isinstance(entry, RejectedSourceEntry)
    assert entry.claim_id == c1.id
    assert entry.reason == "invalid_entry"


@pytest.mark.asyncio
async def test_case_f_all_entries_invalid_top_level_still_accepted():
    c1 = raw_claim("claim 1", "resp-1", provider="openai")
    c2 = raw_claim("claim 2", "resp-2", provider="anthropic")
    dr = debate_result([c1, c2], [model_response("openai"), model_response("anthropic")])
    payload = _payload(
        [
            {"claim_id": c1.id, "relation": "supports"},  # sem excerpt -> inválido
            {"claim_id": c2.id, "relation": "algo_invalido"},  # relation inválida
        ]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert result.attempts[0].parse_status == "accepted"  # nunca "malformed"
    assert all(isinstance(r, RejectedSourceEntry) for r in result.claim_results)


@pytest.mark.asyncio
async def test_valid_and_invalid_entries_coexist_partial_success():
    c1 = raw_claim("claim válida", "resp-1", provider="openai")
    c2 = raw_claim("claim inválida", "resp-2", provider="anthropic")
    dr = debate_result([c1, c2], [model_response("openai"), model_response("anthropic")])
    payload = _payload(
        [
            {"claim_id": c1.id, "relation": "supports", "excerpt": "a receita cresceu 12% em 2025"},
            {"claim_id": c2.id, "relation": "supports"},  # sem excerpt
        ]
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    by_claim = {r.claim_id: r for r in result.claim_results}
    assert isinstance(by_claim[c1.id], ValidSourceRelation)
    assert isinstance(by_claim[c2.id], RejectedSourceEntry)


# ---------------------------------------------------------------------------
# Falhas de attempt (transporte / malformado / retry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_failure_produces_skipped_reason_and_attempt_record():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider("anthropic", [transport_error_response("anthropic")])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert result.skipped_reason == "source_analysis_transport_failed"
    assert len(result.attempts) == 1
    assert result.attempts[0].transport_status == "error"
    assert result.claim_results == []


@pytest.mark.asyncio
async def test_malformed_json_retries_once_then_fails():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "isto não é JSON"),
            text_response("anthropic", "ainda não é JSON"),
        ],
    )
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert len(provider.received_requests) == 2  # 1 tentativa inicial + 1 retry
    assert len(result.attempts) == 2
    assert result.skipped_reason == "source_analysis_output_invalid"


@pytest.mark.asyncio
async def test_malformed_then_accepted_recovers_via_retry():
    c1 = raw_claim("A receita cresceu 12%.", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    good_payload = _payload(
        [{"claim_id": c1.id, "relation": "supports", "excerpt": "a receita cresceu 12% em 2025"}]
    )
    provider = ScriptedProvider(
        "anthropic",
        [text_response("anthropic", "json quebrado"), text_response("anthropic", good_payload)],
    )
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert len(result.attempts) == 2
    assert result.attempts[0].parse_status == "malformed"
    assert result.attempts[1].parse_status == "accepted"
    assert len(result.claim_results) == 1


# ---------------------------------------------------------------------------
# Provenance / accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requested_and_effective_model_provenance_preserved():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload([])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    attempt = result.attempts[0]
    assert attempt.requested_model == provider.default_model
    assert attempt.model == "fake-model"


@pytest.mark.asyncio
async def test_usage_and_cost_accounted_in_result():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload([])
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert result.source_analysis_input_tokens >= 0
    assert result.source_analysis_output_tokens >= 0


# ---------------------------------------------------------------------------
# Isolamento: source_text só vai pro provider de análise dedicado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_text_never_sent_to_a_different_registered_provider():
    """Um segundo provider registrado (simulando um participante do
    Council) nunca é chamado pelo SourceAnalyzer -- só o
    source_analyzer_provider declarado em RunConfig recebe QUALQUER
    requisição, e portanto só ele pode ter visto source_text."""
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = _payload([])
    analyzer_provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    other_provider = ScriptedProvider("openai", [])  # nunca deveria ser chamado
    analyzer = SourceAnalyzer({"anthropic": analyzer_provider, "openai": other_provider})

    result = await analyzer.analyze(dr, _run_config(source_analyzer_provider="anthropic"))

    assert result.attempts[0].provider == "anthropic"
    assert other_provider.received_requests == []
    assert len(analyzer_provider.received_requests) == 1
    assert _SOURCE in analyzer_provider.received_requests[0].messages[0].content


# ---------------------------------------------------------------------------
# Etapa 17A.1 (Objetivo D) — tolerância a cerca de código Markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fenced_json_source_analysis_output_succeeds():
    c1 = raw_claim("A receita cresceu 12%.", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])
    payload = (
        '```json\n{"claim_relations": [{"claim_id": "'
        + c1.id
        + '", "relation": "supports", "excerpt": "a receita cresceu 12% em 2025"}]}\n```'
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    analyzer = SourceAnalyzer({"anthropic": provider})

    result = await analyzer.analyze(dr, _run_config())

    assert result.attempts[0].parse_status == "accepted"
    assert len(result.claim_results) == 1
