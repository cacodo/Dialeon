"""
Tipo de coluna dos dois fragmentos de auditoria brutos (repair M2).

`deterministic_verification_attempts.raw_proposal_json` e
`source_claim_analysis_results.raw_entry_json` continuam sendo colunas
`JSON` com EXATAMENTE a mesma escrita de antes (DDL `JSON`, `json.dumps`
do bind do SQLAlchemy, `None` como o texto `'null'`) -- nenhuma linha
existente muda de representação, nenhuma migration de dados.

Só a LEITURA muda. O processador de resultado do `JSON` genérico chama
`json.loads` no texto armazenado antes de qualquer código da aplicação
ver o valor; um fragmento histórico gravado antes do contrato de
app/audit_fragment.py (em Python 3.13/3.14 o save aceitava milhares de
níveis) estoura `RecursionError` nesse decoder em Python 3.11 -- o run
inteiro fica ilegível e nenhum validador de domínio chega a rodar.

Aqui a aplicação decodifica sob controle: mede a profundidade do texto
sem decodificar (`json_text_exceeds_depth`, linear, sem recursão) e só
então chama `json.loads`. Um texto fora do contrato vira
`OmittedStoredAuditFragment(reason)`, que o serializer transforma em
`(None, reason)` no domínio. A linha no banco nunca é reescrita por uma
leitura, e o sentinela não é serializável: uma tentativa de gravá-lo de
volta falha no bind em vez de apagar o original em silêncio.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.types import JSON, TypeDecorator

from app.audit_fragment import AuditFragmentOmittedReason, json_text_exceeds_depth


@dataclass(frozen=True)
class OmittedStoredAuditFragment:
    """Fragmento armazenado que a aplicação se recusou a decodificar."""

    reason: AuditFragmentOmittedReason


def decode_stored_audit_fragment(value: Any) -> Any:
    """Valor armazenado -> valor JSON, ou `OmittedStoredAuditFragment`.

    Por causa da afinidade NUMERIC da coluna `JSON` no SQLite, um número
    de primeiro nível chega como `int`/`float` já decodificado pelo
    driver; só texto é JSON a decodificar."""
    if value is None or not isinstance(value, str):
        return value
    if json_text_exceeds_depth(value):
        return OmittedStoredAuditFragment("complexity_limit_exceeded")
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        # Texto que não decodifica sob o runtime atual (ex.: inteiro além
        # do limite de dígitos do CPython): fora do contrato JSON da
        # aplicação. Nunca propaga texto de exceção.
        return OmittedStoredAuditFragment("non_json_value")


class AuditFragmentJSON(TypeDecorator):
    """`JSON` na escrita; decodificação limitada da aplicação na leitura."""

    impl = JSON
    cache_ok = True

    def result_processor(self, dialect, coltype):  # noqa: ANN001, ANN201
        return decode_stored_audit_fragment


def stored_audit_fragment(
    value: Any, recorded_reason: AuditFragmentOmittedReason | None
) -> tuple[Any, AuditFragmentOmittedReason | None]:
    """(fragmento, motivo) pra reconstruir o domínio a partir da linha.

    Um motivo gravado na linha tem precedência (o fragmento foi omitido na
    escrita); um fragmento recusado na leitura só é marcado com o motivo da
    leitura -- a coluna de motivo na linha continua como está."""
    if isinstance(value, OmittedStoredAuditFragment):
        return None, recorded_reason or value.reason
    return value, recorded_reason
