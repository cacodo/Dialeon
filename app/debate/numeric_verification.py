"""
Verificação numérica determinística — Etapa 15.

`DETERMINISTIC RESULT != MODEL OPINION`. Escopo v1 fechado: EXATAMENTE
uma operação binária (+, -, *, /) por asserção proposta — sem expressões
aninhadas, sem AST, sem parser de expressão arbitrária. A gramática
inteira é a própria validação do Pydantic sobre `ArithmeticAssertion`
(closed shape) — nenhuma dependência de parser externo, nenhum `eval`/
`exec`, nenhum lookup de função por string controlada pela LLM (o
operador é `Literal["+", "-", "*", "/"]`, despachado por `if/elif`
explícito, nunca por nome).

Isolamento de extração (achado do Repo Evidence Pack): a LLM propõe a
asserção como `Any` (campo permissivo) dentro de `ExtractedClaimDraft`
(app/debate/schemas.py) — isso nunca pode invalidar a Claim em si.
Validação ESTRITA acontece aqui, isolada, DEPOIS que a Claim já foi
construída — uma proposta malformada vira `invalid_proposal` (registro
de auditoria), nunca destrói a extração.

Aritmética exata: strings decimais limitadas → `Decimal` → `Fraction`
(nunca `float` em nenhum ponto do cálculo) → comparação de igualdade
exata de `Fraction`. `1/3` comparado a asserted_result="0.33" É
`contradicts` — sem tolerância, sem arredondamento implícito.

Placement: verificação roda sobre a Claim BRUTA (recém-extraída),
sobre a claim extraída, que é a claim autoritativa (não há mais agrupamento
semântico na execução corrente) — nunca sobre uma claim canônica de fusão
histórica, que tinha texto sintetizado pela LLM e nunca teve uma proposta
numérica própria. `judge/context.py` é quem decide como expor
isso por nó de lineage.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# --- Limites de segurança (Repo Evidence Pack, seção H — fechados) ---
# Uma única operação binária já limita o crescimento de numerador/
# denominador do Fraction a uma multiplicação/divisão de dois valores
# já limitados — sem precisar de bound adicional pra "profundidade" de
# expressão, porque não existe expressão aninhada em v1.
_MAX_SIGNIFICANT_DIGITS = 30
_MAX_MAGNITUDE = Decimal("1e18")

# Sintaxe decimal deliberadamente estreita: sinal opcional, dígitos
# inteiros (sem zero à esquerda, exceto "0" sozinho), ponto decimal
# opcional com dígitos depois. SEM notação científica (nenhum "e"/"E"),
# sem espaços, sem vírgula. Rejeitar sci notation é uma escolha
# deliberada do v1 (mais simples de raciocinar sobre limite de
# dígitos) — testado explicitamente.
_DECIMAL_STRING_RE = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")

# Patch de revisão independente (Issue 2): limite SEPARADO de comprimento
# TOTAL do literal (caracteres), checado ANTES de qualquer regex/Decimal.
# `_MAX_SIGNIFICANT_DIGITS` sozinho não bloqueia um payload como
# "0.000...(milhares de zeros)...0001" -- isso conta como 1 dígito
# significativo (zeros à esquerda são descartados por `lstrip("0")`),
# mas a STRING em si pode ser arbitrariamente grande, e nada garante que
# `Decimal(raw)`/`Fraction(...)` sejam baratos pra uma string desse
# tamanho. 64 caracteres é generoso o bastante pra qualquer claim
# numérica real (30 dígitos significativos + sinal + ponto já cabem
# várias vezes) e pequeno o bastante pra impedir que padding de zeros
# vaze o orçamento de dígitos significativos.
_MAX_LITERAL_LENGTH = 64


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidNumericLiteral(ValueError):
    """Literal decimal fora da sintaxe/limites aceitos -- vira
    `invalid_proposal`, nunca `computation_failed` (a operação nem
    chega a rodar)."""


def parse_bounded_decimal(raw: str) -> Decimal:
    """Único ponto de entrada de texto->número deste módulo. Nunca usa
    `float` em nenhum passo -- só `Decimal`, depois convertido a
    `Fraction` por quem chama."""
    if not isinstance(raw, str):
        raise InvalidNumericLiteral(f"literal precisa ser string, recebido {type(raw).__name__}")
    # Checagem MAIS barata primeiro, antes de regex/Decimal -- bloqueia
    # payload de padding (ex.: zeros à esquerda em excesso) que passaria
    # ileso pelo limite de dígitos SIGNIFICATIVOS abaixo.
    if len(raw) > _MAX_LITERAL_LENGTH:
        raise InvalidNumericLiteral(
            f"literal excede o comprimento total máximo de {_MAX_LITERAL_LENGTH} caracteres"
        )
    if not _DECIMAL_STRING_RE.match(raw):
        raise InvalidNumericLiteral(
            f"sintaxe decimal inválida ou não suportada (sem notação científica): {raw!r}"
        )
    significant_digits = raw.replace("-", "").replace(".", "").lstrip("0") or "0"
    if len(significant_digits) > _MAX_SIGNIFICANT_DIGITS:
        raise InvalidNumericLiteral(
            f"literal excede o máximo de {_MAX_SIGNIFICANT_DIGITS} dígitos significativos"
        )
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:  # defensivo -- a regex já deveria ter barrado
        raise InvalidNumericLiteral(f"literal decimal inválido: {raw!r}") from exc
    if abs(value) >= _MAX_MAGNITUDE:
        raise InvalidNumericLiteral(
            f"magnitude {value} excede o limite permitido (< {_MAX_MAGNITUDE})"
        )
    return value


class ArithmeticAssertion(BaseModel):
    """Única forma de operação suportada em v1 -- não uma AST genérica.
    Contrato fechado (handoff Stage 15): campos são STRINGS decimais
    (nunca número JSON) -- o payload externo já passou por
    `json.loads()` antes de chegar aqui, então um valor numérico JSON
    já teria perdido a representação lexical original ao virar float
    na desserialização padrão; exigir string preserva o texto exato
    que a LLM emitiu.
    """

    model_config = _CONFIG

    kind: Literal["arithmetic"] = "arithmetic"
    left: str = Field(min_length=1)
    operator: Literal["+", "-", "*", "/"]
    right: str = Field(min_length=1)
    asserted_result: str = Field(min_length=1)

    @field_validator("left", "right", "asserted_result")
    @classmethod
    def _bounded_decimal_string(cls, value: str) -> str:
        parse_bounded_decimal(value)  # levanta InvalidNumericLiteral (subclasse ValueError)
        return value


_ASSERTION_ADAPTER: TypeAdapter[ArithmeticAssertion] = TypeAdapter(ArithmeticAssertion)


class DeterministicVerificationAttempt(BaseModel):
    """Provenance/attempt determinística — Etapa 15. Tipo pequeno e
    DISTINTO de `EvidenceRef` (evidência externa, Fase 5): formas de
    provenance genuinamente diferentes não devem fingir ser a mesma
    coisa (Repo Evidence Pack, item B). Nunca reutiliza/generaliza
    `EvidenceRef`.

    `claim_id` sempre aponta pra Claim BRUTA que produziu a proposta —
    nunca uma claim canônica de fusão (que nunca é verificada
    diretamente, ver módulo docstring).
    """

    model_config = _CONFIG

    id: str = Field(default_factory=_new_id)
    claim_id: str = Field(min_length=1)
    state: Literal["invalid_proposal", "computation_failed", "supports", "contradicts"]

    # Só quando state="invalid_proposal" -- payload cru (JSON-safe,
    # veio de json.loads()) que foi rejeitado, pra auditoria mostrar
    # exatamente o que a LLM propôs.
    raw_proposal: Any = None

    # Só quando state != "invalid_proposal" -- a asserção normalizada
    # que passou na validação estrita.
    assertion: ArithmeticAssertion | None = None

    # Só quando state em (supports, contradicts) -- forma canônica
    # exata de Fraction (ex. "1/3", "30"), nunca aproximação decimal.
    computed_result: str | None = None

    created_at: datetime = Field(default_factory=_now)


def _to_fraction(literal: str) -> Fraction:
    # Literal já validado (bounded decimal string) no momento em que
    # ArithmeticAssertion foi construído -- Decimal->Fraction é exato,
    # sem passar por float em nenhum ponto.
    return Fraction(Decimal(literal))


def _evaluate(assertion: ArithmeticAssertion) -> tuple[Literal["computation_failed", "supports", "contradicts"], str | None]:
    left = _to_fraction(assertion.left)
    right = _to_fraction(assertion.right)
    asserted = _to_fraction(assertion.asserted_result)

    if assertion.operator == "+":
        computed = left + right
    elif assertion.operator == "-":
        computed = left - right
    elif assertion.operator == "*":
        computed = left * right
    else:  # "/"
        if right == 0:
            return "computation_failed", None
        computed = left / right

    state: Literal["supports", "contradicts"] = "supports" if computed == asserted else "contradicts"
    return state, str(computed)


def build_verification_attempt(
    claim_id: str, raw_proposal: Any
) -> DeterministicVerificationAttempt | None:
    """Ponto de entrada único chamado por `claim_extraction.py`.

    `raw_proposal is None` -> `never_attempted`: devolve `None`, NENHUM
    registro é criado (ausência de registro É o estado -- nunca
    persistido, nunca confundido com um estado real).
    """
    if raw_proposal is None:
        return None

    try:
        assertion = _ASSERTION_ADAPTER.validate_python(raw_proposal)
    except ValidationError:
        return DeterministicVerificationAttempt(
            claim_id=claim_id,
            state="invalid_proposal",
            raw_proposal=raw_proposal,
            assertion=None,
            computed_result=None,
        )

    state, computed_result = _evaluate(assertion)
    return DeterministicVerificationAttempt(
        claim_id=claim_id,
        state=state,
        raw_proposal=None,
        assertion=assertion,
        computed_result=computed_result,
    )
