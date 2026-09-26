"""
Exception handlers -- Etapa 11.

Cada handler mapeia UM tipo de erro esperado (Decision Delta secao 6/7).
O catch-all genérico nunca inclui `str(exc)`/traceback -- só um código e
mensagem fixos, para não vazar detalhe interno (principio 3).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.exceptions import RunNotFoundError
from app.presentation.schemas import ErrorBody, ErrorResponse
from app.application.errors import (
    InvalidExecutionLimitsError,
    InvalidQuestionError,
    InvalidQuorumConfigurationError,
    LocalPrerequisitesMissingError,
    UnknownProviderError,
)
from app.orchestrator.errors import InsufficientQuorumError

logger = logging.getLogger(__name__)


def _error_json(status_code: int, body: ErrorResponse) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorResponse(
                error=ErrorBody(
                    code="invalid_request",
                    message="Request inválido.",
                    # jsonable_encoder -- exc.errors() do Pydantic pode
                    # incluir o objeto ValueError CRU dentro de ctx.error
                    # (validators customizados), que não é serializável
                    # em JSON diretamente. jsonable_encoder sabe reduzir
                    # isso a algo serializável, sem vazar traceback.
                    details={"errors": jsonable_encoder(exc.errors())},
                )
            ),
        )

    @app.exception_handler(UnknownProviderError)
    async def _handle_invalid_provider(
        request: Request, exc: UnknownProviderError
    ) -> JSONResponse:
        return _error_json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorResponse(
                error=ErrorBody(
                    code="invalid_provider",
                    message="Um ou mais providers solicitados não existem.",
                    details={
                        "unknown_providers": exc.unknown_providers,
                        "known_providers": exc.known_providers,
                    },
                )
            ),
        )

    @app.exception_handler(LocalPrerequisitesMissingError)
    async def _handle_local_prerequisites_missing(
        request: Request, exc: LocalPrerequisitesMissingError
    ) -> JSONResponse:
        return _error_json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorResponse(
                error=ErrorBody(
                    code="provider_prerequisites_missing",
                    message="O provider escolhido não tem a configuração local necessária.",
                    details={"provider": exc.provider},
                )
            ),
        )

    @app.exception_handler(InvalidQuestionError)
    async def _handle_invalid_question(
        request: Request, exc: InvalidQuestionError
    ) -> JSONResponse:
        # Accepted Question Size Boundary V1 -- mesmo code="invalid_request"
        # que o handler de RequestValidationError acima usa: é a MESMA
        # classe de problema (forma inválida de `question`), só
        # detectada numa boundary diferente (chamador direto do service,
        # nunca alcançado pelos dois clientes reais -- CreateRunRequest
        # já rejeita isso bem mais cedo pra eles).
        return _error_json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorResponse(
                error=ErrorBody(
                    code="invalid_request",
                    message=exc.reason,
                    details=None,
                )
            ),
        )

    @app.exception_handler(InvalidQuorumConfigurationError)
    async def _handle_invalid_quorum_configuration(
        request: Request, exc: InvalidQuorumConfigurationError
    ) -> JSONResponse:
        # Accepted Quorum Feasibility Boundary V1 -- mesmo
        # code="invalid_request" que InvalidQuestionError usa acima: é a
        # MESMA classe de problema (configuração de aceite inválida),
        # detectada numa boundary diferente. NUNCA 409 -- 409/
        # "insufficient_quorum" (handler abaixo) é reservado pra uma
        # execução FACTÍVEL que foi de fato despachada e cujo resultado
        # OBSERVADO ficou abaixo do quórum, nunca pra uma configuração
        # matematicamente infactível detectada antes de qualquer
        # dispatch.
        return _error_json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorResponse(
                error=ErrorBody(
                    code="invalid_request",
                    message=str(exc),
                    details={
                        "min_to_return": exc.min_to_return,
                        "participant_count": exc.participant_count,
                    },
                )
            ),
        )

    @app.exception_handler(InvalidExecutionLimitsError)
    async def _handle_invalid_execution_limits(
        request: Request, exc: InvalidExecutionLimitsError
    ) -> JSONResponse:
        # Finite RunConfig New-Execution Boundary V1 -- mesmo
        # code="invalid_request" que InvalidQuestionError/
        # InvalidQuorumConfigurationError usam acima: é a MESMA classe
        # de problema (configuração de aceite inválida), nunca
        # alcançado pelos dois clientes reais em uso normal
        # (`Settings.default_max_cost_usd`/
        # `orchestrator_round_dispatch_timeout_seconds` já são sempre
        # finitos) -- só protege um chamador direto do service.
        return _error_json(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorResponse(
                error=ErrorBody(
                    code="invalid_request",
                    message=exc.reason,
                    details=None,
                )
            ),
        )

    @app.exception_handler(InsufficientQuorumError)
    async def _handle_insufficient_quorum(
        request: Request, exc: InsufficientQuorumError
    ) -> JSONResponse:
        return _error_json(
            status.HTTP_409_CONFLICT,
            ErrorResponse(
                error=ErrorBody(
                    code="insufficient_quorum",
                    message=str(exc),
                    details={
                        "run_id": exc.persisted_failure_id,
                        "successful_count": exc.successful_count,
                        "total_providers": exc.total_providers,
                        "min_to_return": exc.min_to_return,
                    },
                )
            ),
        )

    @app.exception_handler(RunNotFoundError)
    async def _handle_run_not_found(request: Request, exc: RunNotFoundError) -> JSONResponse:
        return _error_json(
            status.HTTP_404_NOT_FOUND,
            ErrorResponse(
                error=ErrorBody(
                    code="run_not_found",
                    message=f"Run não encontrada: {exc.run_id}",
                    details=None,
                )
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Erro interno inesperado ao processar %s", request.url)
        return _error_json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ErrorResponse(
                error=ErrorBody(
                    code="internal_error",
                    message="Erro interno inesperado.",
                    details=None,
                )
            ),
        )
