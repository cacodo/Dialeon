"""
claim_grouping_v4 -- CONTENÇÃO DE AUTORIDADE.

PRINCÍPIO: agrupamento PROPÕE relações; NÃO reescreve claims. Um resultado
de agrupamento (mesmo com clusters de 2+ ids) é só metadado consultivo e
auditável (a resposta bruta fica no `ClaimProcessingAttempt`). Ele NÃO
cria claim canônica, NÃO sintetiza texto, NÃO une suporte, NÃO supersede nem
remove nenhuma claim original, NÃO altera linhagem e NÃO muda o que
crítica/reconciliação/Source Analysis/Judge veem.

Estes testes são sobre CONTENÇÃO DE AUTORIDADE -- nunca aprovam a fusão
semântica que um modelo eventualmente proponha (o caso "M11" abaixo é uma
falsa fusão de proposições distintas, e mesmo assim nenhuma claim some nem
herda o suporte da outra).
"""

from __future__ import annotations

import ast
import inspect
import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.debate import claim_extraction as ce
from app.debate.claim_extraction import group_claims
from app.debate.claims import get_current_claims
from app.debate.debate_engine import DebateEngine
from app.debate.processing_record import ClaimProcessingAttempt
from app.editor.attempt import EditorAttempt
from app.judge.attempt import JudgeAttempt
from app.judge.context import build_judge_request
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType
from app.source_analysis.attempt import SourceAnalysisAttempt
from tests.council.fixtures import run_config as _grouping_run_config
from tests.debate.fakes import CallableProvider, ScriptedProvider, text_response
from tests.debate.test_debate_engine_reconciliation import (
    _extraction,
    _known_claims_payload,
    _ok,
    _participant_handler,
    _run_config,
)
from tests.judge.fixtures import raw_claim

REPO = Path(__file__).resolve().parents[2]

# M11 do replay v3: duas proposições DISTINTAS (imagem-espelho), de modelos diferentes.
CLAIM_MAINTENANCE = "O SaaS reduz a carga de manutenção."
CLAIM_PREDICTABLE = "O SaaS tem custo previsível por assinatura."


# ---------------------------------------------------------------------------
# 1. group_claims: nada é criado/unido/supersedido
# ---------------------------------------------------------------------------


async def _group(claims, payload):
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    attempts = await group_claims(
        claims,
        round_number=1,
        grouper=provider,
        max_output_tokens_per_call=8192,
        run_config=_grouping_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )
    return attempts, provider


@pytest.mark.asyncio
async def test_multi_member_cluster_changes_nothing_about_the_original_claims():
    a = raw_claim(CLAIM_MAINTENANCE, "resp-a", provider="openai")
    b = raw_claim(CLAIM_PREDICTABLE, "resp-b", provider="gemini")
    c = raw_claim("Um terceiro ponto.", "resp-c", provider="anthropic")
    originals = [x.model_dump() for x in (a, b, c)]

    attempts, _p = await _group([a, b, c], json.dumps({"clusters": [[a.id, b.id], [c.id]]}))

    assert [x.parse_status for x in attempts] == ["accepted"]
    # texto, suporte, linhagem, ids: byte a byte iguais
    assert [x.model_dump() for x in (a, b, c)] == originals
    assert a.text == CLAIM_MAINTENANCE and b.text == CLAIM_PREDICTABLE
    assert [s.provider for s in a.supporting_model_response_ids] == ["openai"]
    assert [s.provider for s in b.supporting_model_response_ids] == ["gemini"]
    for claim in (a, b, c):
        assert claim.parent_claim_id is None
        assert claim.merged_from_claim_ids == []
        assert claim.superseded_by is None
    # todas seguem atuais (nenhuma referenciada por linhagem nova)
    assert {x.id for x in get_current_claims([a, b, c])} == {a.id, b.id, c.id}


@pytest.mark.asyncio
async def test_m11_shaped_false_merge_cannot_make_a_claim_disappear_or_inherit_support():
    """Regressão de AUTORIDADE (não aprova o merge semântico): mesmo que o
    modelo ponha duas proposições materialmente diferentes no MESMO cluster,
    nenhuma das duas some nem herda o suporte da outra."""
    maintenance = raw_claim(CLAIM_MAINTENANCE, "resp-1", provider="anthropic")
    predictable = raw_claim(CLAIM_PREDICTABLE, "resp-2", provider="gemini")

    attempts, _p = await _group(
        [maintenance, predictable],
        json.dumps({"clusters": [[maintenance.id, predictable.id]]}),  # cluster único agressivo
    )

    current = get_current_claims([maintenance, predictable])
    assert {c.id for c in current} == {maintenance.id, predictable.id}  # nenhuma sumiu
    assert {s.provider for s in maintenance.supporting_model_response_ids} == {"anthropic"}
    assert {s.provider for s in predictable.supporting_model_response_ids} == {"gemini"}
    # a proposta continua AUDITÁVEL na resposta bruta, sem virar autoridade
    proposed = json.loads(attempts[0].raw_output_text)["clusters"]
    assert proposed == [[maintenance.id, predictable.id]]


@pytest.mark.asyncio
async def test_group_claims_return_value_has_no_claims_to_apply():
    a = raw_claim("a", "resp-a", provider="openai")
    b = raw_claim("b", "resp-b", provider="anthropic")

    result, _p = await _group([a, b], json.dumps({"clusters": [[a.id, b.id]]}))

    assert isinstance(result, list)
    assert all(isinstance(x, ClaimProcessingAttempt) for x in result)  # SÓ tentativas


# ---------------------------------------------------------------------------
# 2. Estrutural: nenhum caminho alcançável de group_claims constrói claim canônica
# ---------------------------------------------------------------------------


def _reachable_function_names(module_path: Path, start: str) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    functions = {
        n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    seen: set[str] = set()
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Name) and node.id in functions:
                stack.append(node.id)
    return seen


def test_no_canonical_claim_path_is_reachable_from_group_claims():
    module = REPO / "app/debate/claim_extraction.py"
    from_grouping = _reachable_function_names(module, "group_claims")
    from_reconciliation = _reachable_function_names(module, "reconcile_claims")

    assert "_run_structured_grouping_call" in from_grouping  # sanidade do grafo
    for forbidden in ("_build_canonical_claim", "_merge_supports", "_compute_status"):
        assert forbidden not in from_grouping, forbidden
    # reconciliação (fora do escopo desta slice) continua usando a construção canônica
    assert "_build_canonical_claim" in from_reconciliation


def test_debate_engine_round_processing_only_uses_raw_claims():
    source = inspect.getsource(DebateEngine._process_round)

    assert "canonical_claims" not in source
    assert "return raw_claims, attempts, verification_attempts" in source


def test_group_claims_source_never_touches_claim_authority_fields():
    source = inspect.getsource(group_claims)
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    tree = ast.parse(inspect.getsource(group_claims))
    attribute_names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    call_names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    assert not attribute_names & {"merged_from_claim_ids", "superseded_by", "supporting_model_response_ids", "parent_claim_id", "text"}
    assert not call_names & {"Claim", "_build_canonical_claim", "_merge_supports"}
    assert "model_copy" not in code


# ---------------------------------------------------------------------------
# 3. Downstream: pipeline completo (engine) com um cluster maximamente agressivo
# ---------------------------------------------------------------------------

R1 = {
    "resposta inicial openai": CLAIM_MAINTENANCE,
    "resposta inicial gemini": CLAIM_PREDICTABLE,
}


def _processor(cluster_style: str, captured: dict):
    """Processor único. `cluster_style`: "singletons" (nenhuma proposta de
    equivalência) ou "one_big_cluster" (TODAS as claims do request num único
    cluster -- o pior caso de falsa fusão)."""

    async def handler(call_index: int, request) -> object:
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            captured.setdefault("reconciliation_payloads", []).append(payload)
            ids = [c["id"] for c in payload]
            # reconciliação (fora do escopo): tudo ungrouped (formato próprio dela)
            return _ok("claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids}))
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            captured.setdefault("grouping_payloads", []).append(payload)
            ids = [c["id"] for c in payload]
            clusters = [ids] if cluster_style == "one_big_cluster" else [[i] for i in ids]
            return _ok("claude-processor", json.dumps({"clusters": clusters}))
        if "RESPOSTA_A_ANALISAR" in content:
            known = _known_claims_payload(content)
            if known:
                captured.setdefault("round2_known_claims", []).append(known)
            for marker, text in R1.items():
                if marker in content:
                    return _ok("claude-processor", _extraction(text))
            if "crítica openai" in content:
                return _ok("claude-processor", _extraction("Uma objeção nova da crítica."))
            return _ok("claude-processor", json.dumps({"claims": []}))
        raise AssertionError(f"chamada inesperada: {content[:200]}")

    return handler


async def _run_engine(cluster_style: str):
    captured: dict = {}
    providers = {
        "openai": CallableProvider(
            "openai", _participant_handler("openai", "resposta inicial openai", "crítica openai")
        ),
        "gemini": CallableProvider(
            "gemini", _participant_handler("gemini", "resposta inicial gemini", "crítica gemini")
        ),
        "claude-processor": CallableProvider("claude-processor", _processor(cluster_style, captured)),
    }
    result = await DebateEngine(providers).run(_run_config(["openai", "gemini"]))
    return result, captured


def _fingerprint(claims):
    """Conjunto de claims comparável entre execuções (ids mudam por execução)."""
    return sorted(
        (
            c.text,
            c.round_introduced,
            tuple(sorted(s.provider for s in c.supporting_model_response_ids)),
            c.parent_claim_id is not None,
            len(c.merged_from_claim_ids),
            c.superseded_by is not None,
            c.source_model_response_id is None,
        )
        for c in claims
    )


@pytest.mark.asyncio
async def test_engine_one_big_cluster_leaves_every_original_claim_current_and_untouched():
    result, captured = await _run_engine("one_big_cluster")

    grouping_attempts = [a for a in result.claim_processing_attempts if a.operation == "grouping"]
    assert grouping_attempts and all(a.parse_status == "accepted" for a in grouping_attempts)
    proposed = json.loads(grouping_attempts[0].raw_output_text)["clusters"]
    assert len(proposed) == 1 and len(proposed[0]) == 2  # a proposta agressiva existiu e ficou auditável

    by_text = {c.text: c for c in result.claims}
    maintenance, predictable = by_text[CLAIM_MAINTENANCE], by_text[CLAIM_PREDICTABLE]
    # nenhuma claim canônica (canônicas têm source_model_response_id=None)
    assert all(c.source_model_response_id is not None for c in result.claims)
    assert all(not c.merged_from_claim_ids for c in result.claims)
    # nenhuma sumiu, nenhuma herdou suporte
    current_texts = {c.text for c in get_current_claims(result.claims)}
    assert {CLAIM_MAINTENANCE, CLAIM_PREDICTABLE} <= current_texts
    assert {s.provider for s in maintenance.supporting_model_response_ids} == {"openai"}
    assert {s.provider for s in predictable.supporting_model_response_ids} == {"gemini"}


@pytest.mark.asyncio
async def test_engine_claim_set_is_identical_whatever_the_grouping_proposal():
    """O conjunto de claims do debate é IDÊNTICO (texto/suporte/linhagem/
    round) com um cluster único agressivo e com só singletons: o agrupamento
    não tem efeito algum sobre as claims."""
    singletons, _c1 = await _run_engine("singletons")
    aggressive, _c2 = await _run_engine("one_big_cluster")

    assert _fingerprint(singletons.claims) == _fingerprint(aggressive.claims)
    assert _fingerprint(get_current_claims(singletons.claims)) == _fingerprint(
        get_current_claims(aggressive.claims)
    )


@pytest.mark.asyncio
async def test_downstream_critique_reconciliation_and_judge_see_the_original_claims():
    result, captured = await _run_engine("one_big_cluster")

    # crítica (extração da rodada 2) recebeu as claims ORIGINAIS da rodada 1
    known_texts = {k["text"] for known in captured["round2_known_claims"] for k in known}
    assert {CLAIM_MAINTENANCE, CLAIM_PREDICTABLE} <= known_texts
    # reconciliação recebeu as originais do lado Round 1
    round1_side = {
        e["text"] for payload in captured["reconciliation_payloads"] for e in payload if e["round"] == 1
    }
    assert {CLAIM_MAINTENANCE, CLAIM_PREDICTABLE} <= round1_side
    # Judge recebe as claims atuais = as originais
    current = get_current_claims(result.claims)
    request = build_judge_request("Pergunta?", result, current, 8192)
    body = request.messages[0].content
    assert CLAIM_MAINTENANCE in body and CLAIM_PREDICTABLE in body


@pytest.mark.asyncio
async def test_grouping_payload_sent_to_the_model_is_the_raw_claims_verbatim():
    _result, captured = await _run_engine("singletons")

    sent_texts = {e["text"] for payload in captured["grouping_payloads"] for e in payload}
    assert {CLAIM_MAINTENANCE, CLAIM_PREDICTABLE} <= sent_texts


# ---------------------------------------------------------------------------
# 4. Auditoria / status: v4 nunca emite accepted_normalized; histórico legível
# ---------------------------------------------------------------------------


def test_v4_attempt_recorder_has_no_way_to_emit_accepted_normalized():
    assert "parse_status" not in inspect.signature(ce._accepted_attempt).parameters
    tree = ast.parse(inspect.getsource(ce._run_structured_grouping_call))
    fn = tree.body[0]
    body_nodes = fn.body[1:] if isinstance(fn.body[0], ast.Expr) else fn.body  # sem o docstring
    identifiers = {
        n.id if isinstance(n, ast.Name) else n.value
        for stmt in body_nodes
        for n in ast.walk(stmt)
        if isinstance(n, ast.Name) or (isinstance(n, ast.Constant) and isinstance(n.value, str))
    }
    assert "accepted_normalized" not in identifiers
    assert not any("normaliz" in str(i).lower() for i in identifiers)


@pytest.mark.asyncio
async def test_a_v3_style_singleton_group_response_is_rejected_not_normalized():
    a = raw_claim("a", "resp-a", provider="openai")
    b = raw_claim("b", "resp-b", provider="anthropic")
    v3_shape = json.dumps(
        {"groups": [{"member_claim_ids": [a.id], "canonical_text": "x"}], "ungrouped_claim_ids": [b.id]}
    )
    good = json.dumps({"clusters": [[a.id], [b.id]]})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", v3_shape), text_response("anthropic", good)])

    attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=8192,
        run_config=_grouping_run_config(), prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert [x.parse_status for x in attempts] == ["malformed", "accepted"]


def _attempt_kwargs(**overrides):
    fields = dict(
        operation="grouping",
        round_number=1,
        attempt_number=1,
        provider="anthropic",
        requested_model="m",
        model="m",
        target_claim_ids=["c1", "c2"],
        transport_status="success",
        transport_attempts=1,
        raw_output_text='{"groups": [{"member_claim_ids": ["c1"], "canonical_text": ""}]}',
        parse_status="accepted_normalized",
        latency_ms=1,
    )
    fields.update(overrides)
    return fields


def test_historical_v3_accepted_normalized_grouping_attempt_remains_readable():
    historical = ClaimProcessingAttempt(**_attempt_kwargs())

    assert historical.parse_status == "accepted_normalized"
    assert historical.operation == "grouping"
    assert historical.raw_output_text.startswith('{"groups"')  # resposta original v3 intacta


def test_accepted_normalized_stays_grouping_only_and_error_free():
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_attempt_kwargs(operation="reconciliation"))
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(
            **_attempt_kwargs(operation="extraction", target_claim_ids=[], target_model_response_id="mr-1")
        )
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(**_attempt_kwargs(parse_error_message="erro"))
    with pytest.raises(ValidationError):
        ClaimProcessingAttempt(
            **_attempt_kwargs(
                transport_status="error",
                raw_output_text=None,
                transport_error=ProviderErrorInfo(type=ProviderErrorType.TIMEOUT, message="t", retryable=True),
            )
        )


def test_accepted_normalized_does_not_exist_for_judge_editor_or_source_analysis():
    for model in (JudgeAttempt, EditorAttempt, SourceAnalysisAttempt):
        assert "accepted_normalized" not in set(typing.get_args(model.model_fields["parse_status"].annotation))
