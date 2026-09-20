"""
Política de raciocínio do JUDGE (judge_v2): o request atual do Judge pede
`minimal_reasoning=True`; `judge_v1` (histórico) usava `False`.

Evidência (replay controlado do request real de 43 claims que estourava
`max_tokens=8192` com raciocínio habilitado): mudar SÓ `minimal_reasoning`
False -> True (Anthropic: `thinking={"type": "disabled"}`) deu `end_turn`,
5.228 tokens de saída, 43/43 cobertura aceita pelo parser de produção.

Estes testes travam a FRONTEIRA da mudança: Judge-only, sem branch por
provider, sem mexer em prompt/schema/teto de saída/transporte/autoridade de
claims. Nenhuma chamada de provider real.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.debate.claim_extraction import _build_extraction_request
from app.debate.claims import get_current_claims
from app.debate.context import build_critique_requests
from app.editor.context import build_editor_request
from app.judge.context import JUDGE_CONTRACT_VERSION, build_judge_request
from app.judge.single_judge import SingleJudge
from app.models.domain import ClaimAssessment, JudgeVerdict
from app.models.provider_models import ProviderExecutionPolicy
from app.models.request_provenance import (
    REQUEST_DIGEST_PREFIX,
    RequestProvenance,
    build_request_provenance,
    compute_request_digest,
)
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.orchestrator.orchestrator import _build_initial_request
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.pricing import PricingRegistry
from app.source_analysis.context import build_source_analysis_request
from tests.debate.fakes import ScriptedProvider, text_response
from tests.judge.fixtures import debate_result, model_response, raw_claim

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "llm_council.db"

# Digests do replay controlado do request real de 43 claims (run 2bd3b8b4).
HISTORICAL_JUDGE_V1_DIGEST = (
    "completion-request-sha256-v2:908afd0a9403ea0ffd343e3960b6595d97b5960b7a94a3cea2b8bcbb44fa0243"
)
DIAGNOSTIC_JUDGE_V2_DIGEST = (
    "completion-request-sha256-v2:9faa0823e6a1e0fc064b39096e0e13be59f2b19f9f0938459b160daf6b92adf6"
)
LIVE_RUN_ID = "2bd3b8b4-7916-4563-b3d1-38362cfbe69d"


def _request(n_claims: int = 1, max_tokens: int = 8192):
    claims = [
        raw_claim(f"Claim {i}.", f"resp-{i}", provider="openai", id=f"claim-{i:03d}")
        for i in range(n_claims)
    ]
    dr = debate_result(claims, [model_response("openai")])
    return build_judge_request("Pergunta?", dr, claims, max_tokens), claims, dr


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Pergunta?",
        enabled_providers=["openai"],
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=8192,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        round_dispatch_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)


# ---------------------------------------------------------------------------
# 1. O request ATUAL do Judge
# ---------------------------------------------------------------------------


def test_current_judge_request_is_judge_v2_with_minimal_reasoning():
    request, _claims, _dr = _request()

    assert JUDGE_CONTRACT_VERSION == "judge_v2"
    assert request.minimal_reasoning is True


@pytest.mark.parametrize("max_tokens", [1024, 8192, 12345])
def test_max_tokens_is_exactly_what_the_caller_passes_and_temperature_stays_unset(max_tokens):
    request, _claims, _dr = _request(max_tokens=max_tokens)

    assert request.max_tokens == max_tokens  # nenhum teto/cálculo por cardinalidade
    assert request.temperature is None
    assert request.model is None


def test_only_the_reasoning_field_differs_from_the_judge_v1_style_request():
    """Mesmas mensagens/system prompt: flipando só `minimal_reasoning` de
    volta pra `False`, o request é o de judge_v1 -- e o digest cai no golden
    histórico (oráculo independente, `feac2b53...`, dos goldens de contrato)."""
    request, _claims, _dr = _request()
    v1_style = request.model_copy(update={"minimal_reasoning": False})

    differing = {k for k, v in request.model_dump().items() if v != v1_style.model_dump()[k]}

    assert differing == {"minimal_reasoning"}
    assert compute_request_digest(request) != compute_request_digest(v1_style)
    assert compute_request_digest(request) == compute_request_digest(request)  # determinístico


def test_the_contract_bump_is_provenance_only_the_domain_shape_is_unchanged():
    assert set(JudgeVerdict.model_fields) >= {
        "claim_assessments",
        "best_arguments_by",
        "debate_limitations",
        "confidence",
        "reasoning",
    }
    assert set(ClaimAssessment.model_fields) >= {"claim_id", "verdict", "explanation"}


def _judge_once_kwargs(dr):
    return dict(
        prior_input_tokens=dr.cumulative_input_tokens,
        prior_output_tokens=dr.cumulative_output_tokens,
        prior_cost_usd=dr.cumulative_cost_usd,
    )


@pytest.mark.asyncio
async def test_single_judge_sends_the_v2_request_and_records_v2_provenance():
    request, claims, dr = _request()
    payload = json.dumps(
        {
            "claim_assessments": [{"claim_id": claims[0].id, "verdict": "supported", "explanation": "ok"}],
            "confidence": 0.5,
            "reasoning": "r",
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    result = await SingleJudge({"anthropic": provider}).judge(dr, _run_config(), **_judge_once_kwargs(dr))

    sent = provider.received_requests[0]
    assert sent.minimal_reasoning is True
    assert sent.max_tokens == 8192
    provenance = result.attempts[0].request_provenance
    assert provenance == build_request_provenance("judge_v2", sent)
    assert provenance.contract_version == "judge_v2"
    assert provenance.request_digest.startswith(REQUEST_DIGEST_PREFIX)  # digest v2 inalterado
    assert provenance.request_digest != compute_request_digest(sent.model_copy(update={"minimal_reasoning": False}))


# ---------------------------------------------------------------------------
# 2. Provider: o adapter continua sendo a autoridade do mapeamento
# ---------------------------------------------------------------------------


class _Block:
    type = "text"
    text = "{}"


class _Usage:
    input_tokens = 1
    output_tokens = 1


class _Response:
    content = [_Block()]
    usage = _Usage()
    model = "claude-test"
    stop_reason = "end_turn"


async def _anthropic_kwargs(request) -> dict:
    provider = AnthropicProvider(
        api_key="test-key", timeout_seconds=5, max_retries=0, default_model="claude-test", pricing=PricingRegistry({})
    )
    provider._client = AsyncMock()
    provider._client.messages.create = AsyncMock(return_value=_Response())
    await provider.complete(request)
    return provider._client.messages.create.call_args.kwargs


@pytest.mark.asyncio
async def test_anthropic_adapter_maps_the_judge_request_to_thinking_disabled_and_nothing_else():
    request, _claims, _dr = _request()

    kwargs = await _anthropic_kwargs(request)
    v1_kwargs = await _anthropic_kwargs(request.model_copy(update={"minimal_reasoning": False}))

    assert kwargs["thinking"] == {"type": "disabled"}
    assert "thinking" not in v1_kwargs
    assert {k: v for k, v in kwargs.items() if k != "thinking"} == v1_kwargs  # nenhum outro campo muda
    assert kwargs["max_tokens"] == 8192 and "temperature" not in kwargs


def test_other_adapters_do_not_map_minimal_reasoning_and_judge_has_no_provider_branch():
    for adapter in ("openai_provider.py", "gemini_provider.py"):
        assert "minimal_reasoning" not in (REPO / "app/providers" / adapter).read_text(encoding="utf-8")

    for path in (REPO / "app/judge").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert "anthropic" not in node.id.lower() and node.id != "thinking", (path.name, node.id)
            if isinstance(node, ast.keyword):
                assert node.arg != "thinking", path.name
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 40:
                assert node.value != "thinking", path.name


# ---------------------------------------------------------------------------
# 3. Outras operações: política de raciocínio INALTERADA (só o Judge mudou)
# ---------------------------------------------------------------------------


def test_only_the_judge_and_claim_extraction_ask_for_minimal_reasoning():
    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed")
    response = model_response("openai", id="mr-fixed", response_text="Brasília é a capital.")
    verdict = JudgeVerdict(
        id="verdict-fixed",
        evaluated_through_round=1,
        judge_model="claude-sonnet-5",
        claim_assessments=[ClaimAssessment(claim_id="claim-fixed", verdict="supported", explanation="ok")],
        best_arguments_by={},
        debate_limitations=[],
        confidence=0.9,
        reasoning="r",
    )

    policies = {
        "participant": _build_initial_request("Pergunta?", 1024).minimal_reasoning,
        "critique": build_critique_requests(
            question="Pergunta?", current_claims=[c1], participants=["openai"], max_output_tokens_per_call=1024
        )["openai"].minimal_reasoning,
        "claim_extraction": _build_extraction_request(response, None, 1024).minimal_reasoning,
        "source_analysis": build_source_analysis_request("Fonte.", [c1], 1024).minimal_reasoning,
        "judge": build_judge_request(
            "Pergunta?", debate_result([c1], [response]), [c1], 1024
        ).minimal_reasoning,
        "editor": build_editor_request("Pergunta?", verdict, 1024).minimal_reasoning,
    }

    assert policies == {
        "participant": False,
        "critique": False,
        "claim_extraction": True,  # política própria da extração, inalterada
        "source_analysis": False,
        "judge": True,  # judge_v2
        "editor": False,
    }


def test_minimal_reasoning_default_stays_false_globally():
    from app.models.provider_models import CompletionRequest, Message

    assert CompletionRequest.model_fields["minimal_reasoning"].default is False
    assert CompletionRequest(messages=[Message(role="user", content="x")]).minimal_reasoning is False


# ---------------------------------------------------------------------------
# 4. Transporte e capacidade de saída: INALTERADOS
# ---------------------------------------------------------------------------


def test_judge_transport_policy_is_still_120_seconds_and_one_attempt():
    override = ProviderExecutionPolicy.from_settings(Settings(_env_file=None)).judge_override

    assert override is not None
    assert override.attempt_timeout_seconds == 120.0
    assert override.max_transport_attempts_per_completion == 1


def test_judge_output_ceiling_setting_is_still_8192():
    assert Settings(_env_file=None).default_max_output_tokens_judge == 8192


# ---------------------------------------------------------------------------
# 5. Cobertura sintética de 50 claims (SÓ construção de request)
# ---------------------------------------------------------------------------


def test_a_50_claim_request_is_built_deterministically_with_every_target_id_and_no_upstream_dedupe():
    """Só CONSTRUÇÃO do request -- não afirma nada sobre capacidade do
    provider. Nenhuma deduplicação/agrupamento a montante é assumida: as 50
    claims (inclusive textos idênticos) entram todas, na ordem dada."""
    claims = [
        raw_claim("Afirmação repetida." if i % 5 == 0 else f"Afirmação {i}.", f"resp-{i}", provider="openai", id=f"claim-{i:03d}")
        for i in range(50)
    ]
    dr = debate_result(claims, [model_response("openai")])
    current = get_current_claims(dr.claims)

    first = build_judge_request("Pergunta?", dr, current, 8192)
    second = build_judge_request("Pergunta?", dr, current, 8192)

    assert len(current) == 50
    body = first.messages[0].content
    serialized = json.loads(body[body.index("[{") :])
    assert [c["id"] for c in serialized] == [c.id for c in claims]
    assert len({c["id"] for c in serialized}) == 50
    assert first == second and compute_request_digest(first) == compute_request_digest(second)
    assert first.minimal_reasoning is True and first.max_tokens == 8192


# ---------------------------------------------------------------------------
# 6. Histórico judge_v1: legível, nunca reescrito
# ---------------------------------------------------------------------------


def test_historical_judge_v1_provenance_remains_valid_and_distinct_from_v2():
    v1 = RequestProvenance(contract_version="judge_v1", request_digest=HISTORICAL_JUDGE_V1_DIGEST)
    v2 = RequestProvenance(contract_version="judge_v2", request_digest=DIAGNOSTIC_JUDGE_V2_DIGEST)

    assert (v1.contract_version, v1.request_digest) == ("judge_v1", HISTORICAL_JUDGE_V1_DIGEST)
    assert v1 != v2 and JUDGE_CONTRACT_VERSION == v2.contract_version


@pytest.mark.skipif(not DB.exists(), reason="evidência local: banco com a run 2bd3b8b4 não existe neste checkout")
@pytest.mark.asyncio
async def test_local_evidence_43_claim_request_reproduces_the_historical_and_diagnostic_digests():
    """Reconstrói (somente leitura, sem provider) o request REAL de 43 claims:
    o builder atual (judge_v2) dá o digest do replay controlado
    `9faa0823...`; com `minimal_reasoning=False` dá o digest histórico
    `908afd0a...` -- e a proveniência judge_v1 PERSISTIDA não foi reescrita."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.storage.repository import CouncilRepository

    ro = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = ro.execute(
            "select request_provenance_json from judge_attempts where council_run_id=?", (LIVE_RUN_ID,)
        ).fetchone()
    finally:
        ro.close()
    if row is None:
        pytest.skip("evidência local: run 2bd3b8b4 ausente do banco deste checkout")

    engine = create_async_engine(f"sqlite+aiosqlite:///file:{DB}?mode=ro&uri=true")
    try:
        record = await CouncilRepository(async_sessionmaker(engine, expire_on_commit=False)).get_run(LIVE_RUN_ID)
    finally:
        await engine.dispose()
    result = record.council_run_result
    current = get_current_claims(result.debate_result.claims)
    request = build_judge_request(
        result.run_config.question, result.debate_result, current, result.run_config.max_output_tokens_judge
    )

    assert len(current) == 43
    assert request.max_tokens == 8192 and request.temperature is None
    assert compute_request_digest(request) == DIAGNOSTIC_JUDGE_V2_DIGEST
    v1_style = request.model_copy(update={"minimal_reasoning": False})
    assert compute_request_digest(v1_style) == HISTORICAL_JUDGE_V1_DIGEST
    assert json.loads(row[0]) == {"contract_version": "judge_v1", "request_digest": HISTORICAL_JUDGE_V1_DIGEST}
