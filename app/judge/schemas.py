"""
Contrato estruturado de I/O do Judge (Etapa 6).

Deliberadamente NÃO é `ClaimAssessment`/`JudgeVerdict` de domínio
(app/models/domain.py) — é o que a LLM tem permissão de produzir. A
aplicação (app/judge/single_judge.py) converte isso em domínio depois de
validar: referência a claim_id só pode apontar pra uma claim atual
conhecida (nunca id novo inventado pela LLM), e a cobertura precisa ser
exata (nenhuma claim atual pode ficar sem avaliação, nenhuma avaliação
extra sobrando).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_IO_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ClaimAssessmentDraft(BaseModel):
    """Uma avaliação que a LLM propôs para UMA claim atual.

    `claim_id` é sempre uma REFERÊNCIA a um id que a aplicação já colocou
    no contexto (a lista de claims atuais) — nunca um id novo. Os cinco
    valores de `verdict` são os únicos aceitos; `"rejected"` não significa
    "provado externamente falso" (não existe Verifier nesta etapa), só
    "o Judge rejeita com base no debate disponível"."""

    model_config = _IO_CONFIG

    claim_id: str = Field(min_length=1)
    verdict: Literal["supported", "partially_supported", "rejected", "conflicting", "unresolved"]
    explanation: str = Field(min_length=1)


class JudgeOutput(BaseModel):
    """Resultado de uma chamada de julgamento. `claim_assessments` deve
    cobrir EXATAMENTE o conjunto de claims atuais fornecido — validado
    depois do parse (não pelo Pydantic, que não tem acesso a esse
    conjunto), em app/judge/single_judge.py."""

    model_config = _IO_CONFIG

    claim_assessments: list[ClaimAssessmentDraft] = Field(default_factory=list)
    best_arguments_by: dict[str, str] = Field(default_factory=dict)
    debate_limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
