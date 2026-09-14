"""
Router HTTP -- Etapa 11.

Camada de transporte pura (principio 1): nenhum endpoint contém lógica de
pipeline/accounting/pricing -- só validação de request, chamada ao
service/repository já montados, e mapeamento pra schema HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from app.bootstrap import AppComponents
from app.api.exceptions import RunNotFoundError
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

router = APIRouter()


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


@router.post("/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
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
    return completed_run_response(
        result, provider_execution_policy=components.provider_execution_policy
    )


@router.get("/runs", response_model=RunListResponse)
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


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, request: Request) -> RunResponse:
    components = _components(request)
    record = await components.repository.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)

    if isinstance(record, CompletedRunRecord):
        return completed_run_response(
            record.council_run_result, provider_execution_policy=record.provider_execution_policy
        )
    if isinstance(record, QuorumFailureRecord):
        return quorum_failure_run_response(record)
    assert isinstance(record, AcceptedRunRecord)
    if record.status == "running":
        return running_run_response(record)
    return failed_run_response(record)


@router.get("/runs/{run_id}/audit", response_model=RunAuditResponse)
async def get_run_audit(run_id: str, request: Request) -> RunAuditResponse:
    components = _components(request)
    record = await components.repository.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)

    if isinstance(record, CompletedRunRecord):
        return completed_run_audit(
            record.council_run_result, provider_execution_policy=record.provider_execution_policy
        )
    if isinstance(record, QuorumFailureRecord):
        return quorum_failure_audit(record)
    assert isinstance(record, AcceptedRunRecord)
    if record.status == "running":
        return running_run_response(record)
    return failed_run_response(record)
