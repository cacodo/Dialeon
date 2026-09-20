"""
Testes de ponta a ponta (`DebateEngine.run()`) da reconciliação cross-round
v2 (cross_round_claim_reconciliation_v2) -- CONSULTIVA e NÃO-DESTRUTIVA.

A reconciliação propõe relações ESPARSAS de equivalência entre uma claim
atual do Round 1 e uma do Round 2 (`equivalence_clusters`). A proposta fica
só na resposta bruta auditável do `ClaimProcessingAttempt`: NUNCA cria claim,
une/transfere suporte, cria `parent_claim_id`, supersede ou remove claim.
Só a revisão EXPLÍCITA da extração (`parent_claim_id`) altera o conjunto de
claims atuais -- semântica preservada exatamente como era.

Estes testes são sobre CONTENÇÃO DE AUTORIDADE e o pipeline; nunca aprovam a
validade semântica de uma equivalência proposta (o "modelo" roteirizado pode
propor relações erradas de propósito). Um único provider dedicado
("claude-processor", fora de enabled_providers) responde TODAS as chamadas de
extração/agrupamento/reconciliação, roteadas por conteúdo.
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from app.debate.claims import get_current_claims
from app.debate.debate_engine import DebateEngine
from app.judge.context import build_judge_request
from app.models.provider_models import (
    ModelIdentitySource,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
    TokenUsage,
)
from app.orchestrator.config import QuorumPolicy, RunConfig
from tests.debate.fakes import CallableProvider

NO_RELATIONS = json.dumps({"equivalence_clusters": []})


def _run_config(enabled_providers, **overrides) -> RunConfig:
    fields = dict(
        question="Qual é o valor correto?",
        enabled_providers=enabled_providers,
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        round_dispatch_timeout_seconds=5.0,
        claim_processor_provider="claude-processor",
        judge_provider="claude-processor",
        editor_provider="claude-processor",
        source_analyzer_provider="claude-processor",
    )
    fields.update(overrides)
    return RunConfig(**fields)


def _ok(provider: str, text: str) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model="fake-model",
        model="fake-model",
        model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
        status="success",
        text=text,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        cost_usd=None,
        latency_ms=10,
        attempts=1,
    )


def _err(provider: str) -> ProviderResponse:
    return ProviderResponse(
        provider=provider,
        requested_model="fake-model",
        model="fake-model",
        model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
        status="error",
        text=None,
        usage=None,
        cost_usd=None,
        latency_ms=10,
        attempts=1,
        error=ProviderErrorInfo(type=ProviderErrorType.API_ERROR, message="x", retryable=False),
    )


def _participant_handler(provider_name: str, initial_text: str, critique_text: str):
    """Debatedor comum -- 2 chamadas: rodada inicial, depois crítica.
    Nunca é o processor, então nunca vê extração/agrupamento/reconciliação."""

    async def handler(call_index: int, request) -> ProviderResponse:
        if call_index == 1:
            return _ok(provider_name, initial_text)
        return _ok(provider_name, critique_text)

    return handler


def _known_claims_payload(content: str) -> list[dict]:
    """Extrai CLAIMS_ANTERIORES (id+texto) do corpo de uma requisição de
    extração da rodada de crítica -- ver app/debate/claim_extraction.py,
    _build_extraction_request."""
    marker = "só pode referenciar um destes ids):"
    if marker not in content:
        return []
    return json.loads(content.split(marker, 1)[1].strip())


def _find_id_by_text_prefix(known_claims: list[dict], text_prefix: str) -> str:
    for entry in known_claims:
        if entry["text"].startswith(text_prefix):
            return entry["id"]
    raise AssertionError(f"nenhuma claim conhecida com prefixo {text_prefix!r}: {known_claims}")


def _extraction(text: str) -> str:
    return json.dumps({"claims": [{"text": text, "revises_claim_id": None}]})


def _all_singleton_clusters(payload: list[dict]) -> str:
    return json.dumps({"clusters": [[c["id"]] for c in payload]})


def _relate_first_of_each_round(payload: list[dict]) -> str:
    """Proposta roteirizada (possivelmente ERRADA): um cluster com a 1ª claim
    round=1 e a 1ª claim round=2."""
    r1 = next(c["id"] for c in payload if c["round"] == 1)
    r2 = next(c["id"] for c in payload if c["round"] == 2)
    return json.dumps({"equivalence_clusters": [[r1, r2]]})


def _processor_handler(
    extraction_map: dict[str, str],
    *,
    reconciliation: Callable[[list[dict]], str] | None = None,
    revision_of_prefix: str | None = None,
    revision_new_text: str | None = None,
    captured: dict | None = None,
):
    """Processor dedicado -- roteia por conteúdo, nunca por call_index.

    `extraction_map`: {substring da resposta analisada -> JSON de extração}.
    `reconciliation`: função (payload CLAIMS_ATUAIS -> texto de resposta);
    default = nenhuma relação proposta. `revision_of_prefix`/
    `revision_new_text`: a extração da rodada de crítica cujo contexto contém
    o prefixo produz uma claim com `revises_claim_id` resolvido dinamicamente.
    `captured`: dicionário onde o handler guarda o que recebeu."""
    captured = captured if captured is not None else {}

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            captured.setdefault("reconciliation_payloads", []).append(payload)
            builder = reconciliation or (lambda _p: NO_RELATIONS)
            return _ok("claude-processor", builder(payload))
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            return _ok("claude-processor", _all_singleton_clusters(payload))
        if "RESPOSTA_A_ANALISAR" in content:
            if revision_of_prefix is not None and revision_of_prefix in content:
                known = _known_claims_payload(content)
                parent_id = _find_id_by_text_prefix(known, revision_of_prefix)
                text = json.dumps(
                    {"claims": [{"text": revision_new_text, "revises_claim_id": parent_id}]}
                )
                return _ok("claude-processor", text)
            for marker, extraction_json in extraction_map.items():
                if marker in content:
                    return _ok("claude-processor", extraction_json)
            raise AssertionError(f"resposta não reconhecida pelo processor: {content[:300]}")
        raise AssertionError(f"chamada inesperada pro processor: {content[:300]}")

    return handler


def _providers(participant_handler, processor_handler) -> dict[str, CallableProvider]:
    return {
        "openai": CallableProvider("openai", participant_handler),
        "claude-processor": CallableProvider("claude-processor", processor_handler),
    }


def _reconciliation_attempts(result):
    return [a for a in result.claim_processing_attempts if a.operation == "reconciliation"]


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
            c.support_scope_model_count,
        )
        for c in claims
    )


# ---------------------------------------------------------------------------
# 1. Proposta de equivalência cross-round -> NADA muda (autoridade)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_r2_equivalence_proposal_is_audited_but_changes_no_claim():
    r1_text = "O céu é azul devido ao espalhamento de Rayleigh."
    r2_text = "A cor azul do céu vem do espalhamento de Rayleigh da luz solar."
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation=_relate_first_of_each_round,
    )

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    attempts = _reconciliation_attempts(result)
    assert [a.parse_status for a in attempts] == ["accepted"]  # estruturalmente aceita
    proposed = json.loads(attempts[0].raw_output_text)["equivalence_clusters"]
    assert len(proposed) == 1 and len(proposed[0]) == 2  # a proposta existiu e ficou auditável

    # NADA autoritativo mudou: 2 claims originais, atuais, sem canônica
    assert {c.text for c in result.claims} == {r1_text, r2_text}
    assert len(result.claims) == 2
    assert {c.text for c in get_current_claims(result.claims)} == {r1_text, r2_text}
    for claim in result.claims:
        assert claim.source_model_response_id is not None  # nenhuma canônica (essas têm None)
        assert not claim.merged_from_claim_ids
        assert claim.parent_claim_id is None
        assert claim.superseded_by is None
        assert claim.support_scope_model_count is None
        assert [s.provider for s in claim.supporting_model_response_ids] == ["openai"]


@pytest.mark.asyncio
async def test_claim_set_is_identical_with_and_without_a_relation_proposal():
    async def run(reconciliation):
        participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
        processor = _processor_handler(
            extraction_map={
                "resposta inicial": _extraction("Claim A."),
                "resposta de crítica": _extraction("Claim B."),
            },
            reconciliation=reconciliation,
        )
        return await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    none = await run(None)
    proposed = await run(_relate_first_of_each_round)

    assert _fingerprint(none.claims) == _fingerprint(proposed.claims)
    assert _fingerprint(get_current_claims(none.claims)) == _fingerprint(
        get_current_claims(proposed.claims)
    )


@pytest.mark.asyncio
async def test_downstream_inputs_are_unchanged_by_a_bad_relation_proposal():
    """Regressão de AUTORIDADE (não aprova o merge): mesmo com uma proposta
    ERRADA, o Judge recebe as DUAS claims originais."""
    r1_text = "A decisão poderia mudar se surgissem novas condições."
    r2_text = "O hosting gerenciado com SLA e preço fixo mudaria a decisão."
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation=_relate_first_of_each_round,
    )

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    request = build_judge_request("Pergunta?", result, current, 8192)
    body = request.messages[0].content
    assert r1_text in body and r2_text in body
    assert len(current) == 2


# ---------------------------------------------------------------------------
# 2. Revisão explícita -- semântica de parent_claim_id INALTERADA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_explicit_revision_remains_the_only_thing_that_retires_a_claim():
    r1_text = "O total é 264."
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={"resposta inicial": _extraction(r1_text)},
        revision_of_prefix="O total é 264",
        revision_new_text="O total é 270, corrigindo a afirmação anterior de 264.",
        # se a reconciliação rodasse por engano, esta proposta provaria o bug
        reconciliation=_relate_first_of_each_round,
    )

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    assert [c.text for c in current] == ["O total é 270, corrigindo a afirmação anterior de 264."]
    assert current[0].parent_claim_id is not None  # REVISÃO explícita da extração
    assert current[0].merged_from_claim_ids == []
    # a claim revisada segue no histórico, não-atual, com a linhagem de sempre
    parent = next(c for c in result.claims if c.id == current[0].parent_claim_id)
    assert parent.text == r1_text and parent.parent_claim_id is None
    assert parent.id not in {c.id for c in current}
    # nenhuma reconciliação: o lado Round 1 ficou vazio após a revisão
    assert _reconciliation_attempts(result) == []


@pytest.mark.asyncio
async def test_revised_parent_is_not_eligible_and_a_proposal_cannot_use_it_or_create_linkage():
    """Um id de claim JÁ revisada (não-atual) não é elegível: uma proposta que
    o referencia é REJEITADA (retry estruturado) e nunca cria/altera
    `parent_claim_id`. Só a revisão explícita da extração existe."""
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    seen: dict = {}
    revised_id_holder: dict = {}

    def reconciliation(payload: list[dict]) -> str:
        eligible_ids = {c["id"] for c in payload}
        revised_id = revised_id_holder["id"]
        assert revised_id not in eligible_ids  # a revisada NÃO é candidata
        if not seen.get("first"):
            seen["first"] = True
            r1 = next(c["id"] for c in payload if c["round"] == 1)
            return json.dumps({"equivalence_clusters": [[revised_id, r1]]})  # usa id INELEGÍVEL
        return NO_RELATIONS

    base = _processor_handler(
        extraction_map={
            "resposta inicial": json.dumps(
                {
                    "claims": [
                        {"text": "O total é 264.", "revises_claim_id": None},
                        {"text": "Uma claim independente do Round 1.", "revises_claim_id": None},
                    ]
                }
            ),
        },
        revision_of_prefix="O total é 264",
        revision_new_text="O total é 270, corrigindo a afirmação anterior de 264.",
        reconciliation=reconciliation,
    )

    async def processor(call_index, request):
        content = request.messages[0].content
        if "RESPOSTA_A_ANALISAR" in content:
            known = _known_claims_payload(content)
            if known:
                revised_id_holder["id"] = _find_id_by_text_prefix(known, "O total é 264")
        return await base(call_index, request)

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    attempts = _reconciliation_attempts(result)
    assert [a.parse_status for a in attempts] == ["inconsistent_references", "accepted"]
    current_texts = {c.text for c in get_current_claims(result.claims)}
    assert current_texts == {
        "O total é 270, corrigindo a afirmação anterior de 264.",
        "Uma claim independente do Round 1.",
    }
    # exatamente UMA claim com parent_claim_id (a revisão explícita da extração)
    with_parent = [c for c in result.claims if c.parent_claim_id is not None]
    assert [c.text for c in with_parent] == ["O total é 270, corrigindo a afirmação anterior de 264."]
    assert all(not c.merged_from_claim_ids for c in result.claims)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "r1_text, r2_text",
    [
        ("O experimento confirmou a hipótese A.", "O experimento refutou a hipótese A."),  # contradição
        ("O SaaS reduz a manutenção.", "O SaaS reduz a manutenção, mas não elimina a gestão de usuários."),  # refinamento
        ("O custo é de US$ 12 por usuário.", "O custo é de US$ 21 por usuário."),  # número
    ],
    ids=["contradiction", "refinement", "numeric"],
)
async def test_even_a_wrong_proposal_over_contradiction_refinement_or_numbers_changes_nothing(
    r1_text, r2_text
):
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation=_relate_first_of_each_round,
    )

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    assert {c.text for c in get_current_claims(result.claims)} == {r1_text, r2_text}
    assert len(result.claims) == 2


# ---------------------------------------------------------------------------
# 3. Elegibilidade / execução
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_payload_carries_all_current_claims_with_round_identity():
    captured: dict = {}
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction("Claim do Round 1."),
            "resposta de crítica": _extraction("Claim do Round 2."),
        },
        captured=captured,
    )

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    payload = captured["reconciliation_payloads"][0]
    assert [(e["round"], e["text"]) for e in payload] == [
        (1, "Claim do Round 1."),
        (2, "Claim do Round 2."),
    ]
    current_by_round = {c.text: c.round_introduced for c in get_current_claims(result.claims)}
    assert current_by_round == {"Claim do Round 1.": 1, "Claim do Round 2.": 2}


@pytest.mark.asyncio
async def test_empty_round2_side_skips_reconciliation_entirely():
    r1_text = "Única claim da rodada inicial."
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")

    async def processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            raise AssertionError("reconciliação nunca deveria ser chamada aqui")
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            return _ok("claude-processor", _all_singleton_clusters(payload))
        if "RESPOSTA_A_ANALISAR" in content:
            if "resposta inicial" in content:
                return _ok("claude-processor", _extraction(r1_text))
            return _ok("claude-processor", json.dumps({"claims": []}))  # crítica sem claim nova
        raise AssertionError(f"chamada inesperada: {content[:200]}")

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    assert _reconciliation_attempts(result) == []
    assert [c.text for c in get_current_claims(result.claims)] == [r1_text]


@pytest.mark.asyncio
async def test_budget_exhausted_before_reconciliation_skips_it_cleanly():
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction("Claim da rodada inicial."),
            "resposta de crítica": _extraction("Claim da rodada de crítica."),
        },
        reconciliation=_relate_first_of_each_round,  # se chamada por engano, provaria o bug
    )

    # 90 tokens = EXATAMENTE round1 (45) + round2 (45): completa as duas rodadas,
    # mas já esgotado no gate ANTES da reconciliação.
    result = await DebateEngine(_providers(participant, processor)).run(
        _run_config(["openai"], max_total_tokens=90)
    )

    assert _reconciliation_attempts(result) == []
    assert result.cumulative_budget_exceeded is True
    assert len(get_current_claims(result.claims)) == 2  # degradação segura, nunca abortada


# ---------------------------------------------------------------------------
# 4. Suporte de provedores diferentes NUNCA é unido/transferido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_proposal_across_providers_never_unions_or_transfers_support():
    async def processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            return _ok("claude-processor", _relate_first_of_each_round(payload))
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            return _ok("claude-processor", _all_singleton_clusters(payload))
        if "RESPOSTA_A_ANALISAR" in content:
            for marker, text in (
                ("resposta inicial openai", "Proposição do openai no Round 1."),
                ("resposta inicial gemini", "Proposição do gemini no Round 1."),
            ):
                if marker in content:
                    return _ok("claude-processor", _extraction(text))
            return _ok("claude-processor", _extraction("Proposição do gemini no Round 2."))
        raise AssertionError(content[:200])

    providers = {
        "openai": CallableProvider(
            "openai", _participant_handler("openai", "resposta inicial openai", "crítica openai")
        ),
        "gemini": CallableProvider(
            "gemini", _participant_handler("gemini", "resposta inicial gemini", "crítica gemini")
        ),
        "claude-processor": CallableProvider("claude-processor", processor),
    }

    result = await DebateEngine(providers).run(_run_config(["openai", "gemini"]))

    by_text = {c.text: c for c in result.claims}
    assert {s.provider for s in by_text["Proposição do openai no Round 1."].supporting_model_response_ids} == {"openai"}
    assert {s.provider for s in by_text["Proposição do gemini no Round 1."].supporting_model_response_ids} == {"gemini"}
    for claim in result.claims:
        assert len({s.model_response_id for s in claim.supporting_model_response_ids}) == 1
        assert claim.support_scope_model_count is None


# ---------------------------------------------------------------------------
# 5. Contadores de tokens/custo da tentativa seguem contabilizados
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_attempt_accounting_is_recorded_like_any_other_attempt():
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction("Claim A."),
            "resposta de crítica": _extraction("Claim B."),
        },
    )

    result = await DebateEngine(_providers(participant, processor)).run(_run_config(["openai"]))

    (attempt,) = _reconciliation_attempts(result)
    assert attempt.usage is not None and attempt.usage.output_tokens == 5
    assert attempt.operation == "reconciliation" and attempt.attempt_number == 1
    assert attempt.request_provenance.contract_version == "cross_round_claim_reconciliation_v2"
