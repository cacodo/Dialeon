from __future__ import annotations

from app.bootstrap import AppComponents
from app.application.service import CouncilExecutionService
from app.council.runner import CouncilRunner
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.repository import CouncilRepository
from tests.council.fakes import FakeDebateEngine, FakeEditor, FakeJudge, FakeSourceAnalyzer


async def build_test_components(
    settings,
    *,
    debate_result=None,
    source_analysis_result=None,
    judge_result=None,
    editor_result=None,
    quorum_exc=None,
    provider_names=("openai", "anthropic", "gemini"),
) -> AppComponents:
    """Monta AppComponents sem nenhum provider real/rede -- banco SQLite
    em memória isolado, e CouncilRunner recebendo os fakes já existentes
    de tests/council/fakes.py. `providers` é só um dict de nomes -- a
    única coisa que o router verifica nele e `nome in providers`, nunca
    chama método nenhum."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_factory = make_session_factory(engine)
    repository = CouncilRepository(session_factory)

    debate_engine = FakeDebateEngine(result=debate_result, exc=quorum_exc)
    source_analyzer = FakeSourceAnalyzer(result=source_analysis_result)
    judge = FakeJudge(result=judge_result)
    editor = FakeEditor(result=editor_result)
    runner = CouncilRunner(
        debate_engine=debate_engine,
        source_analyzer=source_analyzer,
        judge=judge,
        editor=editor,
    )
    providers = {name: object() for name in provider_names}
    service = CouncilExecutionService(
        runner=runner, repository=repository, known_providers=frozenset(providers)
    )

    return AppComponents(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        providers=providers,
        repository=repository,
        service=service,
    )


def make_components_factory(**kwargs):
    """`components_factory` compatível com `create_app(...)` -- ignora o
    `settings` real passado pelo lifespan, sempre devolve os
    componentes de teste já parametrizados."""

    async def factory(settings):
        return await build_test_components(settings, **kwargs)

    return factory
