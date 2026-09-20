"""
Política de publicação do OpenAPI (contrato público 1.x).

1. RESPOSTAS extensíveis: o servidor pode ADICIONAR campos opcionais a
   qualquer objeto de resposta durante 1.x, e clientes devem ignorar chaves de
   resposta desconhecidas. Por isso o OpenAPI NÃO publica objetos de resposta
   como fechados (`additionalProperties: false`). REQUESTS continuam
   estritos: os schemas alcançáveis a partir de um `requestBody` mantêm
   `additionalProperties: false`, e a validação em runtime (Pydantic
   `extra="forbid"`) não muda em nenhum modelo -- só a PUBLICAÇÃO do schema de
   resposta.
2. O 422 padrão do FastAPI (`HTTPValidationError`) não é o que o servidor
   devolve (o handler de `RequestValidationError` devolve `ErrorResponse`).
   Rotas que podem falhar validação declaram 422 com `ErrorResponse`; o 422
   padrão remanescente (rotas cujos parâmetros não podem falhar validação) e
   seus componentes são removidos.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.presentation.schemas import ErrorResponse

_REF_PREFIX = "#/components/schemas/"
_FRAMEWORK_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


def error_response(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


INTERNAL_ERROR_RESPONSE = error_response(
    "Erro interno inesperado (`error.code` = `internal_error`)."
)
INVALID_REQUEST_RESPONSE = error_response(
    "Request inválido (`error.code` = `invalid_request`, ou `invalid_provider` "
    "quando `enabled_providers` contém provider inexistente)."
)
RUN_NOT_FOUND_RESPONSE = error_response("Run não encontrada (`error.code` = `run_not_found`).")
INSUFFICIENT_QUORUM_RESPONSE = error_response(
    "Execução despachada, mas com respostas abaixo do quórum mínimo "
    "(`error.code` = `insufficient_quorum`; `error.details.run_id` é a falha persistida)."
)


def _refs_in(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
            found.add(ref[len(_REF_PREFIX) :])
        for value in node.values():
            found |= _refs_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs_in(value)
    return found


def _request_schema_names(openapi: dict[str, Any], schemas: dict[str, Any]) -> set[str]:
    pending = _refs_in(
        [
            operation.get("requestBody", {})
            for path_item in openapi.get("paths", {}).values()
            for operation in path_item.values()
            if isinstance(operation, dict)
        ]
    )
    strict: set[str] = set()
    while pending:
        name = pending.pop()
        if name in strict or name not in schemas:
            continue
        strict.add(name)
        pending |= _refs_in(schemas[name])
    return strict


def apply_public_contract_policy(openapi: dict[str, Any]) -> dict[str, Any]:
    """Aplica IN PLACE (idempotente) a política acima ao documento gerado."""
    schemas = openapi.get("components", {}).get("schemas", {})

    # (2) 422 padrão do framework -> removido onde ainda é o default falso.
    for path_item in openapi.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            response_422 = responses.get("422")
            if response_422 is not None and _refs_in(response_422) & set(_FRAMEWORK_VALIDATION_SCHEMAS):
                del responses["422"]
    still_referenced = _refs_in(openapi.get("paths", {}))
    for name in _FRAMEWORK_VALIDATION_SCHEMAS:
        if name not in still_referenced:
            schemas.pop(name, None)

    # (1) Respostas abertas; requests (e o que eles referenciam) continuam estritos.
    strict = _request_schema_names(openapi, schemas)
    for name, schema in schemas.items():
        if name not in strict and schema.get("additionalProperties") is False:
            del schema["additionalProperties"]
    return openapi


def install_public_contract_policy(app: FastAPI) -> None:
    original = app.openapi

    def openapi() -> dict[str, Any]:
        return apply_public_contract_policy(original())

    app.openapi = openapi  # type: ignore[method-assign]
