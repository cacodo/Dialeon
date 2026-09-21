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


# Feedback de retry (instrução da APLICAÇÃO, nunca texto do modelo): a 2ª
# tentativa recebe a regra violada, derivada só de um código fechado + ids/
# contagens que a aplicação já conhece. Nunca a mensagem da exceção (que pode
# conter ids inventados pelo modelo ou texto do validador de schema).
_MAX_FEEDBACK_IDS = 10

# Feedback GENÉRICO quando a saída anterior nem chegou a ser um JSON válido no
# schema esperado -- sem nenhum conteúdo da saída rejeitada.
MALFORMED_OUTPUT_FEEDBACK = (
    "A resposta anterior não seguiu o formato exigido: responda SOMENTE com um único JSON "
    "válido com os campos claim_assessments, best_arguments_by, debate_limitations, confidence "
    "e reasoning, sem nenhum texto fora do JSON e sem campos extras."
)


class MalformedJudgeOutputError(JudgeError):
    """JSON inválido, ou JSON válido que não bate com o schema esperado."""

    def to_feedback(self) -> str:
        return MALFORMED_OUTPUT_FEEDBACK


def _format_ids(ids: tuple[str, ...]) -> str:
    shown = ", ".join(ids[:_MAX_FEEDBACK_IDS])
    extra = len(ids) - _MAX_FEEDBACK_IDS
    return f"{shown} (e mais {extra})" if extra > 0 else shown


class InconsistentJudgeReferenceError(JudgeError):
    """JSON estruturalmente válido, mas com referências que a aplicação não
    consegue aceitar: claim_id desconhecido, duplicado, provider
    desconhecido em best_arguments_by, ou completude de claim_assessments
    violada (claim atual omitida, ou avaliação extra sobrando).

    Além da mensagem (persistida como `parse_error_message`), carrega uma
    descrição ESTRUTURADA: `code` fechado, `count` (quantos itens violam) e
    `known_ids` (SÓ ids/providers que a aplicação já conhece -- nunca um id
    inventado pelo modelo), `required_count` (nº de claims atuais)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "inconsistent_references",
        count: int = 0,
        known_ids: tuple[str, ...] = (),
        required_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.count = count
        self.known_ids = known_ids
        self.required_count = required_count

    def to_feedback(self) -> str:
        n = self.required_count
        if self.code == "duplicate_claim_id":
            named = f" Ids repetidos: {_format_ids(self.known_ids)}." if self.known_ids else ""
            return (
                f"Na resposta anterior, {self.count} claim_id(s) apareceram mais de uma vez em "
                f"claim_assessments.{named} Cada claim atual deve aparecer EXATAMENTE UMA vez: "
                f"retorne uma única avaliação para cada uma das {n} claims atuais, sem repetir ids."
            )
        if self.code == "unknown_claim_id":
            return (
                f"Na resposta anterior, {self.count} claim_id(s) em claim_assessments não "
                f"correspondem a nenhuma claim atual fornecida. Use SOMENTE os ids exatos das "
                f"{n} claims atuais, uma avaliação para cada."
            )
        if self.code == "missing_claim_id":
            return (
                f"Na resposta anterior, {self.count} claim(s) atual(is) ficaram sem avaliação: "
                f"{_format_ids(self.known_ids)}. Avalie CADA uma das {n} claims atuais "
                "exatamente uma vez (use unresolved se não conseguir decidir)."
            )
        if self.code == "unknown_provider":
            valid = f" Providers válidos: {_format_ids(self.known_ids)}." if self.known_ids else ""
            return (
                f"Na resposta anterior, best_arguments_by referenciou {self.count} provider(s) "
                f"que não participaram do debate.{valid} Use somente providers válidos "
                "(ou deixe best_arguments_by vazio)."
            )
        return (
            "A resposta anterior violou uma regra de referências. Avalie cada claim atual "
            "exatamente uma vez, usando os ids exatos fornecidos."
        )
