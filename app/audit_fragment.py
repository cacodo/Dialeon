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
  ambíguo demais pra ser tratado como "o que o provider devolveu";
- strings (valores E chaves) só com Unicode scalar values -- nenhum code
  point substituto (U+D800..U+DFFF), mesma regra do I-JSON (RFC 7493
  §2.1). `json.loads` aceita o escape `"\\ud800"` e devolve um substituto
  isolado; ele salva e relê (o bind escapa em ASCII), mas o
  `model_dump(mode="json")`/`model_dump_json()` do Pydantic, a CLI JSON e
  a resposta HTTP falham com `UnicodeEncodeError`. Um par substituto em
  DOIS code points (só construível programaticamente) seria fundido em um
  caractere pelo round-trip do banco -- transformação silenciosa, também
  rejeitada. Todo o resto do Unicode (BMP, não-BMP, marcas combinantes,
  string vazia) é aceito intacto;
- inteiros dentro de int64 (`-2**63 .. 2**63 - 1`). Evidência: um inteiro
  de primeiro nível fora disso volta do SQLite como `REAL` (a coluna
  `JSON` tem afinidade NUMERIC: `10**20` é relido como `1e+20` -- valor
  alterado em silêncio), e acima de 4300 dígitos o `json.dumps` do bind
  estoura o limite global (e mutável, mínimo 640) de conversão
  int->str do CPython. A checagem é só comparação numérica: nunca chama
  `str()` no inteiro. O mesmo limite vale em qualquer posição do
  fragmento, pra que o contrato não dependa de onde o número aparece.

Todo valor ACEITO por este contrato atravessa, sem transformação além da
igualdade numérica JSON (um float inteiro de primeiro nível, como `5.0`,
volta como `5` pela mesma afinidade NUMERIC): domínio -> persistência ->
reload -> `model_dump(mode="json")` -> `model_dump_json()` -> JSON público
(HTTP/CLI). Nenhum motivo novo foi criado pros casos de string/inteiro:
`non_json_value` significa "fora do modelo JSON interoperável da
aplicação" (tipo, número não finito, inteiro fora de int64, substituto
Unicode), o que é exatamente o que eles são.

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
import re
from typing import Any, Literal

MAX_AUDIT_FRAGMENT_DEPTH = 32
MAX_AUDIT_FRAGMENT_NODES = 10_000
MIN_AUDIT_FRAGMENT_INT = -(2**63)
MAX_AUDIT_FRAGMENT_INT = 2**63 - 1

_SURROGATE = re.compile(r"[\ud800-\udfff]")

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
        if item is None or kind is bool:
            continue
        if kind is str:
            if _SURROGATE.search(item) is not None:
                return "non_json_value"
            continue
        if kind is int:
            if not MIN_AUDIT_FRAGMENT_INT <= item <= MAX_AUDIT_FRAGMENT_INT:
                return "non_json_value"
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
                if type(key) is not str or _SURROGATE.search(key) is not None:
                    return "non_json_value"
                stack.append((child, depth + 1))
    return None


# Um token estrutural FORA de string JSON, ou uma string JSON inteira
# (pulada como um token só, com escapes). Loop desenrolado, sem
# alternância aninhada: linear e sem recursão no motor de regex.
_JSON_STRUCTURE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]', re.DOTALL)


def json_text_exceeds_depth(text: str, limit: int = MAX_AUDIT_FRAGMENT_DEPTH) -> bool:
    """Se o texto JSON ARMAZENADO aninha containers além de `limit`,
    medido sem decodificar -- varredura linear, sem recursão. Existe pra
    que um fragmento histórico gravado antes deste contrato nunca chegue
    ao `json.loads` recursivo (que estoura em profundidades diferentes em
    cada Python). Texto malformado pode ser superestimado, nunca
    subestimado: o pior caso é degradar o que já não decodificaria."""
    depth = 0
    for match in _JSON_STRUCTURE.finditer(text):
        token = match.group()
        if token == "[" or token == "{":
            depth += 1
            if depth > limit:
                return True
        elif token == "]" or token == "}":
            depth -= 1
    return False


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
