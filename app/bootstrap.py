"""
Bootstrap / wiring compartilhável -- Etapa 11 (patch final, Parte B).

Deliberadamente FORA de `app/api/`: este módulo NÃO importa FastAPI e não
sabe nada de HTTP -- monta a cadeia providers -> pipeline -> persistência
-> `CouncilExecutionService`, que é execution semantics compartilhada,
não uma preocupação exclusiva da boundary HTTP (CLIENT SURFACE ≠
EXECUTION AUTHORITY; COMPOSITION ROOT ≠ HTTP API CONCERN). `app/api/`
e `app/cli/` usam este módulo como clientes, cada um chamando o MESMO
`build_app_components` no início do seu próprio lifecycle (ver
`app/api/app.py`/`app/cli/main.py`) -- nenhum dos dois depende do
outro, nem reimplementa a montagem da cadeia.

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

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.service import CouncilExecutionService
from app.config import Settings
from app.council.runner import CouncilRunner
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.models.provider_models import ProviderExecutionPolicy
from app.orchestrator.config import QuorumPolicy
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
    `app.state` -- nenhum global mutável de módulo (Decision Delta §14).

    `provider_execution_policy` (T02.2): a MESMA instância resolvida que
    foi injetada em `providers`/`service` -- exposta aqui, sibling de
    `.repository`, principalmente pra composição/inspeção de deployment
    (ex.: testes, futuros clientes) que precisem do snapshot vigente sem
    depender de nenhum Run já ter sido aceito.

    Provider Default-Model Snapshot Provenance V1 (F1, repair pós-
    revisão independente) -- `app/api/routes.py`/`app/cli/commands.py`
    NÃO leem mais este campo pra montar a resposta de um Run: depois de
    `CouncilExecutionService.run()` suceder, os dois recarregam o
    registro recém-persistido (`repository.get_run(result.id)`) e usam
    `record.provider_execution_policy`/
    `record.default_model_authority_snapshot` -- o segundo destes varia
    POR RUN (depende de `run_config.all_provider_authorities`), então
    nunca poderia ser um valor de deployment único como este campo é;
    ler de volta o registro recém-gravado (em vez de recomputar do
    registry de provider ao vivo) é o que garante que a resposta
    imediata reporte exatamente o que foi aceito, nunca um valor
    potencialmente diferente observado depois da execução."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    providers: dict[str, LLMProvider]
    repository: CouncilRepository
    service: CouncilExecutionService
    provider_execution_policy: ProviderExecutionPolicy


def _validate_internal_provider_config(
    settings: Settings, providers: dict[str, LLMProvider]
) -> None:
    """`claim_processor_provider`/`judge_provider`/`editor_provider`/
    `source_analyzer_provider` não precisam pertencer a
    `enabled_providers` (RunConfig, já documentado), mas precisam
    existir no registry de providers construído -- senão TODA execução
    falharia em runtime profundo (dentro de
    DebateEngine/SingleJudge/Editor/SourceAnalyzer) com um ValueError
    cru. Falhar aqui, no startup, com uma mensagem clara, é preferível a
    deixar isso estourar silenciosamente na primeira request.

    T02.4 (repair pós-revisão independente, MEDIUM): `source_analyzer_provider`
    estava AUSENTE desta checagem -- um `default_source_analyzer_provider`
    inválido sobrevivia ao startup inteiro, permitia que um Run fosse
    aceito/mintado durably, consumisse providers reais de debate, e só
    falhasse quando a Source Analysis começasse. As 4 chaves abaixo
    espelham EXATAMENTE os 4 papéis internos que
    `RunConfig.from_settings` lê de `Settings` (ver
    `app/orchestrator/config.py`) -- se um papel novo for adicionado a
    `RunConfig.from_settings` no futuro, adicione a chave equivalente
    aqui também (nenhum mecanismo automático os mantém em sincronia,
    deliberadamente, pra não introduzir um framework de capability
    registry fora do escopo desta correção)."""
    required = {
        "default_claim_processor_provider": settings.default_claim_processor_provider,
        "default_judge_provider": settings.default_judge_provider,
        "default_editor_provider": settings.default_editor_provider,
        "default_source_analyzer_provider": settings.default_source_analyzer_provider,
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


def _validate_default_quorum_feasibility(
    settings: Settings, providers: dict[str, LLMProvider]
) -> None:
    """Deployment Execution Configuration Boundary V1.

    Duas checagens distintas, deliberadamente NUNCA fundidas numa só:

    1. FORMA do quórum padrão -- construindo o `QuorumPolicy` REAL
       (`QuorumPolicy.from_settings`, app/orchestrator/config.py) e
       descartando o resultado. Isso reusa a ÚNICA implementação
       canônica de "positividade + `min_to_return <= min_for_debate`"
       que já existe -- NUNCA reimplementada aqui como uma segunda
       comparação que pudesse divergir da de `QuorumPolicy`. Antes
       desta correção, uma forma de quórum padrão inválida (ex.:
       `quorum_min_to_return=5, quorum_min_for_debate=2` num `.env` de
       deployment) sobrevivia ao bootstrap inteiro (engine/DB/registry/
       service construídos) e só falhava na primeira
       `RunConfig.from_settings()` de um Run real.

    2. FACTIBILIDADE do `min_to_return` padrão contra a cardinalidade do
       registry de participantes REALMENTE construído (`len(providers)`)
       -- NUNCA `all_provider_authorities`/união de papéis internos/
       contagem configurada/literal hardcoded. Um deployment cujo
       `min_to_return` padrão excede até o número TOTAL de providers
       disponíveis pra seleção nunca poderia satisfazer seu próprio
       quórum de retorno, mesmo selecionando TODOS eles. Igualdade é
       válida; `min_for_debate` pode legitimamente exceder a
       cardinalidade do registry (só significa que a crítica nunca
       roda -- ver `app.orchestrator.config.validate_quorum_feasibility`,
       cuja autoridade PER-RUN sobre o subconjunto selecionado numa
       execução real permanece inalterada e é reusada sem modificação
       -- esta função nunca a substitui, só cobre o caso de deployment
       ANTES de qualquer execução existir)."""
    try:
        quorum_policy = QuorumPolicy.from_settings(settings)
    except ValidationError as exc:
        raise ConfigurationError(
            "Configuração de quórum padrão inválida (quorum_min_for_debate="
            f"{settings.quorum_min_for_debate}, quorum_min_to_return="
            f"{settings.quorum_min_to_return}): {exc}"
        ) from exc

    participant_registry_size = len(providers)
    if quorum_policy.min_to_return > participant_registry_size:
        raise ConfigurationError(
            "Configuração de quórum padrão infactível -- quorum_min_to_return "
            f"({quorum_policy.min_to_return}) excede o número de providers "
            f"disponíveis no registry construído ({participant_registry_size}): "
            "nenhuma execução nova poderia satisfazer esse quórum de retorno "
            "mesmo selecionando todos os providers disponíveis."
        )


async def build_app_components(settings: Settings) -> AppComponents:
    """Monta a cadeia inteira. Chamado uma vez no lifespan de
    `create_app()` -- nunca por request (Decision Delta, revisão final:
    "endpoint criando pipeline manualmente por request" é exatamente o
    que isso evita).

    T02.2: `ProviderExecutionPolicy.from_settings(settings)` é chamado
    AQUI, e SÓ AQUI, uma única vez por aplicação composta -- o valor
    resolvido é então injetado, sem recálculo, em `build_all_providers`
    (que constrói os 3 `LLMProvider`) e em `CouncilExecutionService`
    (que o usa como snapshot de aceite durável). `ConfigurationError`
    nunca precisa envolver a validação de
    `attempt_timeout_seconds`/`max_transport_attempts_per_completion` --
    um `provider_timeout_seconds<=0`/`provider_max_retries<0` já falha
    aqui como `pydantic.ValidationError`, mesma convenção que
    `QuorumPolicy.from_settings`/`RunConfig.from_settings` já seguem pra
    qualquer outro valor de Settings semanticamente inválido (nunca um
    clamp silencioso)."""
    provider_execution_policy = ProviderExecutionPolicy.from_settings(settings)
    providers = build_all_providers(settings, provider_execution_policy)
    _validate_internal_provider_config(settings, providers)
    _validate_default_quorum_feasibility(settings, providers)

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
            runner=runner,
            repository=repository,
            providers=providers,
            provider_execution_policy=provider_execution_policy,
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
        provider_execution_policy=provider_execution_policy,
    )
