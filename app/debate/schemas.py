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

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_IO_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)

# Repair (Run02 claim-extraction exhaustion) -- teto RÍGIDO de claims por
# chamada de extração (uma ModelResponse por vez, ver
# `claim_extraction.py:extract_claims`). Única fonte de verdade deste
# número -- o prompt de extração (`_build_extraction_request`) referencia
# esta MESMA constante, nunca um "12" literal duplicado que poderia
# divergir do schema. Nunca aplicado a `ClaimGroupingOutput` (agrupamento/
# reconciliação): aquele schema exige cobertura de TODAS as claims brutas
# dadas como entrada, um contrato estruturalmente diferente que não tem
# teto de cardinalidade -- ver docstring de `ClaimGroupingOutput`.
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


class ClaimGroupProposal(BaseModel):
    """Um grupo de claims brutas que a LLM considera semanticamente
    equivalentes. `member_claim_ids` exige no mínimo 2: um "grupo" de 1
    membro não funde nada — claims que não têm equivalente ficam em
    `ClaimGroupingOutput.ungrouped_claim_ids`, não aqui."""

    model_config = _IO_CONFIG

    member_claim_ids: list[str] = Field(min_length=2)
    canonical_text: str = Field(min_length=1)


class RawClaimGroupProposal(BaseModel):
    """FRONTEIRA de parse EXCLUSIVA do agrupamento intra-round
    (claim_grouping_v3) -- NUNCA o schema APLICADO. Idêntica a
    `ClaimGroupProposal` exceto por `member_claim_ids` aceitar 1 membro:
    um "grupo" de 1 id é a representação errada, mas inequívoca, de
    "esta claim não tem equivalente" (replay v2 real: 6 claims postas em
    grupos unitários com `ungrouped_claim_ids` vazio). O
    `normalize`-and-validate de `claim_extraction.py` converte cada
    grupo unitário em id ungrouped ANTES de qualquer claim canônica
    existir; o `canonical_text` de um grupo unitário é descartado. Grupo
    com 0 membros continua rejeitado (`min_length=1`)."""

    model_config = _IO_CONFIG

    member_claim_ids: list[str] = Field(min_length=1)
    canonical_text: str = Field(min_length=1)


class RawClaimGroupingOutput(BaseModel):
    """Forma bruta de uma resposta de agrupamento intra-round, ANTES da
    normalização de grupos unitários -- ver `RawClaimGroupProposal`.
    Mesmos campos/config de `ClaimGroupingOutput`, então qualquer outro
    formato inválido (chave extra, tipo errado, JSON truncado) continua
    rejeitado exatamente como antes. Só o agrupamento intra-round usa
    isto; reconciliação continua parseando `ClaimGroupingOutput`
    diretamente (grupo unitário segue rejeitado por contrato)."""

    model_config = _IO_CONFIG

    groups: list[RawClaimGroupProposal] = Field(default_factory=list)
    ungrouped_claim_ids: list[str] = Field(default_factory=list)


class ClaimGroupingOutput(BaseModel):
    """Resultado de uma chamada de agrupamento. A aplicação valida, depois
    de parsear isto, que a união de `groups` + `ungrouped_claim_ids` é
    EXATAMENTE igual ao conjunto de claims brutas dadas como entrada — sem
    sobra, sem falta, sem duplicata entre grupos (ver
    app/debate/claim_extraction.py:_validate_grouping_references)."""

    model_config = _IO_CONFIG

    groups: list[ClaimGroupProposal] = Field(default_factory=list)
    ungrouped_claim_ids: list[str] = Field(default_factory=list)
