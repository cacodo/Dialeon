"""
Resultado por-claim do source analyzer — Etapa 16.

`SourceClaimAnalysisResult` é uma união discriminada por `kind`, NÃO uma
hierarquia de classes/status genérico:

- `ValidSourceRelation`: uma conclusão epistêmica REAL sobre a fonte
  (supports/contradicts/unresolved) -- sempre corresponde a uma claim
  corrente REAL, sempre com excerpt mecanicamente verificado (nunca
  confiado da LLM) quando a relação é supports/contradicts.
- `RejectedSourceEntry`: NÃO é uma relação epistêmica. Nunca vira
  "unresolved" -- unresolved é uma resposta válida do modelo dizendo que
  a fonte não decide a claim; rejected é a aplicação dizendo que não
  pôde confiar no que foi devolvido (omitido, duplicado, desconhecido,
  ou malformado). Misturar os dois destruiria PROVENANCE != VERDICT.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CONFIG = ConfigDict(frozen=True, extra="forbid")


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ValidSourceRelation(BaseModel):
    model_config = _CONFIG

    kind: Literal["relation"] = "relation"
    id: str = Field(default_factory=_new_id)
    claim_id: str = Field(min_length=1)
    relation: Literal["supports", "contradicts", "unresolved"]
    # Verbatim, fatiado do source_text ORIGINAL pela aplicação -- nunca o
    # texto que a LLM "disse" que citou. None quando relation="unresolved"
    # (nada a citar).
    excerpt: str | None = None
    excerpt_start: int | None = None
    excerpt_end: int | None = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _excerpt_matches_relation(self) -> ValidSourceRelation:
        if self.relation == "unresolved":
            if self.excerpt is not None or self.excerpt_start is not None or self.excerpt_end is not None:
                raise ValueError(
                    "relation='unresolved' não deve ter excerpt/excerpt_start/excerpt_end "
                    "-- nada a citar quando a fonte não decide a claim"
                )
        else:
            if self.excerpt is None or self.excerpt_start is None or self.excerpt_end is None:
                raise ValueError(
                    f"relation={self.relation!r} exige excerpt/excerpt_start/excerpt_end "
                    "preenchidos -- uma relação sem trecho citável não é verificável"
                )
            if self.excerpt_start < 0 or self.excerpt_end <= self.excerpt_start:
                raise ValueError("excerpt_start/excerpt_end precisam formar um range válido")
        return self


class RejectedSourceEntry(BaseModel):
    """NÃO é uma relação epistêmica -- ver docstring do módulo."""

    model_config = _CONFIG

    kind: Literal["rejected"] = "rejected"
    id: str = Field(default_factory=_new_id)
    # None quando a rejeição é por claim_id desconhecido/não-corrente --
    # não há claim real pra apontar (nunca fabricamos uma FK falsa).
    claim_id: str | None = None
    reason: Literal["omitted_by_model", "duplicate_claim_id", "invalid_entry"]
    # JSON-safe, nunca objeto Python arbitrário -- None só quando não há
    # fragmento nenhum (caso "omitted_by_model": a claim nunca apareceu).
    raw_entry: Any = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _omitted_has_no_raw_entry(self) -> RejectedSourceEntry:
        if self.reason == "omitted_by_model" and self.raw_entry is not None:
            raise ValueError(
                "reason='omitted_by_model' não deve ter raw_entry -- não existe "
                "fragmento nenhum quando a claim nunca foi endereçada"
            )
        return self


SourceClaimAnalysisResult = Annotated[
    Union[ValidSourceRelation, RejectedSourceEntry], Field(discriminator="kind")
]
