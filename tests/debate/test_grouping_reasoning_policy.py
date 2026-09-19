"""
claim_grouping_v2 -- R1 grouping latency repair (`minimal_reasoning=True`).

Cobre a POLÍTICA de request/proveniência do agrupamento intra-round, e
prova que nada além dela mudou (reconciliação, extração, Judge, transporte,
orçamento de saída, validação de cobertura, contabilidade, superfície
pública). Nenhuma chamada de rede/provider aqui.

Evidência que motivou a mudança (replay exato do request R1 persistido da run
2bd3b8b4, sob claim_grouping_v1): 36 claims, `max_tokens=8192`,
`minimal_reasoning=False` -> 81,479 s, `finish_reason=max_tokens`, 5.059 tokens
de entrada, 8.192 de saída dos quais 6.284 de raciocínio não visível, JSON
truncado com 30/36 ids. O teste `local_evidence` abaixo reconstrói esse
request a partir do banco local (quando existe) e verifica os dois digests.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.debate.claim_extraction import (
    CLAIM_EXTRACTION_CONTRACT_VERSION,
    CLAIM_GROUPING_CONTRACT_VERSION,
    CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION,
    _build_extraction_request,
    _build_grouping_request,
    _build_reconciliation_request,
    group_claims,
    reconcile_claims,
)
from app.judge.context import JUDGE_CONTRACT_VERSION, build_judge_request
from app.models.provider_models import CompletionRequest, Message
from app.models.request_provenance import (
    REQUEST_DIGEST_PREFIX,
    RequestProvenance,
    build_request_provenance,
    compute_request_digest,
)
from app.orchestrator.config import RunConfig
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.pricing import PricingRegistry
from tests.council.fixtures import run_config as _run_config
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response
from tests.judge.fixtures import debate_result, model_response, raw_claim

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- valores históricos/novos (fixture de 2 claims dos goldens de contrato) ---
V1_GOLDEN_DIGEST = (
    "completion-request-sha256-v2:35b8b9aff06e4ca98caebb6c9a6dca4a8d1e04b5837d5b0ce2f4b70775d65bc7"
)
V2_GOLDEN_DIGEST = (
    "completion-request-sha256-v2:662adfe838102f9fc581b106c71a902f1ec205e2007f85579910db093f9bd73a"
)
# --- workload histórico real de 36 claims (run 2bd3b8b4, R1 grouping) ---
HISTORICAL_V1_DIGEST = (
    "completion-request-sha256-v2:197a843192b5849d4c46ceb2f8492f8d2fd22d69fe55496a1bc87fd2970b5906"
)
EXPECTED_NEW_POLICY_DIGEST = (
    "completion-request-sha256-v2:b9f2f0451bd33fe6943ed6c32bc6053497e9d94bff997f8335914d2f4ed2df1a"
)
LIVE_RUN_ID = "2bd3b8b4-7916-4563-b3d1-38362cfbe69d"


def _fixed_claims():
    return [
        raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-1"),
        raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-2"),
    ]


# ---------------------------------------------------------------------------
# 1. Política do request de agrupamento
# ---------------------------------------------------------------------------


def test_grouping_request_sets_minimal_reasoning_true():
    request = _build_grouping_request(_fixed_claims(), 8192)

    assert request.minimal_reasoning is True


def test_grouping_request_keeps_output_budget_temperature_and_model_unchanged():
    request = _build_grouping_request(_fixed_claims(), 8192)

    assert request.max_tokens == 8192  # o teto recebido do chamador, nunca alterado aqui
    assert request.temperature is None
    assert request.model is None
    assert [m.role for m in request.messages] == ["user"]


def test_grouping_contract_version_advanced_to_v2():
    assert CLAIM_GROUPING_CONTRACT_VERSION == "claim_grouping_v2"


def test_only_minimal_reasoning_differs_from_claim_grouping_v1():
    """Prova AUTOSSUFICIENTE (sem banco) de que prompt/mensagens/model/
    max_tokens/temperature são BYTE-IDÊNTICOS ao v1: o MESMO request com
    `minimal_reasoning=False` reproduz EXATAMENTE o golden v1 histórico
    (`35b8b9af...`), e só com o flag True o digest passa a ser o golden v2."""
    request = _build_grouping_request(_fixed_claims(), 1024)

    v1_equivalent = request.model_copy(update={"minimal_reasoning": False})

    assert compute_request_digest(v1_equivalent) == V1_GOLDEN_DIGEST
    assert compute_request_digest(request) == V2_GOLDEN_DIGEST
    assert compute_request_digest(request) != compute_request_digest(v1_equivalent)
    # os demais campos semânticos são literalmente iguais
    assert request.model_copy(update={"minimal_reasoning": False}) == v1_equivalent
    assert request.messages == v1_equivalent.messages
    assert request.system_prompt == v1_equivalent.system_prompt


def test_digest_format_is_unchanged_completion_request_sha256_v2():
    digest = compute_request_digest(_build_grouping_request(_fixed_claims(), 8192))

    assert digest.startswith("completion-request-sha256-v2:")
    assert digest.startswith(REQUEST_DIGEST_PREFIX)
    assert re.fullmatch(r"[0-9a-f]{64}", digest.removeprefix(REQUEST_DIGEST_PREFIX))


@pytest.mark.skipif(
    not (REPO_ROOT / "llm_council.db").exists(),
    reason="evidência local: banco com a run 2bd3b8b4 não existe neste checkout",
)
@pytest.mark.asyncio
async def test_local_evidence_reconstructed_36_claim_workload_digests():
    """Reconstrói o workload R1 REAL (36 claims, mesma ordem de produção) a
    partir do banco persistido (somente leitura, nenhum provider) e verifica:
    novo digest == `b9f2f045...`; MESMO request com `minimal_reasoning=False`
    == digest histórico persistido `197a8431...` (prova independente de que só
    a política mudou); e que a proveniência histórica persistida
    (`claim_grouping_v1`) NÃO foi reescrita."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.storage.repository import CouncilRepository

    db = REPO_ROOT / "llm_council.db"
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = ro.execute(
        "select request_provenance_json from claim_processing_attempts "
        "where council_run_id=? and operation='grouping' and round_number=1",
        (LIVE_RUN_ID,),
    ).fetchone()
    if row is None:
        pytest.skip("evidência local: run 2bd3b8b4 ausente do banco deste checkout")
    persisted = json.loads(row[0])

    engine = create_async_engine(f"sqlite+aiosqlite:///file:{db}?mode=ro&uri=true")
    try:
        repo = CouncilRepository(async_sessionmaker(engine, expire_on_commit=False))
        record = await repo.get_run(LIVE_RUN_ID)
    finally:
        await engine.dispose()
    result = record.council_run_result
    raw_r1 = [
        c
        for c in result.debate_result.claims
        if c.round_introduced == 1 and c.parent_claim_id is None and not c.merged_from_claim_ids
    ]
    assert len(raw_r1) == 36
    request = _build_grouping_request(raw_r1, result.run_config.max_output_tokens_grouping)

    assert request.max_tokens == 8192
    assert compute_request_digest(request) == EXPECTED_NEW_POLICY_DIGEST
    assert (
        compute_request_digest(request.model_copy(update={"minimal_reasoning": False}))
        == HISTORICAL_V1_DIGEST
    )
    # histórico intocado: continua v1 + digest antigo, legível como RequestProvenance
    assert persisted == {
        "contract_version": "claim_grouping_v1",
        "request_digest": HISTORICAL_V1_DIGEST,
    }
    assert RequestProvenance(**persisted).contract_version == "claim_grouping_v1"


# ---------------------------------------------------------------------------
# 2. Anthropic: mapeamento explícito pra thinking desabilitado (só agrupamento)
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeUsage:
    input_tokens = 8
    output_tokens = 4


class _FakeResponse:
    def __init__(self):
        self.content = [_FakeTextBlock('{"groups": [], "ungrouped_claim_ids": []}')]
        self.usage = _FakeUsage()
        self.model = "claude-test"
        self.stop_reason = "end_turn"


def _anthropic() -> AnthropicProvider:
    provider = AnthropicProvider(
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        default_model="claude-test",
        pricing=PricingRegistry({}),
    )
    provider._client = AsyncMock()
    provider._client.messages.create = AsyncMock(return_value=_FakeResponse())
    return provider


@pytest.mark.asyncio
async def test_anthropic_maps_grouping_request_to_explicit_disabled_thinking():
    provider = _anthropic()

    await provider.complete(_build_grouping_request(_fixed_claims(), 8192))

    kwargs = provider._client.messages.create.call_args.kwargs
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_anthropic_never_sends_thinking_for_the_reconciliation_request():
    provider = _anthropic()
    round1 = [raw_claim("A", "resp-1", provider="openai", id="r1-a")]
    round2 = [raw_claim("B", "resp-2", provider="anthropic", id="r2-b", round_introduced=2)]

    await provider.complete(_build_reconciliation_request(round1, round2, 8192))

    kwargs = provider._client.messages.create.call_args.kwargs
    assert "thinking" not in kwargs


# ---------------------------------------------------------------------------
# 3. Não-regressão: reconciliação, extração, Judge
# ---------------------------------------------------------------------------


def test_reconciliation_request_and_contract_are_unchanged():
    round1 = [raw_claim("A", "resp-1", provider="openai", id="r1-a")]
    round2 = [raw_claim("B", "resp-2", provider="anthropic", id="r2-b", round_introduced=2)]

    request = _build_reconciliation_request(round1, round2, 8192)

    assert request.minimal_reasoning is False
    assert request.max_tokens == 8192
    assert CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION == "cross_round_claim_reconciliation_v1"
    assert CLAIM_GROUPING_CONTRACT_VERSION != CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION


def test_extraction_request_and_contract_are_unchanged():
    response = model_response("openai", id="mr-fixed-1", response_text="Brasília é a capital.")

    request = _build_extraction_request(response, None, 4096)

    assert request.minimal_reasoning is True
    assert request.max_tokens == 4096
    assert CLAIM_EXTRACTION_CONTRACT_VERSION == "claim_extraction_v2"


def test_judge_request_and_contract_are_unchanged():
    c1 = raw_claim("A", "resp-1", provider="openai")
    dr = debate_result([c1], [model_response("openai")])

    request = build_judge_request("Pergunta?", dr, [c1], 8192)

    assert request.minimal_reasoning is False
    assert request.max_tokens == 8192
    assert JUDGE_CONTRACT_VERSION == "judge_v1"


# ---------------------------------------------------------------------------
# 4. group_claims: proveniência v2, transporte default, cobertura de ids,
#    truncamento fail-closed, contabilidade
# ---------------------------------------------------------------------------


async def _group(provider, claims, *, max_tokens=8192):
    return await group_claims(
        claims,
        round_number=1,
        grouper=provider,
        max_output_tokens_per_call=max_tokens,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )


def _two_claims():
    return [
        raw_claim("claim a", "resp-a", provider="openai"),
        raw_claim("claim b", "resp-b", provider="anthropic"),
    ]


@pytest.mark.asyncio
async def test_grouping_attempts_record_the_v2_provenance_of_the_exact_request_sent():
    a, b = _two_claims()
    payload = json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    _canonical, attempts = await _group(provider, [a, b])

    sent = provider.received_requests[0]
    assert sent.minimal_reasoning is True
    assert sent.max_tokens == 8192
    assert attempts[0].request_provenance == build_request_provenance("claim_grouping_v2", sent)
    assert attempts[0].request_provenance.contract_version == "claim_grouping_v2"


@pytest.mark.asyncio
async def test_grouping_transport_stays_the_provider_default_policy():
    """Agrupamento chama `complete(request)` SEM override de política de
    transporte -> continua 60 s / 3 tentativas (default do provider)."""
    a, b = _two_claims()
    payload = json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    await _group(provider, [a, b])

    assert provider.received_execution_policies == [None]


@pytest.mark.asyncio
async def test_reconciliation_transport_stays_the_provider_default_policy():
    round1 = [raw_claim("A", "resp-1", provider="openai")]
    round2 = [raw_claim("B", "resp-2", provider="anthropic", round_introduced=2)]
    payload = json.dumps(
        {"groups": [], "ungrouped_claim_ids": [round1[0].id, round2[0].id]}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    await reconcile_claims(
        round1,
        round2,
        reconciler=provider,
        max_output_tokens_per_call=8192,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
        total_models_in_round=3,
        support_scope_model_count=3,
    )

    sent = provider.received_requests[0]
    assert sent.minimal_reasoning is False
    assert provider.received_execution_policies == [None]


@pytest.mark.asyncio
async def test_complete_id_coverage_remains_mandatory_under_v2():
    a, b = _two_claims()
    only_a = json.dumps({"groups": [], "ungrouped_claim_ids": [a.id]})  # esquece b
    provider = ScriptedProvider(
        "anthropic",
        [text_response("anthropic", only_a), text_response("anthropic", only_a)],
    )

    canonical, attempts = await _group(provider, [a, b])

    assert canonical == []  # fail-closed: nenhuma fusão
    assert [x.parse_status for x in attempts] == ["inconsistent_references"] * 2


@pytest.mark.asyncio
async def test_truncated_grouping_output_from_the_live_replay_shape_fails_closed_without_retry():
    """Forma REAL do replay (30/36 ids emitidos, cortado no meio de uma
    string, `max_tokens`): rejeitado como malformado, sem retry (truncamento
    conhecido), nenhuma claim fundida -- e a contabilidade do uso
    CONHECIDO da resposta truncada é preservada, nunca zerada/omitida."""
    claims = [raw_claim(f"claim {i}", f"resp-{i}", provider="openai") for i in range(6)]
    emitted = ", ".join(f'"{c.id}"' for c in claims[:5])
    truncated = '{"groups": [\n  {"member_claim_ids": [' + emitted + '], "canonical_text": "texto cort'
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response(
                "anthropic",
                truncated,
                input_tokens=5059,
                output_tokens=8192,
                cost_usd=0.092038,
                provider_finish_reason="max_tokens",
            )
        ],
    )

    canonical, attempts = await _group(provider, claims)

    assert canonical == []
    assert len(attempts) == 1  # sem retry -- truncamento confirmado
    assert attempts[0].parse_status == "malformed"
    assert attempts[0].provider_finish_reason == "max_tokens"
    assert attempts[0].usage.input_tokens == 5059
    assert attempts[0].usage.output_tokens == 8192
    assert attempts[0].cost_usd == pytest.approx(0.092038)
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_transport_failure_accounting_stays_unknown_not_zero():
    a, b = _two_claims()
    provider = ScriptedProvider(
        "anthropic", [transport_error_response("anthropic", attempts=3)]
    )

    canonical, attempts = await _group(provider, [a, b])

    assert canonical == []
    assert attempts[0].transport_status == "error"
    assert attempts[0].usage is None
    assert attempts[0].cost_usd is None
    assert attempts[0].transport_attempts == 3  # propagado verbatim da resposta do provider


# ---------------------------------------------------------------------------
# 5. Nenhuma superfície pública nova, nenhum código provider-específico
# ---------------------------------------------------------------------------


def test_no_public_or_configuration_knob_exposes_the_reasoning_policy():
    from app.config import Settings
    from app.presentation.schemas import CreateRunRequest

    for model in (Settings, RunConfig, CreateRunRequest):
        assert not any(
            "reasoning" in name or "thinking" in name for name in model.model_fields
        ), model.__name__
    assert set(CreateRunRequest.model_fields) == {"question", "enabled_providers", "source_text"}

    for rel in ("app/api", "app/cli", "app/presentation", "frontend/src/api/types.ts"):
        target = REPO_ROOT / rel
        files = [target] if target.is_file() else [p for p in target.rglob("*.py")]
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert "minimal_reasoning" not in text, path
            assert "thinking" not in text.lower(), path


def test_grouping_module_stays_provider_agnostic():
    """`claim_extraction.py` só liga o flag provider-NEUTRO
    (`minimal_reasoning`); o mapeamento pra `thinking` mora exclusivamente
    no adapter Anthropic. Nenhum import/identificador/chave de dict
    provider-específico foi introduzido no módulo de agrupamento."""
    tree = ast.parse((REPO_ROOT / "app/debate/claim_extraction.py").read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not any("anthropic" in n.lower() for n in names)
        if isinstance(node, ast.Dict):
            for key in node.keys:
                assert not (isinstance(key, ast.Constant) and key.value == "thinking")
        if isinstance(node, ast.Name):
            assert "anthropic" not in node.id.lower()
        if isinstance(node, ast.keyword):
            assert node.arg != "thinking"


# ---------------------------------------------------------------------------
# 6. Proveniência histórica: v1 continua legível e inalterada
# ---------------------------------------------------------------------------


def test_historical_claim_grouping_v1_provenance_remains_readable_and_unchanged():
    historical = RequestProvenance(
        contract_version="claim_grouping_v1", request_digest=HISTORICAL_V1_DIGEST
    )
    current = build_request_provenance(
        CLAIM_GROUPING_CONTRACT_VERSION, _build_grouping_request(_fixed_claims(), 8192)
    )

    assert historical.contract_version == "claim_grouping_v1"
    assert historical.request_digest == HISTORICAL_V1_DIGEST
    assert current.contract_version == "claim_grouping_v2"
    assert historical.contract_version != current.contract_version


def test_completion_request_schema_is_unchanged():
    assert set(CompletionRequest.model_fields) == {
        "messages",
        "system_prompt",
        "model",
        "max_tokens",
        "temperature",
        "minimal_reasoning",
    }
    assert Message.model_fields.keys() == {"role", "content"}
