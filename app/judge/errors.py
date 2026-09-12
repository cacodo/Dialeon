"""
Exceções da camada de "structured output" do Judge (Etapa 6). Deliberadamente
SEPARADAS das exceções de transporte (`app/providers/errors.py`, internas ao
`LLMProvider`) e SEPARADAS das exceções do claim processor
(`app/debate/errors.py`) — mesma disciplina de camadas independentes já
estabelecida na Etapa 5, agora replicada aqui sem acoplamento entre os dois
módulos.

- JudgeError: base.
- MalformedJudgeOutputError: a chamada teve sucesso de transporte, mas o
  texto retornado não é JSON válido, ou é JSON válido que não bate com o
  schema esperado (JudgeOutput).
- InconsistentJudgeReferenceError: JSON estruturalmente válido, mas com
  referências que a aplicação não aceita — claim_id inexistente, duplicado,
  provider desconhecido em best_arguments_by, OU falha de completude
  (alguma claim atual não avaliada, ou avaliação extra sobrando). Falha de
  completude é a MESMA categoria de referência inconsistente, não um quarto
  tipo de erro.
"""

from __future__ import annotations


class JudgeError(Exception):
    """Base de todas as exceções desta camada."""


class MalformedJudgeOutputError(JudgeError):
    """JSON inválido, ou JSON válido que não bate com o schema esperado."""


class InconsistentJudgeReferenceError(JudgeError):
    """JSON estruturalmente válido, mas com referências que a aplicação não
    consegue aceitar: claim_id desconhecido, duplicado, provider
    desconhecido em best_arguments_by, ou completude de claim_assessments
    violada (claim atual omitida, ou avaliação extra sobrando)."""
