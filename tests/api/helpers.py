from __future__ import annotations

from app.bootstrap import AppComponents
from app.application.service import CouncilExecutionService
from app.council.runner import CouncilRunner
from app.models.provider_models import ProviderExecutionPolicy
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.repository import CouncilRepository
from tests.council.fakes import FakeDebateEngine, FakeEditor, FakeJudge, FakeSourceAnalyzer

# T02.2 -- valor de teste fixo, DISTINTO dos defaults reais de Settings
# (60s/3 tentativas) de propósito: qualquer teste que confundisse "o
# resolvido pra este componente de teste" com "o default global" seria
# pego por essa diferença.
TEST_PROVIDER_EXECUTION_POLICY = ProviderExecutionPolicy(
    attempt_timeout_seconds=30.0, max_transport_attempts_per_completion=2
)


class _FakeRegistryProvider:
    """Provider Default-Model Snapshot Provenance V1 -- fake MÍNIMO só
    pra popular `AppComponents.providers`/`CouncilExecutionService`
    nestes testes: nunca chama rede, só expõe `.default_model` (o único
    atributo que `_build_default_model_authority_snapshot` lê). Antes
    desta slice, `providers` aqui era `{name: object() for name in
    provider_names}` -- um `object()` puro não tem `.default_model`,
    então precisou virar este fake mínimo assim que o snapshot passou a
    ser construído de verdade a partir do registry."""

    def __init__(self, default_model: str):
        self.default_model = default_model


class SelfMutatingRegistryProvider:
    """F1 (repair pós-revisão independente, MEDIUM) -- fake adversarial
    pra provar que a apresentação NUNCA relê o registry de provider
    depois do aceite: `.default_model` devolve `first_value` na
    PRIMEIRA leitura, e `later_value` em toda leitura subsequente.

    `CouncilExecutionService.run()` lê `.default_model` exatamente UMA
    vez por provider autorizado (dentro de
    `build_default_model_authority_snapshot`, ANTES do aceite durável)
    -- então o snapshot PERSISTIDO precisa conter `first_value`. Se
    QUALQUER caminho de apresentação (síncrono ou de reconstrução)
    recomputar o snapshot lendo `.default_model` de novo, essa segunda
    leitura devolve `later_value` -- expondo o bug imediatamente."""

    def __init__(self, first_value: str, later_value: str):
        self._first_value = first_value
        self._later_value = later_value
        self.read_count = 0

    @property
    def default_model(self) -> str:
        self.read_count += 1
        return self._first_value if self.read_count == 1 else self._later_value


async def build_test_components(
    settings,
    *,
    debate_result=None,
    source_analysis_result=None,
    judge_result=None,
    editor_result=None,
    quorum_exc=None,
    provider_names=("openai", "anthropic", "gemini"),
    provider_execution_policy: ProviderExecutionPolicy | None = None,
    provider_default_models: dict[str, str] | None = None,
    provider_instances: dict | None = None,
) -> AppComponents:
    """Monta AppComponents sem nenhum provider real/rede -- banco SQLite
    em memória isolado, e CouncilRunner recebendo os fakes já existentes
    de tests/council/fakes.py. `providers` é um dict de fakes mínimos
    (`_FakeRegistryProvider`) -- a única coisa que o router/service
    usam deles é `nome in providers`/`.default_model`, nunca chamam
    método nenhum. `provider_default_models` (opcional) permite a um
    teste escolher explicitamente o `default_model` de cada provider
    (default `f"{name}-fake-model"` quando omitido). `provider_instances`
    (opcional, F1 -- Provider Default-Model Snapshot Provenance V1)
    permite a um teste injetar objetos de provider fake CUSTOMIZADOS
    diretamente (ex.: um fake cujo `.default_model` muda entre leituras,
    pra provar que a apresentação nunca relê o registry depois do
    aceite) -- quando fornecido, substitui inteiramente a construção
    padrão via `_FakeRegistryProvider`."""
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
    if provider_instances is not None:
        providers = dict(provider_instances)
    else:
        default_models = provider_default_models or {}
        providers = {
            name: _FakeRegistryProvider(default_models.get(name, f"{name}-fake-model"))
            for name in provider_names
        }
    policy = provider_execution_policy or TEST_PROVIDER_EXECUTION_POLICY
    service = CouncilExecutionService(
        runner=runner,
        repository=repository,
        providers=providers,
        provider_execution_policy=policy,
    )

    return AppComponents(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        providers=providers,
        repository=repository,
        service=service,
        provider_execution_policy=policy,
    )


def make_components_factory(**kwargs):
    """`components_factory` compatível com `create_app(...)` -- ignora o
    `settings` real passado pelo lifespan, sempre devolve os
    componentes de teste já parametrizados."""

    async def factory(settings):
        return await build_test_components(settings, **kwargs)

    return factory
