"""
Contratos estruturados de I/O para as chamadas de extração e agrupamento de
claims (claim processor, Etapa 5).

Deliberadamente NÃO são `Claim` de domínio (app/models/domain.py) — são o
que a LLM tem permissão de produzir. A aplicação é quem converte isso em
`Claim` de verdade, depois de validar (ver app/debate/claim_extraction.py).
Nenhum destes schemas tem campo de id de `Claim` que a LLM poderia inventar
— `member_claim_ids`/`revises_claim_id` são REFERÊNCIAS a ids que a
aplicação já gerou e forneceu como contexto, nunca ids novos criados pela
LLM.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

_IO_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)

# Repair (Run02 claim-extraction exhaustion) -- teto RÍGIDO de claims por
# chamada de extração (uma ModelResponse por vez, ver
# `claim_extraction.py:extract_claims`). Única fonte de verdade deste
# número -- o prompt de extração (`_build_extraction_request`) referencia
# esta MESMA constante, nunca um "12" literal duplicado que poderia
# divergir do schema. Nunca aplicado aos schemas de agrupamento
# (`ClaimGroupingPartitionOutput`, partição exata de TODAS as claims) nem de
# reconciliação (`CrossRoundEquivalenceProposalOutput`, propostas esparsas):
# contratos estruturalmente diferentes, sem teto de cardinalidade.
MAX_EXTRACTED_CLAIMS = 12


class ExtractedClaimDraft(BaseModel):
    """Uma claim que a LLM identificou na resposta que está sendo
    processada. `revises_claim_id`, quando presente, é uma REFERÊNCIA a um
    id de claim do round anterior que esta claim corrige/contesta —
    sempre `None` no round 1 (não há claim anterior possível); no round 2+
    só pode referenciar um id que a aplicação explicitamente colocou no
    contexto daquela chamada de extração. A aplicação valida essa
    referência antes de converter em `Claim.parent_claim_id` — nunca aceita
    sem checar.

    `proposed_numeric_assertion` (Etapa 15) -- DELIBERADAMENTE `Any`, não
    o schema estrito (`ArithmeticAssertion`, app/debate/numeric_verification.py).
    Esta validação (`ExtractedClaimDraft`/`ClaimExtractionOutput`) é
    ATÔMICA sobre a lista inteira de claims da resposta — se este campo
    fosse estrito aqui, uma proposta numérica malformada de UMA claim
    derrubaria a extração de TODAS as outras claims válidas da mesma
    resposta (Repo Evidence Pack, Issue A). A validação estrita acontece
    SEPARADA, depois que a Claim já foi construída, em
    `numeric_verification.build_verification_attempt` — falha lá vira
    `invalid_proposal` (auditoria), nunca invalida a extração."""

    model_config = _IO_CONFIG

    text: str = Field(min_length=1)
    revises_claim_id: str | None = None
    proposed_numeric_assertion: Any = None


class ClaimExtractionOutput(BaseModel):
    """Resultado de uma chamada de extração — pode legitimamente conter 0
    claims (a resposta processada não afirmou nada extraível).

    Repair (Run02 claim-extraction exhaustion) -- `max_length=12`:
    cardinalidade de extração deixou de ser semanticamente ilimitada (ver
    `MAX_EXTRACTED_CLAIMS`/prompt em `claim_extraction.py`). Um output com
    13+ claims é um output REJEITADO pelo schema (`ValidationError` ->
    `MalformedClaimOutputError`, mesmo tratamento de qualquer outro JSON
    que não bata com o contrato) -- nunca aceito e depois cortado pra 12
    em silêncio: 13 claims é uma VIOLAÇÃO de contrato, não um excesso
    tolerado."""

    model_config = _IO_CONFIG

    claims: list[ExtractedClaimDraft] = Field(default_factory=list, max_length=MAX_EXTRACTED_CLAIMS)


class ClaimGroupingPartitionOutput(BaseModel):
    """claim_grouping_v4 -- saída do agrupamento intra-round: UMA partição
    exata das claims de entrada em `clusters` (lista de listas de ids).

    NÃO-DESTRUTIVO e apenas CONSULTIVO/auditável: um cluster de 2+ ids é uma
    PROPOSTA do modelo de que aquelas claims expressam a mesma proposição
    material -- nunca equivalência verificada, consenso nem verdade. Não
    existe `canonical_text`, nenhum texto sintetizado, nenhum
    `ungrouped_claim_ids` (claim sem equivalente = cluster de 1 id, válido e
    esperado). O resultado NÃO cria claim canônica, NÃO une suporte, NÃO
    supersede nem remove nenhuma claim original (ver `group_claims`).

    Forma: `{"clusters": [["id1", "id2"], ["id3"]]}`. Cluster vazio,
    chave extra ou tipo errado -> rejeitado pelo schema; cobertura
    exata-uma-vez é validada em `_parse_and_validate_grouping_partition`
    (app/debate/claim_extraction.py). Nenhuma partição malformada é
    normalizada. Os schemas `ClaimGroupProposal`/`ClaimGroupingOutput`
    abaixo continuam existindo SÓ pra reconciliação cross-round."""

    # Config PRÓPRIA (não `_IO_CONFIG`): SEM `str_strip_whitespace`. Os ids da
    # partição precisam casar EXATAMENTE com os ids fornecidos -- " id" ou
    # "id\n" NÃO podem virar "id" em silêncio (a resposta bruta e o parse
    # aceito divergiriam). Escopo: só este schema; os demais mantêm `_IO_CONFIG`.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    clusters: list[Annotated[list[str], Field(min_length=1)]]


class CrossRoundEquivalenceProposalOutput(BaseModel):
    """cross_round_claim_reconciliation_v2 -- saída da reconciliação
    cross-round: PROPOSTAS ESPARSAS e POSITIVAS de equivalência entre
    rodadas, nunca uma partição completa e nunca uma reescrita de claims.

    Forma: `{"equivalence_clusters": [["r1-id", "r2-id"], ...]}`.
    - `equivalence_clusters` é OBRIGATÓRIO; `[]` é válido ("nenhuma relação
      proposta");
    - cada cluster tem >= 2 ids (cluster vazio/unitário -> rejeitado pelo
      schema); cruzar rodadas, ids elegíveis e unicidade global são validados
      em `_parse_and_validate_reconciliation_equivalence`
      (app/debate/claim_extraction.py);
    - sem `canonical_text`, sem `ungrouped_claim_ids`, chave extra proibida.

    Uma claim NÃO mencionada significa SOMENTE "nenhuma relação foi
    proposta" -- nunca "verificada como não relacionada", nem contradição,
    nem claim nova, nem evidência negativa. Um cluster é uma PROPOSTA
    consultiva e auditável do modelo, nunca equivalência verificada nem
    consenso: NÃO cria claim, NÃO une nem transfere suporte, NÃO cria
    `parent_claim_id`/revisão e NÃO altera o conjunto de claims atuais.

    Config PRÓPRIA (não `_IO_CONFIG`): SEM `str_strip_whitespace` -- ids
    precisam casar EXATAMENTE com os elegíveis (nenhum " id"/"id\n" vira
    "id" em silêncio). Escopo: só este schema."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    equivalence_clusters: list[Annotated[list[str], Field(min_length=2)]]
