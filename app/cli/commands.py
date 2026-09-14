"""
Implementação dos comandos da CLI -- Etapa 14 (T19A.1; layering
corrigido no patch de revisão).

Camada EMBEDDED: cada comando recebe `AppComponents` já montado (ver
`app/cli/main.py`) e chama diretamente `CouncilExecutionService`/
`CouncilRepository` -- os mesmos objetos que `app/api/routes.py` usa.
Nunca importa nada de FastAPI, nunca chama HTTP, nunca monta um segundo
`CouncilRunner`/pipeline/persistência.

Contratos/mappers vêm de `app.presentation` (não de `app.api`): a
revisão do patch apontou corretamente que `app/api/schemas.py`/
`app/api/mappers.py`, mesmo sendo Pydantic puro, eram semanticamente
HTTP-specific ("Schemas HTTP -- Etapa 11") -- importar de lá criaria
`CLI -> API presentation layer`, não uma camada neutra de verdade.
`app/presentation/{schemas,mappers}.py` é o destino compartilhado real:
`app/api/` e `app/cli/` são dois clientes dele, nenhum importa o outro.
`CreateRunRequest` em particular já contém a validação de forma
(pergunta não-vazia depois de trim) que a API usa -- reusá-la aqui
significa que CLI e API compartilham essa regra pela mesma razão que
compartilham a validação de provider desconhecido (ver
`app/application/errors.py`). Nunca importado: `app.api.*` (nenhum
módulo daquele pacote, nem os que não dependem de FastAPI).
"""

from __future__ import annotations

from pydantic import ValidationError

from app.application.errors import UnknownProviderError
from app.bootstrap import AppComponents
from app.cli import output
from app.orchestrator.config import RunConfig
from app.orchestrator.errors import InsufficientQuorumError
from app.presentation.mappers import (
    completed_run_audit,
    completed_run_response,
    failed_run_response,
    quorum_failure_audit,
    quorum_failure_run_response,
    running_run_response,
    run_summary_response,
)
from app.presentation.schemas import CreateRunRequest, ProvidersResponse, RunListResponse
from app.storage.records import AcceptedRunRecord, CompletedRunRecord, QuorumFailureRecord

EXIT_OK = 0
EXIT_INTERNAL_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_INSUFFICIENT_QUORUM = 3
EXIT_NOT_FOUND = 4


def _pydantic_errors(exc: ValidationError) -> list[dict]:
    """Extrai só loc/msg/type de cada erro -- nunca `ctx`/`input`, que no
    Pydantic v2 podem carregar o objeto de exceção original (não
    serializável em JSON diretamente). Mais estreito que
    `jsonable_encoder` (que a API usa), mas suficiente aqui e sem
    precisar importar nada de FastAPI (ver docstring do módulo)."""
    return [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]


async def cmd_run(
    components: AppComponents,
    *,
    question: str,
    providers: list[str] | None,
    source_text: str | None,
    as_json: bool,
) -> int:
    """Etapa 14, seção 4: superfície de input idêntica à API --
    `question` + `enabled_providers` (`--providers`) + `source_text`
    (`--source`, Etapa 16), nada mais. Se `--providers` for omitido, usa
    TODOS os providers atualmente construídos (mesmo default implícito
    que faria sentido pra um uso local de um comando só -- a API exige a
    lista explicitamente porque é um contrato HTTP formal, mas a CLI
    pode ser mais conveniente pra esse caso sem abrir NENHUM campo novo
    de configuração)."""
    enabled_providers = providers if providers is not None else sorted(components.providers)

    try:
        body = CreateRunRequest(
            question=question, enabled_providers=enabled_providers, source_text=source_text
        )
    except ValidationError as exc:
        message = "Request inválido."
        if as_json:
            output.emit_json_error(
                "invalid_request", message, details={"errors": _pydantic_errors(exc)}
            )
        else:
            output.print_error(message)
            for err in exc.errors():
                output.print_error(f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
        return EXIT_INVALID_INPUT

    run_config = RunConfig.from_settings(
        components.settings,
        question=body.question,
        enabled_providers=body.enabled_providers,
        source_text=body.source_text,
    )

    try:
        result = await components.service.run(run_config)
    except UnknownProviderError as exc:
        message = "Um ou mais providers solicitados não existem."
        if as_json:
            output.emit_json_error(
                "invalid_provider",
                message,
                details={
                    "unknown_providers": exc.unknown_providers,
                    "known_providers": exc.known_providers,
                },
            )
        else:
            output.print_error(message)
        return EXIT_INVALID_INPUT
    except InsufficientQuorumError as exc:
        message = str(exc)
        if as_json:
            output.emit_json_error(
                "insufficient_quorum",
                message,
                details={
                    "run_id": exc.persisted_failure_id,
                    "successful_count": exc.successful_count,
                    "total_providers": exc.total_providers,
                    "min_to_return": exc.min_to_return,
                },
            )
        else:
            output.print_error(f"quórum insuficiente: {message}")
        return EXIT_INSUFFICIENT_QUORUM

    response = completed_run_response(
        result, provider_execution_policy=components.provider_execution_policy
    )
    if as_json:
        output.emit_json(response)
    else:
        print(output.human_run_result(response))
    return EXIT_OK


async def cmd_list(components: AppComponents, *, limit: int, offset: int, as_json: bool) -> int:
    summaries = await components.repository.list_runs(limit=limit, offset=offset)
    runs = [run_summary_response(s) for s in summaries]
    if as_json:
        output.emit_json(RunListResponse(runs=runs, limit=limit, offset=offset))
    else:
        print(output.human_run_summary_list(runs))
    return EXIT_OK


async def _get_record(
    components: AppComponents, run_id: str
) -> CompletedRunRecord | QuorumFailureRecord | None:
    """Mesma checagem de existência que `app/api/routes.py` faz (`None`
    -> não encontrada) -- reusada diretamente contra a repository
    boundary real, sem passar por HTTP nem replicar nenhuma query SQL
    (Etapa 14, seção 10). Não há regra nenhuma aqui pra compartilhar
    além de "é None ou não" -- por isso não foi extraída pra lugar
    nenhum, diferente da validação de provider (que TINHA uma regra de
    verdade: o que conta como desconhecido)."""
    return await components.repository.get_run(run_id)


async def cmd_get(components: AppComponents, *, run_id: str, as_json: bool) -> int:
    record = await _get_record(components, run_id)
    if record is None:
        message = f"Run não encontrada: {run_id}"
        if as_json:
            output.emit_json_error("run_not_found", message)
        else:
            output.print_error(message)
        return EXIT_NOT_FOUND

    if isinstance(record, CompletedRunRecord):
        response = completed_run_response(
            record.council_run_result, provider_execution_policy=record.provider_execution_policy
        )
        if as_json:
            output.emit_json(response)
        else:
            print(output.human_run_result(response))
    elif isinstance(record, QuorumFailureRecord):
        response = quorum_failure_run_response(record)
        if as_json:
            output.emit_json(response)
        else:
            print(output.human_quorum_failure(response))
    else:
        assert isinstance(record, AcceptedRunRecord)
        lifecycle_response = (
            running_run_response(record) if record.status == "running" else failed_run_response(record)
        )
        if as_json:
            output.emit_json(lifecycle_response)
        else:
            print(output.human_accepted_run(lifecycle_response))
    return EXIT_OK


async def cmd_audit(components: AppComponents, *, run_id: str, as_json: bool) -> int:
    record = await _get_record(components, run_id)
    if record is None:
        message = f"Run não encontrada: {run_id}"
        if as_json:
            output.emit_json_error("run_not_found", message)
        else:
            output.print_error(message)
        return EXIT_NOT_FOUND

    if isinstance(record, CompletedRunRecord):
        audit = completed_run_audit(
            record.council_run_result, provider_execution_policy=record.provider_execution_policy
        )
    elif isinstance(record, QuorumFailureRecord):
        audit = quorum_failure_audit(record)
    else:
        assert isinstance(record, AcceptedRunRecord)
        audit = running_run_response(record) if record.status == "running" else failed_run_response(record)

    if as_json:
        output.emit_json(audit)
    elif isinstance(record, AcceptedRunRecord):
        print(output.human_accepted_run(audit))
    else:
        print(output.human_run_audit(audit))
    return EXIT_OK


async def cmd_providers(components: AppComponents, *, as_json: bool) -> int:
    response = ProvidersResponse(providers=sorted(components.providers))
    if as_json:
        output.emit_json(response)
    else:
        print(output.human_providers_list(response.providers))
    return EXIT_OK
