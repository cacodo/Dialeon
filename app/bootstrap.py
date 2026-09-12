"""
Bootstrap / wiring compartilhável -- Etapa 11 (patch final, Parte B).

Deliberadamente FORA de `app/api/`: este módulo NÃO importa FastAPI e não
sabe nada de HTTP -- monta a cadeia providers -> pipeline -> persistência
-> `CouncilExecutionService`, que é execution semantics compartilhada,
não uma preocupação exclusiva da boundary HTTP (CLIENT SURFACE ≠
EXECUTION AUTHORITY; COMPOSITION ROOT ≠ HTTP API CONCERN). `app/api/`
usa este módulo como cliente; um futuro `app/cli/` (não implementado
nesta etapa) poderia reusar exatamente o mesmo `build_app_components`
sem depender de nada de `app/api/`.

Primeiro lugar do projeto inteiro que monta a cadeia completa:
providers -> pipeline -> persistência -> CouncilExecutionService. Nada
disso existia antes (nem em teste) -- confirmado no Evidence Pack da
Etapa 11.

Desvio consciente do diagrama da Decision Delta original (Etapa 11,
secao 13): o diagrama lista `Orchestrator` como um passo separado entre
`build_all_providers` e `DebateEngine`. O construtor real de
`DebateEngine` (Etapa 8, já aprovada) é
`DebateEngine(providers: dict[str, LLMProvider])` -- ele constrói seu
PRÓPRIO `Orchestrator` internamente
(`self._orchestrator = Orchestrator(providers)`), e não aceita um
`Orchestrator` externo. Injetar um `Orchestrator` aqui separadamente
duplicaria a rodada inicial (o mesmo achado da Etapa 8 original). Por
isso este módulo NÃO constrói `Orchestrator` -- `DebateEngine(providers)`
já é suficiente, exatamente como o resto do pipeline já assume desde a
Etapa 8.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.service import CouncilExecutionService
from app.config import Settings
from app.council.runner import CouncilRunner
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.providers.base import LLMProvider
from app.providers.factory import build_all_providers
from app.source_analysis.analyzer import SourceAnalyzer
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.repository import CouncilRepository


class ConfigurationError(Exception):
    """Erro de configuração da APLICAÇÃO (Settings inválido) -- nunca um
    erro do cliente HTTP, nunca deve virar 422 (Decision Delta §4). Se
    isso for levantado, é a implantação que está mal configurada, não a
    request de ninguém."""


@dataclass
class AppComponents:
    """Tudo que a API precisa, montado uma vez no lifespan e guardado em
    `app.state` -- nenhum global mutável de módulo (Decision Delta §14)."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    providers: dict[str, LLMProvider]
    repository: CouncilRepository
    service: CouncilExecutionService


def _validate_internal_provider_config(
    settings: Settings, providers: dict[str, LLMProvider]
) -> None:
    """`claim_processor_provider`/`judge_provider`/`editor_provider` não
    precisam pertencer a `enabled_providers` (RunConfig, já documentado),
    mas precisam existir no registry de providers construído -- senão
    TODA execução falharia em runtime profundo (dentro de
    DebateEngine/SingleJudge/Editor) com um ValueError cru. Falhar aqui,
    no startup, com uma mensagem clara, é preferível a deixar isso
    estourar silenciosamente na primeira request."""
    required = {
        "default_claim_processor_provider": settings.default_claim_processor_provider,
        "default_judge_provider": settings.default_judge_provider,
        "default_editor_provider": settings.default_editor_provider,
    }
    missing = {
        field: provider_name
        for field, provider_name in required.items()
        if provider_name not in providers
    }
    if missing:
        raise ConfigurationError(
            "Configuração interna inválida -- provider(s) configurado(s) em Settings "
            f"não existem no registry construído ({sorted(providers)}): {missing}"
        )


async def build_app_components(settings: Settings) -> AppComponents:
    """Monta a cadeia inteira. Chamado uma vez no lifespan de
    `create_app()` -- nunca por request (Decision Delta, revisão final:
    "endpoint criando pipeline manualmente por request" é exatamente o
    que isso evita)."""
    providers = build_all_providers(settings)
    _validate_internal_provider_config(settings, providers)

    engine = create_engine(settings.database_url)
    try:
        await init_db(engine)
        session_factory = make_session_factory(engine)
        repository = CouncilRepository(session_factory)

        debate_engine = DebateEngine(providers)
        source_analyzer = SourceAnalyzer(providers)
        judge = SingleJudge(providers)
        editor = Editor(providers)
        runner = CouncilRunner(
            debate_engine=debate_engine,
            source_analyzer=source_analyzer,
            judge=judge,
            editor=editor,
        )
        service = CouncilExecutionService(
            runner=runner, repository=repository, known_providers=frozenset(providers)
        )
    except Exception:
        # Stage 14 (patch de revisão final): se qualquer coisa falhar
        # DEPOIS do engine já ter sido criado (ex.: init_db() -- que já
        # abre conexão de verdade, não é lazy como create_engine()
        # sozinho) mas ANTES de AppComponents existir, o chamador
        # (_run_async/lifespan do FastAPI) nunca recebe um `components`
        # pra poder chamar `.dispose()` -- o engine ficaria órfão até o
        # GC decidir finalizá-lo (indesejável pra recursos assíncronos:
        # SQLAlchemy emite warning de engine não descartado, e em
        # backends que não são SQLite isso pode significar uma conexão
        # de rede genuinamente aberta). Descarta só o que foi de fato
        # criado e relança a exceção ORIGINAL intacta -- quem chama
        # continua vendo exatamente a mesma falha, só que sem vazar o
        # engine.
        await engine.dispose()
        raise

    return AppComponents(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        providers=providers,
        repository=repository,
        service=service,
    )
