"""
Contrato estruturado de I/O do source analyzer (Etapa 16).

Isolamento crítico (mesmo achado de Stage 15, Issue A): `claim_relations`
é `list[dict[str, Any]]` DELIBERADAMENTE solto no nível do parse de topo
-- uma entrada malformada de UMA claim não pode derrubar as demais nem
fazer o `SourceAnalysisOutput` inteiro virar `malformed`. Cada dict é
validado individualmente contra `SourceRelationDraft`, depois, isolado
(ver `app/source_analysis/analyzer.py`) -- exatamente o mesmo princípio
que salvou `ExtractedClaimDraft.proposed_numeric_assertion` de derrubar
extração de claims válidas.

Diferença deliberada do Judge (`JudgeOutput`/`_parse_and_validate`):
aqui NÃO existe verificação de completude/duplicata/referência no nível
do parse -- essas checagens viram REJEIÇÃO POR ENTRADA
(`RejectedSourceEntry`), nunca rejeição do output inteiro. Source
analysis é informação suplementar audit-only, não uma peça de
julgamento com obrigação de completude atômica como o Judge tem.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_IO_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceRelationDraft(BaseModel):
    """Uma relação claim↔fonte que a LLM propôs para UM claim_id.

    `excerpt`: obrigatório quando `relation` é `supports`/`contradicts`
    (uma relação epistêmica sem trecho citável não é verificável
    mecanicamente); ignorado/descartado quando `relation="unresolved"`
    (nada a citar quando a fonte não decide a claim) -- a aplicação NUNCA
    confia neste texto como prova por si só, sempre verifica como
    substring exata do `source_text` original antes de aceitar.
    """

    model_config = _IO_CONFIG

    claim_id: str = Field(min_length=1)
    relation: Literal["supports", "contradicts", "unresolved"]
    excerpt: str | None = None


class SourceAnalysisOutput(BaseModel):
    """`claim_relations` deliberadamente `list[dict]`, não
    `list[SourceRelationDraft]` -- ver docstring do módulo."""

    model_config = _IO_CONFIG

    claim_relations: list[dict[str, Any]] = Field(default_factory=list)
