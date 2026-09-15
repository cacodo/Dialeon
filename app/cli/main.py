"""
Entry point da CLI -- Etapa 14 (T19A.1).

argparse (stdlib) -- sem framework de CLI novo (Typer/Click/Rich): o
conjunto de comandos é pequeno e plano, argparse já é suficiente (Repo
Evidence Pack de T19A.1, seção F/11) -- nenhuma dependência nova.

Lifecycle (seção 6 do Stage 14): Settings() -> build_app_components() ->
comando -> SEMPRE `engine.dispose()`, via `try/finally` -- o mesmo padrão
que `create_app()` já usa no lifespan do FastAPI (`app/api/app.py`), só
que síncrono ao processo inteiro em vez de por-request. Nenhum framework
de lifecycle novo introduzido.

Erros de ARGUMENTO (subcomando ausente, flag desconhecida, etc.) usam o
comportamento padrão do argparse -- `SystemExit(2)`, que já bate
exatamente com o exit code 2 ("input/configuração inválida") deste
Stage, sem nenhum código extra pra mapear isso.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.bootstrap import build_app_components
from app.cli import commands, output
from app.config import Settings
from app.orchestrator.config import MAX_SOURCE_TEXT_CHARACTERS

EXIT_INTERNAL_ERROR = 1


def _limit_type(raw: str) -> int:
    """Espelha `Query(default=50, ge=1, le=100)` de `GET /runs` (Etapa 11,
    `app/api/routes.py`) -- mesmos limites, checados aqui na CLI em vez
    de propagados sem validação pro repository. Levantar
    `ArgumentTypeError` faz o argparse chamar `parser.error(...)`, que já
    termina em `SystemExit(2)` -- o mesmo exit code 2 usado pra todo
    input inválido nesta CLI, sem código extra pra mapear isso."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"limit precisa ser um inteiro, recebido: {raw!r}")
    if not (1 <= value <= 100):
        raise argparse.ArgumentTypeError("limit precisa estar entre 1 e 100")
    return value


def _offset_type(raw: str) -> int:
    """Espelha `Query(default=0, ge=0)` de `GET /runs`."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"offset precisa ser um inteiro, recebido: {raw!r}")
    if value < 0:
        raise argparse.ArgumentTypeError("offset precisa ser >= 0")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dialeon", description="Dialeon -- LLM Council, sem frontend gráfico."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Executa uma pergunta através do Council.")
    run_parser.add_argument("question", help="A pergunta a ser executada.")
    run_parser.add_argument(
        "--providers",
        default=None,
        metavar="p1,p2,...",
        help="Providers separados por vírgula (ex.: openai,anthropic). "
        "Se omitido, usa todos os providers disponíveis.",
    )
    run_parser.add_argument(
        "--source",
        default=None,
        metavar="TEXTO",
        help=f"Fonte textual delimitada, opcional -- até "
        f"{MAX_SOURCE_TEXT_CHARACTERS} caracteres. As claims atuais são "
        "comparadas contra ela (Source Analysis) e o resultado é "
        "reconciliado deterministicamente com a avaliação do Judge na "
        "resposta final.",
    )
    run_parser.add_argument("--json", action="store_true", dest="as_json")

    list_parser = subparsers.add_parser("list", help="Lista execuções recentes.")
    list_parser.add_argument("--limit", type=_limit_type, default=50)
    list_parser.add_argument("--offset", type=_offset_type, default=0)
    list_parser.add_argument("--json", action="store_true", dest="as_json")

    get_parser = subparsers.add_parser("get", help="Mostra os detalhes de uma execução.")
    get_parser.add_argument("run_id")
    get_parser.add_argument("--json", action="store_true", dest="as_json")

    audit_parser = subparsers.add_parser(
        "audit", help="Mostra a auditoria completa de uma execução."
    )
    audit_parser.add_argument("run_id")
    audit_parser.add_argument("--json", action="store_true", dest="as_json")

    providers_parser = subparsers.add_parser("providers", help="Lista os providers disponíveis.")
    providers_parser.add_argument("--json", action="store_true", dest="as_json")

    return parser


def _parse_providers(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    items = [p.strip() for p in raw.split(",")]
    return [p for p in items if p]


async def _dispatch(args: argparse.Namespace, components) -> int:
    if args.command == "run":
        return await commands.cmd_run(
            components,
            question=args.question,
            providers=_parse_providers(args.providers),
            source_text=args.source,
            as_json=args.as_json,
        )
    if args.command == "list":
        return await commands.cmd_list(
            components, limit=args.limit, offset=args.offset, as_json=args.as_json
        )
    if args.command == "get":
        return await commands.cmd_get(components, run_id=args.run_id, as_json=args.as_json)
    if args.command == "audit":
        return await commands.cmd_audit(components, run_id=args.run_id, as_json=args.as_json)
    if args.command == "providers":
        return await commands.cmd_providers(components, as_json=args.as_json)
    raise AssertionError(f"comando desconhecido: {args.command!r}")  # argparse já garante um dos acima


async def _run_async(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)  # SystemExit(2) próprio do argparse em input inválido

    # Patch de revisão (Blocker 1): Settings()/build_app_components()
    # também dentro do try -- antes ficavam FORA, então uma falha de
    # bootstrap (Settings malformado, init_db, construção de provider,
    # validação interna de provider em bootstrap.py) escapava do
    # catch-all e virava traceback cru + exit code não controlado pelo
    # Python (o que quer que asyncio.run() decida propagar), em vez de
    # exit 1 + mensagem genérica como qualquer outro erro inesperado.
    # `components = None` até a linha que realmente o constrói -- o
    # `finally` só faz dispose se essa construção chegou a suceder;
    # nunca inventa um dispose artificial sobre algo que nunca existiu.
    components = None
    try:
        settings = Settings()
        components = await build_app_components(settings)
        return await _dispatch(args, components)
    except Exception:
        # Falha inesperada/interna -- nunca vaza traceback nem
        # Settings/secrets pro usuário (seção 11/14 do Stage). Mesmo
        # princípio do handler catch-all de app/api/error_handlers.py:
        # mensagem genérica fixa, nada de `str(exc)`.
        message = "Erro interno inesperado."
        if getattr(args, "as_json", False):
            output.emit_json_error("internal_error", message)
        else:
            output.print_error(message)
        return EXIT_INTERNAL_ERROR
    finally:
        if components is not None:
            await components.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run_async(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
