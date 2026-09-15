"""
Provider-Neutral Request Provenance V1 -- contrato de "golden digest" por
OPERAÇÃO (seção 23 do contrato desta slice).

GOVERNANÇA OBRIGATÓRIA (leia antes de tocar em qualquer um destes
testes):

Cada teste abaixo fixa DOIS valores JUNTOS, nunca um sozinho:

    (contract_version da operação, golden digest representativo)

O propósito NÃO é afirmar que o request representativo aqui é a única
forma possível de request desta operação -- é provar que, se a
SEMÂNTICA normalizada do builder mudar (system prompt, papel/conteúdo/
ordem de mensagem, escolha de modelo/max_tokens/temperature), o digest
golden muda e o teste FALHA, forçando uma revisão explícita.

Se um destes testes falhar porque você mudou intencionalmente o prompt/
builder de uma operação:

    1. Isso é uma mudança de CONTRATO DE REQUEST daquela operação.
    2. Bump a constante `*_CONTRACT_VERSION` correspondente (ver
       app/orchestrator/orchestrator.py, app/debate/context.py,
       app/debate/claim_extraction.py, app/source_analysis/context.py,
       app/judge/context.py, app/editor/context.py).
    3. SÓ ENTÃO atualize o golden digest abaixo pra refletir o novo
       request.

Atualizar SOMENTE o digest esperado, sem bump de versão, sem revisar a
mudança semântica, é uma correção de teste INVÁLIDA -- esconde
exatamente a deriva que este teste existe pra detectar. Refatoração
pura que provavelmente produz o request normalizado IDÊNTICO não
precisa de bump nem toca estes testes (o digest simplesmente
permanece o mesmo).

Os valores golden abaixo foram gerados UMA vez, rodando os builders de
produção reais com os inputs representativos fixos definidos em cada
teste (nunca inventados/calculados à mão)."""

from __future__ import annotations

from app.debate.claim_extraction import (
    CLAIM_EXTRACTION_CONTRACT_VERSION,
    CLAIM_GROUPING_CONTRACT_VERSION,
    CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION,
    _build_extraction_request,
    _build_grouping_request,
    _build_reconciliation_request,
)
from app.debate.context import CRITIQUE_CONTRACT_VERSION, build_critique_requests
from app.editor.context import EDITOR_CONTRACT_VERSION, build_editor_request
from app.judge.context import JUDGE_CONTRACT_VERSION, build_judge_request
from app.models.domain import ClaimAssessment, JudgeVerdict
from app.models.request_provenance import compute_request_digest
from app.orchestrator.orchestrator import (
    INITIAL_RESPONSE_CONTRACT_VERSION,
    _build_initial_request,
)
from app.source_analysis.context import (
    SOURCE_ANALYSIS_CONTRACT_VERSION,
    build_source_analysis_request,
)
from tests.judge.fixtures import debate_result, model_response, raw_claim


# ---------------------------------------------------------------------------
# 1. initial_response_v1
# ---------------------------------------------------------------------------


def test_initial_response_contract_version_and_golden_digest():
    """F2 (review de independência) -- o request golden precisa vir da
    MESMA construção de produção (`_build_initial_request`, extraída de
    dentro de `Orchestrator.run()`), nunca duplicada à mão aqui -- se a
    semântica inline de produção mudar, este golden falha."""
    assert INITIAL_RESPONSE_CONTRACT_VERSION == "initial_response_v1"

    request = _build_initial_request(
        question="Qual a capital do Brasil?", max_output_tokens_per_call=1024
    )

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "15c2bf72c2afb9f4104898a5d531cd3d5878a4721df7c9f3c21c1083ddd0e920"
    )


# ---------------------------------------------------------------------------
# 2. critique_v1
# ---------------------------------------------------------------------------


def test_critique_contract_version_and_golden_digest():
    assert CRITIQUE_CONTRACT_VERSION == "critique_v1"

    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-1")
    requests = build_critique_requests(
        question="Qual a capital do Brasil?",
        current_claims=[c1],
        participants=["openai"],
        max_output_tokens_per_call=1024,
    )

    assert compute_request_digest(requests["openai"]) == (
        "completion-request-sha256-v1:"
        "aafcf11ae424bed25b357291535ba82d3f21efd4c77aaa66ec3b0095847c19b3"
    )


# ---------------------------------------------------------------------------
# 3. claim_extraction_v1
# ---------------------------------------------------------------------------


def test_claim_extraction_contract_version_and_golden_digest():
    assert CLAIM_EXTRACTION_CONTRACT_VERSION == "claim_extraction_v1"

    response = model_response(
        "openai", id="mr-fixed-1", response_text="Brasília é a capital do Brasil."
    )
    request = _build_extraction_request(response, None, 1024)

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "9eb9da22ff9aecf9995cb82a7a16621e4bc2b88afb006f168821255a6178c6d3"
    )


# ---------------------------------------------------------------------------
# 4. claim_grouping_v1
# ---------------------------------------------------------------------------


def test_claim_grouping_contract_version_and_golden_digest():
    assert CLAIM_GROUPING_CONTRACT_VERSION == "claim_grouping_v1"

    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-1")
    c2 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-2")
    request = _build_grouping_request([c1, c2], 1024)

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "87ab1588b68e3b0efa882e891eafcc74db2d8cf9bc77d03c61b53954d9d7320e"
    )


# ---------------------------------------------------------------------------
# 5. cross_round_claim_reconciliation_v1
# ---------------------------------------------------------------------------


def test_cross_round_reconciliation_contract_version_and_golden_digest():
    assert (
        CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION
        == "cross_round_claim_reconciliation_v1"
    )

    round1 = [
        raw_claim(
            "Brasília é a capital.",
            "resp-1",
            provider="openai",
            id="claim-r1",
            round_introduced=1,
        )
    ]
    round2 = [
        raw_claim(
            "A capital é Brasília.",
            "resp-2",
            provider="anthropic",
            id="claim-r2",
            round_introduced=2,
        )
    ]
    request = _build_reconciliation_request(round1, round2, 1024)

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "020dd9bd14f971109213364430fff2b756deff840b43f935b078ddcfcfef4da9"
    )


# ---------------------------------------------------------------------------
# 6. source_analysis_v1
# ---------------------------------------------------------------------------


def test_source_analysis_contract_version_and_golden_digest():
    assert SOURCE_ANALYSIS_CONTRACT_VERSION == "source_analysis_v1"

    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-6")
    request = build_source_analysis_request(
        "Brasília é a capital federal do Brasil.", [c1], 1024
    )

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "92f999cb41d94eee41e7b6ba455de7d796018d93c43e47cabe8742fbefba2a76"
    )


# ---------------------------------------------------------------------------
# 7. judge_v1
# ---------------------------------------------------------------------------


def test_judge_contract_version_and_golden_digest():
    assert JUDGE_CONTRACT_VERSION == "judge_v1"

    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-7")
    mr = model_response("openai", id="mr-fixed-7")
    dr = debate_result([c1], [mr])
    request = build_judge_request(
        question="Qual a capital do Brasil?",
        debate_result=dr,
        current_claims=[c1],
        max_output_tokens_per_call=1024,
    )

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "c3d7349da1442809e09a3176a464792b89a20e98e3ac6b121f89052cd07e60df"
    )


# ---------------------------------------------------------------------------
# 8. editor_v1
# ---------------------------------------------------------------------------


def test_editor_contract_version_and_golden_digest():
    assert EDITOR_CONTRACT_VERSION == "editor_v1"

    verdict = JudgeVerdict(
        id="verdict-fixed-8",
        evaluated_through_round=1,
        judge_model="claude-sonnet-5",
        claim_assessments=[
            ClaimAssessment(claim_id="claim-fixed-7", verdict="supported", explanation="ok")
        ],
        best_arguments_by={},
        debate_limitations=[],
        confidence=0.9,
        reasoning="justificativa",
    )
    request = build_editor_request("Qual a capital do Brasil?", verdict, 1024)

    assert compute_request_digest(request) == (
        "completion-request-sha256-v1:"
        "b73c0be0a7e0040d680d5e28bc2ccb0dc0117340ea381fbcb2fef8c57e575281"
    )


# ---------------------------------------------------------------------------
# Independência das 8 versões -- nenhuma colisão de nome
# ---------------------------------------------------------------------------


def test_all_eight_contract_versions_are_distinct():
    versions = {
        INITIAL_RESPONSE_CONTRACT_VERSION,
        CRITIQUE_CONTRACT_VERSION,
        CLAIM_EXTRACTION_CONTRACT_VERSION,
        CLAIM_GROUPING_CONTRACT_VERSION,
        CROSS_ROUND_CLAIM_RECONCILIATION_CONTRACT_VERSION,
        SOURCE_ANALYSIS_CONTRACT_VERSION,
        JUDGE_CONTRACT_VERSION,
        EDITOR_CONTRACT_VERSION,
    }
    assert len(versions) == 8
