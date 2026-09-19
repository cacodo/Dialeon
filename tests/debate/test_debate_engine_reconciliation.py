"""
Testes de ponta a ponta (`DebateEngine.run()`) da reconciliação cross-round --
cobrem o que só existe na ORQUESTRAÇÃO (não em `reconcile_claims` isolada,
já testada em tests/debate/test_reconciliation.py): o cálculo de
"sobreviventes atuais de cada rodada" DEPOIS do Round 2 (que precisa
respeitar revisões já existentes), o gate de ambos-os-lados-não-vazios, o
gate de budget, e o cômputo real de support_scope_model_count a partir dos
ModelResponse verdadeiros de Round 1 + Round 2.

Um único provider dedicado ("claude-processor", fora de enabled_providers,
mesmo padrão de test_claim_processor_provider_does_not_need_to_be_in_enabled_providers
em test_debate_engine.py) responde TODAS as chamadas de
extração/agrupamento/reconciliação, roteadas por conteúdo da requisição --
os debatedores reais (ex.: "openai") só respondem rodada inicial/crítica.
"""

from __future__ import annotations

import json

import pytest

from app.debate.claims import get_current_claims
from app.debate.debate_engine import DebateEngine
from app.models.provider_models import (
    ModelIdentitySource,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderResponse,
    TokenUsage,
)
from app.orchestrator.config import QuorumPolicy, RunConfig
from tests.debate.fakes import CallableProvider


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


def _processor_handler(
    extraction_map: dict[str, str],
    *,
    grouping_response: str | None = None,
    reconciliation_response: str | None = None,
    revision_of_prefix: str | None = None,
    revision_new_text: str | None = None,
):
    """Processor dedicado -- roteia por conteúdo, nunca por call_index
    (múltiplas extrações/agrupamentos/reconciliação intercalados).

    `extraction_map`: {substring da resposta sendo analisada -> JSON de
    extração a devolver}. `revision_of_prefix`/`revision_new_text`: quando
    presentes, a extração da rodada de crítica cujo texto bate produz uma
    claim com `revises_claim_id` resolvido dinamicamente via
    CLAIMS_ANTERIORES (o id real só existe em runtime)."""

    async def handler(call_index: int, request) -> ProviderResponse:
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            if reconciliation_response is not None:
                return _ok("claude-processor", reconciliation_response)
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok(
                "claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids})
            )
        if "CLAIMS_BRUTAS" in content:
            if grouping_response is not None:
                return _ok("claude-processor", grouping_response)
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok(
                "claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids})
            )
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


def _extraction(text: str) -> str:
    return json.dumps({"claims": [{"text": text, "revises_claim_id": None}]})


def _providers(participant_handler, processor_handler) -> dict[str, CallableProvider]:
    return {
        "openai": CallableProvider("openai", participant_handler),
        "claude-processor": CallableProvider("claude-processor", processor_handler),
    }


# ---------------------------------------------------------------------------
# 1. Paráfrase R1 + R2 equivalente -> uma única claim reconciliada atual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_paraphrase_plus_r2_equivalent_paraphrase_becomes_one_current_claim():
    r1_text = "O céu é azul devido ao espalhamento de Rayleigh."
    r2_text = "A cor azul do céu vem do espalhamento de Rayleigh da luz solar."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation_response=None,  # será sobrescrito por request abaixo
    )

    async def reconciling_processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            ids = [c["id"] for c in payload]
            assert len(ids) == 2
            return _ok(
                "claude-processor",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": ids, "canonical_text": "proposição unificada"}],
                        "ungrouped_claim_ids": [],
                    }
                ),
            )
        return await processor(call_index, request)

    providers = _providers(participant, reconciling_processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    assert len(current) == 1
    assert current[0].text == "proposição unificada"
    assert current[0].round_introduced == 2  # max(1, 2)


# ---------------------------------------------------------------------------
# 2. Revisão explícita em R2 -> lineage de revisão vence, NUNCA convertida
#    em merge de reconciliação
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_explicit_revision_wins_over_reconciliation_merge():
    r1_text = "O total é 264."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={"resposta inicial": _extraction(r1_text)},
        revision_of_prefix="O total é 264",
        revision_new_text="O total é 270, corrigindo a afirmação anterior de 264.",
        # reconciliação nunca deveria nem rodar (round1 fica vazio depois
        # da revisão retirar a única claim de lá) -- se rodasse por
        # engano, este payload provaria o bug fundindo o que não deveria.
        reconciliation_response=json.dumps(
            {"groups": [], "ungrouped_claim_ids": []}
        ),
    )
    providers = _providers(participant, processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    assert len(current) == 1
    assert current[0].text == "O total é 270, corrigindo a afirmação anterior de 264."
    assert current[0].parent_claim_id is not None  # é uma REVISÃO, não uma fusão
    assert current[0].merged_from_claim_ids == []
    # nenhuma tentativa de reconciliação foi feita -- round1 ficou vazio
    # (a única claim de lá foi retirada pela revisão) antes mesmo do gate
    # ambos-os-lados-não-vazios.
    reconciliation_attempts = [
        a for a in result.claim_processing_attempts if a.operation == "reconciliation"
    ]
    assert reconciliation_attempts == []


# ---------------------------------------------------------------------------
# 3. Contradição em R2 -> ambas as claims permanecem atuais e distintas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_contradiction_keeps_both_claims_current_and_distinct():
    r1_text = "O experimento confirmou a hipótese A."
    r2_text = "O experimento refutou a hipótese A."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        # reconciliação corretamente decide NÃO fundir -- contradição
        # nunca é equivalência, mesmo compartilhando o assunto.
        reconciliation_response=None,
    )
    providers = _providers(participant, processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    texts = {c.text for c in current}
    assert texts == {r1_text, r2_text}


# ---------------------------------------------------------------------------
# 4. Refinamento em R2 (detalhe adicional material) -> ambas permanecem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_refinement_with_additional_detail_keeps_both_claims_current():
    r1_text = "O algoritmo tem complexidade O(n log n)."
    r2_text = "O algoritmo tem complexidade O(n log n) apenas no caso médio; no pior caso é O(n^2)."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation_response=None,  # refinamento não é a mesma proposição -- fica ungrouped
    )
    providers = _providers(participant, processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    texts = {c.text for c in current}
    assert texts == {r1_text, r2_text}


# ---------------------------------------------------------------------------
# 5. Claim independente em R2 (sem equivalente em R1) -> permanece atual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_independent_r2_claim_without_r1_equivalent_remains_current():
    r1_text = "A capital do Brasil é Brasília."
    r2_text = "O Brasil tem 26 estados e um distrito federal."  # proposição totalmente nova

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation_response=None,
    )
    providers = _providers(participant, processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    current = get_current_claims(result.claims)
    texts = {c.text for c in current}
    assert texts == {r1_text, r2_text}


# ---------------------------------------------------------------------------
# Lado vazio -> reconciliação pulada, nenhum attempt fabricado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_round2_side_skips_reconciliation_entirely():
    """9 (variante "nenhum candidato possível") -- crítica produz ZERO
    claims extraídas (extração devolve `{"claims": []}`, legitimamente --
    a resposta não afirmou nada extraível) -> current_round2 fica vazio
    -> reconciliação nunca é tentada, nenhum ClaimProcessingAttempt
    fabricado pra uma comparação que não podia produzir nada."""
    r1_text = "Única claim da rodada inicial."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")

    async def processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            raise AssertionError("reconciliação nunca deveria ser chamada aqui")
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok("claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids}))
        if "RESPOSTA_A_ANALISAR" in content:
            if "resposta inicial" in content:
                return _ok("claude-processor", _extraction(r1_text))
            return _ok("claude-processor", json.dumps({"claims": []}))  # crítica sem claim nova
        raise AssertionError(f"chamada inesperada: {content[:200]}")

    providers = _providers(participant, processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    reconciliation_attempts = [
        a for a in result.claim_processing_attempts if a.operation == "reconciliation"
    ]
    assert reconciliation_attempts == []
    current = get_current_claims(result.claims)
    assert len(current) == 1
    assert current[0].text == r1_text


# ---------------------------------------------------------------------------
# support_scope_model_count reflete a UNIÃO real de identidades
# provider/model bem-sucedidas entre Round 1 e Round 2 -- nunca soma,
# nunca max, nunca a contagem de só uma rodada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_support_scope_model_count_reflects_real_cross_round_union():
    """16 -- Round 1: openai + gemini bem-sucedidos (2 identidades).
    Round 2: gemini FALHA na crítica (erro de transporte), só openai
    responde -- união real = {openai, gemini} = 2, NUNCA 2+1=3 (soma),
    NUNCA max(2,1)=2 por coincidência (o valor certo aqui é o MESMO que
    max, mas por ser genuinamente a união, não por acidente -- ver teste
    seguinte para um caso onde união != max)."""
    r1_text_openai = "Proposição sobre X."
    r1_text_gemini = "Outra proposição, independente."
    r2_text_openai = "Reafirmação da proposição sobre X."

    async def openai_handler(call_index: int, request):
        if call_index == 1:
            return _ok("openai", "resposta inicial openai")
        return _ok("openai", "resposta de crítica openai")

    async def gemini_handler(call_index: int, request):
        if call_index == 1:
            return _ok("gemini", "resposta inicial gemini")
        return ProviderResponse(
            provider="gemini",
            requested_model="fake-model",
            model="fake-model",
            model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
            status="error",
            text=None,
            usage=None,
            cost_usd=None,
            latency_ms=10,
            attempts=2,
            error=ProviderErrorInfo(type=ProviderErrorType.API_ERROR, message="falhou", retryable=True),
        )

    async def processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            ids = [c["id"] for c in payload]
            # funde a claim de openai (R1) com a de openai (R2) -- a de
            # gemini (R1) fica independente, sem equivalente.
            openai_ids = [c["id"] for c in payload if "sobre X" in c["text"]]
            other_ids = [i for i in ids if i not in openai_ids]
            return _ok(
                "claude-processor",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": openai_ids, "canonical_text": "X confirmado"}],
                        "ungrouped_claim_ids": other_ids,
                    }
                ),
            )
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok("claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids}))
        if "RESPOSTA_A_ANALISAR" in content:
            if "resposta inicial openai" in content:
                return _ok("claude-processor", _extraction(r1_text_openai))
            if "resposta inicial gemini" in content:
                return _ok("claude-processor", _extraction(r1_text_gemini))
            if "resposta de crítica openai" in content:
                return _ok("claude-processor", _extraction(r2_text_openai))
            raise AssertionError(f"resposta não reconhecida: {content[:200]}")
        raise AssertionError(f"chamada inesperada: {content[:200]}")

    providers = {
        "openai": CallableProvider("openai", openai_handler),
        "gemini": CallableProvider("gemini", gemini_handler),
        "claude-processor": CallableProvider("claude-processor", processor),
    }
    engine = DebateEngine(providers)
    result = await engine.run(
        _run_config(
            ["openai", "gemini"], quorum=QuorumPolicy(min_for_debate=2, min_to_return=1)
        )
    )

    reconciled = next(c for c in result.claims if c.text == "X confirmado")
    assert reconciled.support_scope_model_count == 2  # {openai, gemini}, nunca 3
    assert reconciled.total_models_in_round == 1  # Round 2 (a mais recente) teve só 1 sucesso


@pytest.mark.asyncio
async def test_support_scope_model_count_uses_union_not_max_of_round_counts():
    """11 -- prova que a implementação usa UNIÃO de identidades
    provider/model, NUNCA max(contagem por rodada). Round 1: openai
    (model="modelo-v1") + gemini bem-sucedidos (2 identidades). Round 2:
    só openai responde à crítica, mas o PROVIDER reporta um modelo
    EFETIVO diferente ("modelo-v2", simulando uma mudança real de
    roteamento entre chamadas) -- união real = {(openai,modelo-v1),
    (gemini,·), (openai,modelo-v2)} = 3, enquanto max(round1_count=2,
    round2_count=1) = 2. Se a implementação usasse max() em vez de
    união, este teste falharia com support_scope_model_count=2.

    Nota sobre o caso totalmente disjunto (R1={A,B}, R2={C,D}): esta
    arquitetura NUNCA permite isso de ponta a ponta -- os participantes
    da crítica (Round 2) são sempre um SUBCONJUNTO de quem teve sucesso
    no Round 1 (`critique_participants = [r.provider for r in
    successful_round1]`, ver app/debate/debate_engine.py) -- um provider
    genuinamente novo não pode aparecer só na crítica. A união pode
    ainda divergir do max via o MESMO provider reportando um `model`
    efetivo diferente entre rodadas (como aqui) -- esse é o caso real
    que este teste prova; o caso puramente disjunto é verificado
    separadamente, como uma propriedade da FÓRMULA (não da orquestração
    real), em
    tests/debate/test_reconciliation.py::test_caller_supplied_denominators_are_used_verbatim
    e no teste de aritmética pura abaixo."""
    r1_text_openai = "Proposição sobre Y."
    r1_text_gemini = "Outra proposição, independente."
    r2_text_openai = "Reafirmação da proposição sobre Y."

    async def openai_handler(call_index: int, request):
        if call_index == 1:
            return _ok("openai", "resposta inicial openai")
        # Round 2: mesmo provider, modelo EFETIVO reportado diferente --
        # nunca construído via _ok() (que fixaria "fake-model" pros dois).
        return ProviderResponse(
            provider="openai",
            requested_model="fake-model",
            model="modelo-v2",
            model_identity_source=ModelIdentitySource.PROVIDER_REPORTED,
            status="success",
            text="resposta de crítica openai",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            cost_usd=None,
            latency_ms=10,
            attempts=1,
        )

    async def gemini_handler(call_index: int, request):
        if call_index == 1:
            return _ok("gemini", "resposta inicial gemini")
        return ProviderResponse(
            provider="gemini",
            requested_model="fake-model",
            model="fake-model",
            model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK,
            status="error",
            text=None,
            usage=None,
            cost_usd=None,
            latency_ms=10,
            attempts=2,
            error=ProviderErrorInfo(type=ProviderErrorType.API_ERROR, message="falhou", retryable=True),
        )

    async def processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            ids = [c["id"] for c in payload]
            openai_ids = [c["id"] for c in payload if "sobre Y" in c["text"]]
            other_ids = [i for i in ids if i not in openai_ids]
            return _ok(
                "claude-processor",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": openai_ids, "canonical_text": "Y confirmado"}],
                        "ungrouped_claim_ids": other_ids,
                    }
                ),
            )
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok("claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids}))
        if "RESPOSTA_A_ANALISAR" in content:
            if "resposta inicial openai" in content:
                return _ok("claude-processor", _extraction(r1_text_openai))
            if "resposta inicial gemini" in content:
                return _ok("claude-processor", _extraction(r1_text_gemini))
            if "resposta de crítica openai" in content:
                return _ok("claude-processor", _extraction(r2_text_openai))
            raise AssertionError(f"resposta não reconhecida: {content[:200]}")
        raise AssertionError(f"chamada inesperada: {content[:200]}")

    providers = {
        "openai": CallableProvider("openai", openai_handler),
        "gemini": CallableProvider("gemini", gemini_handler),
        "claude-processor": CallableProvider("claude-processor", processor),
    }
    engine = DebateEngine(providers)
    result = await engine.run(
        _run_config(
            ["openai", "gemini"], quorum=QuorumPolicy(min_for_debate=2, min_to_return=1)
        )
    )

    reconciled = next(c for c in result.claims if c.text == "Y confirmado")
    assert reconciled.support_scope_model_count == 3  # união real, nunca max(2,1)=2
    assert reconciled.total_models_in_round == 1  # Round 2 (a mais recente) teve só 1 sucesso


def test_union_formula_handles_the_fully_disjoint_case_arithmetically():
    """11 (caso disjunto, aritmética pura) -- R1={A,B}, R2={C,D} --
    completamente disjunto, união=4, max(2,2)=2. Não construível de
    ponta a ponta nesta arquitetura (ver nota no teste anterior), mas a
    fórmula que `DebateEngine.run` usa
    (`{(r.provider, r.model) for r in successful_round1 + successful_round2}`)
    é pura aritmética de conjuntos -- este teste verifica a fórmula em
    si, com a MESMA estrutura de dados (tuplas provider/model) que o
    código real produz a partir de `ModelResponse.provider`/`.model`."""
    round1_identities = {("A", "model-a"), ("B", "model-b")}
    round2_identities = {("C", "model-c"), ("D", "model-d")}

    union = round1_identities | round2_identities

    assert len(union) == 4
    assert len(union) != max(len(round1_identities), len(round2_identities))


# ---------------------------------------------------------------------------
# 9. Budget exhausted antes da reconciliação -> reconciliação pulada,
#    nenhum attempt fabricado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_exhausted_before_reconciliation_skips_it_cleanly():
    r1_text = "Claim da rodada inicial."
    r2_text = "Claim da rodada de crítica."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        # se a reconciliação fosse chamada por engano, isto provaria o
        # bug fundindo indevidamente.
        reconciliation_response=json.dumps(
            {"groups": [], "ungrouped_claim_ids": []}
        ),
    )
    providers = _providers(participant, processor)
    engine = DebateEngine(providers)
    # 90 tokens = EXATAMENTE round1 (resposta 15 + extração 15 +
    # agrupamento 15 = 45) + round2 (crítica 15 + extração 15 +
    # agrupamento 15 = 45) -- suficiente pra completar as duas rodadas,
    # mas já esgotado (>=) no gate ANTES da reconciliação, que exigiria
    # mais 15.
    result = await engine.run(_run_config(["openai"], max_total_tokens=90))

    reconciliation_attempts = [
        a for a in result.claim_processing_attempts if a.operation == "reconciliation"
    ]
    assert reconciliation_attempts == []
    assert result.cumulative_budget_exceeded is True
    # ambas as claims pré-reconciliação seguem presentes -- degradação
    # segura, nunca abortada.
    current = get_current_claims(result.claims)
    assert len(current) == 2


# ---------------------------------------------------------------------------
# 10. Conjunto atual pós-reconciliação é exatamente o que o Judge
#     consumiria (get_current_claims(debate_result.claims), a mesma
#     chamada que SingleJudge.judge() faz internamente)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_facing_current_claims_reflect_successful_reconciliation():
    r1_text = "A receita cresceu no último trimestre."
    r2_text = "A receita teve crescimento no trimestre mais recente."

    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")

    async def reconciling_processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok(
                "claude-processor",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": ids, "canonical_text": "crescimento de receita confirmado"}],
                        "ungrouped_claim_ids": [],
                    }
                ),
            )
        if "CLAIMS_BRUTAS" in content:
            payload = json.loads(content.split("CLAIMS_BRUTAS:\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok("claude-processor", json.dumps({"groups": [], "ungrouped_claim_ids": ids}))
        if "RESPOSTA_A_ANALISAR" in content:
            if "resposta inicial" in content:
                return _ok("claude-processor", _extraction(r1_text))
            return _ok("claude-processor", _extraction(r2_text))
        raise AssertionError(f"chamada inesperada: {content[:200]}")

    providers = _providers(participant, reconciling_processor)
    engine = DebateEngine(providers)
    result = await engine.run(_run_config(["openai"]))

    # Exatamente a chamada que app/judge/single_judge.py faz internamente
    # -- provar que ela vê o conjunto RECONCILIADO, nunca os 2 originais.
    judge_facing_claims = get_current_claims(result.claims)
    assert len(judge_facing_claims) == 1
    assert judge_facing_claims[0].text == "crescimento de receita confirmado"


# ---------------------------------------------------------------------------
# Judge Transport Execution Policy V1 -- nenhum override vaza pro pipeline de
# debate (participantes, extração, agrupamento, reconciliação)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_transport_policy_override_reaches_participants_extraction_grouping_or_reconciliation():
    r1_text = "O céu é azul devido ao espalhamento de Rayleigh."
    r2_text = "A cor azul do céu vem do espalhamento de Rayleigh da luz solar."
    participant = _participant_handler("openai", "resposta inicial", "resposta de crítica")
    processor = _processor_handler(
        extraction_map={
            "resposta inicial": _extraction(r1_text),
            "resposta de crítica": _extraction(r2_text),
        },
        reconciliation_response=None,
    )

    async def reconciling_processor(call_index: int, request):
        content = request.messages[0].content
        if "CLAIMS_ATUAIS" in content:
            payload = json.loads(content.split("rodada de crítica combinadas):\n", 1)[1])
            ids = [c["id"] for c in payload]
            return _ok(
                "claude-processor",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": ids, "canonical_text": "proposição unificada"}],
                        "ungrouped_claim_ids": [],
                    }
                ),
            )
        return await processor(call_index, request)

    providers = _providers(participant, reconciling_processor)
    result = await DebateEngine(providers).run(_run_config(["openai"]))

    processor_requests = providers["claude-processor"].received_requests
    # o cenário exercita mesmo extração E reconciliação (não é vácuo)
    assert any("CLAIMS_ATUAIS" in r.messages[0].content for r in processor_requests)
    assert len(processor_requests) >= 3
    assert len(providers["openai"].received_requests) >= 2  # rodada inicial + crítica
    assert get_current_claims(result.claims)  # pipeline completou

    for name, provider in providers.items():
        assert provider.received_execution_policies, name
        assert all(p is None for p in provider.received_execution_policies), name
