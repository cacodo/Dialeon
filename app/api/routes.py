"""
Router HTTP -- Etapa 11.

Camada de transporte pura (principio 1): nenhum endpoint contém lógica de
pipeline/accounting/pricing -- só validação de request, chamada ao
service/repository já montados, e mapeamento pra schema HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.bootstrap import AppComponents
from app.api.exceptions import RunNotFoundError
from app.api.openapi import (
    INSUFFICIENT_QUORUM_RESPONSE,
    INTERNAL_ERROR_RESPONSE,
    INVALID_REQUEST_RESPONSE,
    RUN_NOT_FOUND_RESPONSE,
)
from app.presentation.mappers import (
    completed_run_audit,
    completed_run_response,
    failed_run_response,
    quorum_failure_audit,
    quorum_failure_run_response,
    running_run_response,
    run_summary_response,
)
from app.presentation.schemas import (
    CreateRunRequest,
    ProvidersResponse,
    RunAuditResponse,
    RunListResponse,
    RunResponse,
)
from app.orchestrator.config import RunConfig
from app.storage.records import AcceptedRunRecord, CompletedRunRecord, QuorumFailureRecord

# Todas as rotas podem devolver o catch-all 500 (`internal_error`) -- ver
# `error_handlers.py`. Os demais erros são declarados por rota.
router = APIRouter(responses={500: INTERNAL_ERROR_RESPONSE})


class RunCreationRoute(APIRoute):
    """Check media type after routing, before FastAPI reads the request body."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def require_json(request: Request):
            # Browsers can issue text/plain, form, or headerless simple POSTs
            # without a CORS preflight. JSON requires preflight across origins;
            # this app does not enable CORS. This wrapper belongs only to the
            # Run creation route, so root_path cannot bypass the check.
            content_type = (
                request.headers.get("content-type", "").partition(";")[0].strip().lower()
            )
            if content_type != "application/json" and not (
                content_type.startswith("application/") and content_type.endswith("+json")
            ):
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "invalid_request",
                            "message": "Content-Type JSON obrigatório.",
                            "details": None,
                        }
                    },
                )
            return await handler(request)

        return require_json


run_creation_router = APIRouter(
    route_class=RunCreationRoute, responses={500: INTERNAL_ERROR_RESPONSE}
)


def _components(request: Request) -> AppComponents:
    """Dependência simples via `app.state` -- sem framework de DI
    externo (Decision Delta secao 14). Testável trocando
    `request.app.state.components` por uma instância de teste, sem
    monkeypatch global (Decision Delta secao 15)."""
    return request.app.state.components


@router.get("/providers", response_model=ProvidersResponse)
async def get_providers(request: Request) -> ProvidersResponse:
    """Etapa 12 — único seam HTTP novo autorizado. Só os identificadores
    (chaves de `AppComponents.providers`, já montado no bootstrap) --
    nunca a instância do provider, nunca Settings/credenciais."""
    components = _components(request)
    return ProvidersResponse(providers=sorted(components.providers))


@run_creation_router.post(
    "/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: INSUFFICIENT_QUORUM_RESPONSE, 422: INVALID_REQUEST_RESPONSE},
)
async def create_run(body: CreateRunRequest, request: Request) -> RunResponse:
    """Etapa 14: a validação de `enabled_providers` desconhecidos não
    mora mais aqui -- `CouncilExecutionService.run()` já a faz, antes de
    qualquer chamada ao runner, levantando `UnknownProviderError`
    (`app/application/errors.py`), traduzido pra HTTP 422 +
    `error.code="invalid_provider"` em `error_handlers.py`. Mesmo
    comportamento HTTP de antes -- a regra só passou a morar numa
    boundary que a CLI também compartilha, em vez de duplicada."""
    components = _components(request)

    run_config = RunConfig.from_settings(
        components.settings,
        question=body.question,
        enabled_providers=body.enabled_providers,
        source_text=body.source_text,
    )

    result = await components.service.run(run_config)
    # Provider Default-Model Snapshot Provenance V1 (F1 repair,
    # review de independência) -- NUNCA recomputar o snapshot a partir
    # do registry de provider AO VIVO aqui: o fato de provenance
    # autoritativo é o que `CouncilExecutionService.run()` já construiu
    # e persistiu ANTES do aceite durável. Recarrega o registro recém-
    # gravado (a MESMA transação de `save_success` já commitou) e
    # reusa exatamente `record.default_model_authority_snapshot`/
    # `record.provider_execution_policy` -- o MESMO caminho de leitura
    # que `GET /runs/{id}` já usa, nunca uma segunda fonte de verdade.
    record = await components.repository.get_run(result.id)
    assert isinstance(record, CompletedRunRecord)
    return completed_run_response(
        record.council_run_result,
        provider_execution_policy=record.provider_execution_policy,
        default_model_authority_snapshot=record.default_model_authority_snapshot,
    )


@router.get("/runs", response_model=RunListResponse, responses={422: INVALID_REQUEST_RESPONSE})
async def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RunListResponse:
    components = _components(request)
    summaries = await components.repository.list_runs(limit=limit, offset=offset)
    return RunListResponse(
        runs=[run_summary_response(s) for s in summaries], limit=limit, offset=offset
    )


@router.get("/runs/{run_id}", response_model=RunResponse, responses={404: RUN_NOT_FOUND_RESPONSE})
async def get_run(run_id: str, request: Request) -> RunResponse:
    components = _components(request)
    record = await components.repository.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)

    if isinstance(record, CompletedRunRecord):
        return completed_run_response(
            record.council_run_result,
            provider_execution_policy=record.provider_execution_policy,
            default_model_authority_snapshot=record.default_model_authority_snapshot,
        )
    if isinstance(record, QuorumFailureRecord):
        return quorum_failure_run_response(record)
    assert isinstance(record, AcceptedRunRecord)
    if record.status == "running":
        return running_run_response(record)
    return failed_run_response(record)


@router.get(
    "/runs/{run_id}/audit",
    response_model=RunAuditResponse,
    responses={404: RUN_NOT_FOUND_RESPONSE},
)
async def get_run_audit(run_id: str, request: Request) -> RunAuditResponse:
    components = _components(request)
    record = await components.repository.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)

    if isinstance(record, CompletedRunRecord):
        return completed_run_audit(
            record.council_run_result,
            provider_execution_policy=record.provider_execution_policy,
            default_model_authority_snapshot=record.default_model_authority_snapshot,
        )
    if isinstance(record, QuorumFailureRecord):
        return quorum_failure_audit(record)
    assert isinstance(record, AcceptedRunRecord)
    if record.status == "running":
        return running_run_response(record)
    return failed_run_response(record)
