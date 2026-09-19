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
teste (nunca inventados/calculados à mão).

Repair (Run02 claim-extraction exhaustion; Finding C da revisão
adversarial) -- exceção documentada à regra acima: `CompletionRequest`
ganhou um campo NOVO (`minimal_reasoning`, ver
app/models/provider_models.py) que `_canonical_completion_request_payload`
(app/models/request_provenance.py) agora inclui no digest de TODA
operação -- inclusive as 7 que nunca setam esse campo (sempre `False`
pra elas). Como isso é uma mudança na FORMA de canonicalização (não no
CONTEÚDO semântico de 7 das 8 operações), o prefixo do digest avançou
de `completion-request-sha256-v1:` pra `completion-request-sha256-v2:`
(ver `REQUEST_DIGEST_PREFIX_V1`/`REQUEST_DIGEST_PREFIX_V2`,
app/models/request_provenance.py) -- v1 preservado EXCLUSIVAMENTE como
formato histórico reconhecido na validação/leitura (nenhuma linha já
persistida precisa de migração), nunca mais recomputado. Isso muda os 8
goldens abaixo SIMULTANEAMENTE (todos passam a usar o prefixo v2), mesmo
que só `claim_extraction_v1` -> `claim_extraction_v2` (a `contract_version`
da OPERAÇÃO, um conceito inteiramente separado do prefixo de digest)
tenha avançado: as outras 7 constantes de `contract_version` NÃO
avançaram, porque seus requests continuam semanticamente idênticos
(prompt/mensagens/model/max_tokens/temperature byte-a-byte iguais) -- só
a FORMA de canonicalização evoluiu, uma consequência estrutural de
adicionar um campo ao schema compartilhado, nunca uma mudança de
contrato dessas 7 operações. Ver docstring de
`_canonical_completion_request_payload` pra o mesmo raciocínio do lado
da implementação.

(Nota posterior: `claim_grouping_v1` -> `claim_grouping_v2` avançou depois,
por uma mudança de política de request INDEPENDENTE desta -- ver a seção 4
abaixo. O parágrafo acima descreve só o que valia NESTE patch.)"""

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
        "completion-request-sha256-v2:"
        "813facc85b51d8af9e5d3b4e0f115aa52b23c7ffdd32e9b18aec9df27c2d9148"
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
        "completion-request-sha256-v2:"
        "c5f0d44c71ccc6fc5064814adf103d99a5104d854c57fc54efa0dabc58baf830"
    )


# ---------------------------------------------------------------------------
# 3. claim_extraction_v2
# ---------------------------------------------------------------------------


def test_claim_extraction_contract_version_and_golden_digest():
    assert CLAIM_EXTRACTION_CONTRACT_VERSION == "claim_extraction_v2"

    response = model_response(
        "openai", id="mr-fixed-1", response_text="Brasília é a capital do Brasil."
    )
    request = _build_extraction_request(response, None, 1024)

    assert compute_request_digest(request) == (
        "completion-request-sha256-v2:"
        "3e6362ee2cfb1ac2dc6b9912071d5bf0629f9b6b852c031a7351222d743c6d0e"
    )


# ---------------------------------------------------------------------------
# 4. claim_grouping_v4
# ---------------------------------------------------------------------------
#
# Intencional (governança acima): v1 -> v2 (`minimal_reasoning=True`) -> v3
# (prompt clarificado + normalização de grupo unitário) -> v4 (REDESENHO
# não-destrutivo: partição consultiva em `clusters`, sem canonical_text). Os
# goldens v1/v2/v3 desta MESMA fixture (`35b8b9af...` / `662adfe8...` /
# `1af61f27...`) seguem documentados em
# tests/debate/test_grouping_reasoning_policy.py, derivados dos prompts
# históricos.


def test_claim_grouping_contract_version_and_golden_digest():
    assert CLAIM_GROUPING_CONTRACT_VERSION == "claim_grouping_v4"

    c1 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-1")
    c2 = raw_claim("Brasília é a capital.", "resp-1", provider="openai", id="claim-fixed-2")
    request = _build_grouping_request([c1, c2], 1024)

    assert compute_request_digest(request) == (
        "completion-request-sha256-v2:"
        "2e670987ee5c6677c867cdf31b617cec27aa81c5dc3aed8cf2b32d16ad8cd696"
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
        "completion-request-sha256-v2:"
        "84a708fdab3129f357a67b3563b4b366d5e44f517c56869fe11b866b4e6b6dcb"
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
        "completion-request-sha256-v2:"
        "70fb04c9a0847261dedb3cd4747f7a334970f456a2cecb12604ff2c006307afc"
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
        "completion-request-sha256-v2:"
        "feac2b539e5c4d30af81fac9cf5d25ea2f7f924c875ec318bfb341ad21fb2497"
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
        "completion-request-sha256-v2:"
        "ea6788cb44925c66430dd4f477c80743c6ce1ec7f7e5ee213eb93790faae6875"
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
