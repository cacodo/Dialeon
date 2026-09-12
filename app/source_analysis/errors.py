"""
Exceção da camada de "structured output" do source analyzer (Etapa 16).
Deliberadamente sem uma "InconsistentSourceAnalysisReferenceError" — ver
docstring de `app/source_analysis/schemas.py`: referências inconsistentes
(claim_id desconhecido/duplicado/omitido) viram REJEIÇÃO POR ENTRADA, não
uma exceção de nível de attempt.
"""

from __future__ import annotations


class SourceAnalysisError(Exception):
    """Base."""


class MalformedSourceAnalysisOutputError(SourceAnalysisError):
    """A chamada teve sucesso de transporte, mas o texto retornado não é
    JSON válido, ou é JSON válido que não bate nem com o shape MÍNIMO
    esperado (`{"claim_relations": [...]}`, uma lista de objetos)."""
