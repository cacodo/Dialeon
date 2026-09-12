"""
Exceções da camada de "structured output" do claim processor — extração e
agrupamento de claims. Deliberadamente SEPARADAS das exceções de transporte
(`app/providers/errors.py`, usadas só dentro do LLMProvider): erro HTTP/
timeout/rate limit e output semanticamente inválido são classes de erro
diferentes, com retries diferentes, em camadas diferentes.

- ClaimProcessingError: base.
- MalformedClaimOutputError: a chamada teve sucesso de transporte, mas o
  texto retornado não é JSON válido, ou é JSON válido que não bate com o
  schema esperado (ClaimExtractionOutput/ClaimGroupingOutput).
- InconsistentClaimReferenceError: JSON estruturalmente válido, mas
  referencia ids que a aplicação não reconhece como válidos no contexto
  daquela chamada (id inexistente, de round errado, fora do conjunto
  fornecido como contexto, duplicado entre grupos, ou deixa alguma claim
  bruta sem cobertura — violação de completude).
"""

from __future__ import annotations


class ClaimProcessingError(Exception):
    """Base de todas as exceções desta camada."""


class MalformedClaimOutputError(ClaimProcessingError):
    """JSON inválido, ou JSON válido que não bate com o schema esperado."""


class InconsistentClaimReferenceError(ClaimProcessingError):
    """JSON estruturalmente válido, mas com referências que a aplicação não
    consegue aceitar (id inexistente, fora de contexto, duplicado, ou
    completude violada — alguma claim bruta ficou sem cobertura)."""
