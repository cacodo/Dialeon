"""
Contrato de complexidade de fragmentos de auditoria brutos (repair M2).

Dois campos de domínio guardam, só pra auditoria, um fragmento JSON
devolvido pelo provider sem schema estrito:

- `DeterministicVerificationAttempt.raw_proposal` (proposta numérica
  rejeitada, `state="invalid_proposal"`);
- `RejectedSourceEntry.raw_entry` (entrada de SourceAnalysis rejeitada).

Achado real (M2): uma resposta de extração válida, dentro de 4096 tokens,
com `proposed_numeric_assertion` aninhado ~970 níveis passava pelo parse
e pelo schema, virava `invalid_proposal`, e fazia o `json.dumps` do bind
JSON do SQLAlchemy estourar `RecursionError` DENTRO da transação terminal
-- rollback inteiro, a run ficava `running` e todo attempt/usage/custo já
incorrido sumia do histórico. O limite real do runtime varia com a versão
do Python, a forma da estrutura e a profundidade de pilha já consumida no
ponto de serialização (encoder, decoder, serializer do Pydantic), então
ele NUNCA é a autoridade aqui.

A aplicação define o próprio contrato, fixo e determinístico:

- profundidade de containers <= `MAX_AUDIT_FRAGMENT_DEPTH` (32). Formas
  legítimas desses campos têm profundidade 1-2 (um objeto de asserção;
  uma entrada; uma lista de entradas duplicadas). O limite mais baixo
  medido fora da aplicação é o do serializer do pydantic-core: 255
  níveis CONTANDO os modelos que envolvem o fragmento (`model_dump(mode=
  "json")`/resposta HTTP do resultado inteiro falham acima disso, em
  qualquer versão do Python). O `json.dumps`/`json.loads` do CPython
  3.11 falhou entre ~930 e ~990 níveis dependendo da pilha já consumida
  (3.13/3.14 toleram milhares). 32 fica ~8x abaixo do menor deles, com
  folga pra qualquer aninhamento de modelo em volta;
- total de valores <= `MAX_AUDIT_FRAGMENT_NODES` (10.000). Largura não
  causa recursão, mas o limite mantém o trabalho desta checagem e o
  tamanho da linha limitados independentemente do input;
- só os tipos do modelo de dados JSON, por tipo EXATO (`dict` com chave
  `str`, `list`, `str`, `int`, `bool`, `float` finito, `None`). `NaN`/
  `Infinity` (aceitos por `json.loads` por padrão) não são JSON e
  quebrariam a resposta HTTP estrita; qualquer outro tipo Python é
  ambíguo demais pra ser tratado como "o que o provider devolveu".

Um fragmento fora do contrato NÃO é persistido: o campo estruturado fica
`None` e o campo irmão `*_omitted_reason` registra POR QUÊ, com um motivo
estável da aplicação (nunca texto de exceção). O texto bruto completo do
provider continua no attempt correspondente (`raw_output_text`), que é a
fonte autoritativa do que o modelo realmente devolveu.

A checagem é ITERATIVA (pilha explícita), para cedo e nunca executa nada
do conteúdo -- não pode ela mesma sofrer o `RecursionError` que evita.
"""

from __future__ import annotations

import math
from typing import Any, Literal

MAX_AUDIT_FRAGMENT_DEPTH = 32
MAX_AUDIT_FRAGMENT_NODES = 10_000

AuditFragmentOmittedReason = Literal["complexity_limit_exceeded", "non_json_value"]


def audit_fragment_violation(value: Any) -> AuditFragmentOmittedReason | None:
    """`None` se `value` cabe no contrato; senão o motivo estável.

    Profundidade conta containers no caminho (escalar = 0, `{}` = 1,
    `[[1]]` = 2). Todo valor visitado (container ou escalar) conta como
    um nó; o limite é checado ANTES de empilhar filhos, então a pilha
    nunca cresce além dele -- inclusive pra estruturas cíclicas
    construídas programaticamente."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        kind = type(item)
        if item is None or kind is str or kind is bool or kind is int:
            continue
        if kind is float:
            if not math.isfinite(item):
                return "non_json_value"
            continue
        if kind is not list and kind is not dict:
            return "non_json_value"
        if depth + 1 > MAX_AUDIT_FRAGMENT_DEPTH:
            return "complexity_limit_exceeded"
        if visited + len(stack) + len(item) > MAX_AUDIT_FRAGMENT_NODES:
            return "complexity_limit_exceeded"
        if kind is list:
            stack.extend((child, depth + 1) for child in item)
        else:
            for key, child in item.items():
                if type(key) is not str:
                    return "non_json_value"
                stack.append((child, depth + 1))
    return None


def bound_audit_fragment(
    value: Any, omitted_reason: AuditFragmentOmittedReason | None = None
) -> tuple[Any, AuditFragmentOmittedReason | None]:
    """Par (fragmento, motivo de omissão) que cabe no contrato.

    Um motivo já registrado é mantido (fragmento `None`); um fragmento
    fora do contrato vira `(None, motivo)`; um fragmento válido volta
    intacto -- mesmo objeto, sem cópia nem normalização."""
    if omitted_reason is not None:
        return None, omitted_reason
    violation = audit_fragment_violation(value)
    if violation is not None:
        return None, violation
    return value, None
