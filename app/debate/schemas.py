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
# divergir do schema. (Os schemas de saída de agrupamento e de reconciliação
# foram removidos junto com essas operações -- ver
# app/debate/claim_extraction.py.)
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
