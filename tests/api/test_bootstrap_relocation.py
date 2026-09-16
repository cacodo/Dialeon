from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import bootstrap
from app.api.app import create_app
from app.bootstrap import ConfigurationError
from app.config import Settings
from tests.api.helpers import make_components_factory


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_bootstrap_module_does_not_import_fastapi():
    source = inspect.getsource(bootstrap)
    tree = ast.parse(source)
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(m.startswith("fastapi") for m in imported_modules)


def test_api_app_module_does_not_duplicate_wiring():
    import app.api.app as api_app_module

    source = inspect.getsource(api_app_module)
    assert "build_all_providers(" not in source
    assert "DebateEngine(" not in source
    assert "SingleJudge(" not in source
    assert "CouncilRunner(" not in source
    assert "from app.bootstrap import" in source


def test_api_routes_module_does_not_duplicate_wiring():
    import app.api.routes as routes_module

    source = inspect.getsource(routes_module)
    assert "build_all_providers(" not in source
    assert "DebateEngine(" not in source
    assert "CouncilRunner(" not in source


def test_create_app_and_lifespan_still_work():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200


def test_engine_still_disposed_after_relocation():
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncEngine

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with patch.object(AsyncEngine, "dispose", autospec=True, wraps=AsyncEngine.dispose) as mocked:
        with TestClient(app):
            assert mocked.call_count == 0
        assert mocked.call_count == 1


def test_invalid_internal_config_still_fails_as_configuration_error_not_http():
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, default_judge_provider="provider-que-nao-existe")
    app = create_app(settings=settings, components_factory=build_app_components)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


def test_invalid_source_analyzer_config_fails_as_configuration_error_not_http():
    """T02.4 repair (MEDIUM, teste B) -- `default_source_analyzer_provider`
    inválido precisa ser rejeitado no MESMO ponto/contrato que
    `default_judge_provider`/`default_claim_processor_provider`/
    `default_editor_provider` já eram (ver
    `_validate_internal_provider_config`, app/bootstrap.py) -- ANTES do
    startup completar, nunca só quando Source Analysis rodar em runtime
    profundo. Achado da revisão independente: esta chave estava ausente
    da checagem original."""
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, default_source_analyzer_provider="provider-que-nao-existe")
    app = create_app(settings=settings, components_factory=build_app_components)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


@pytest.mark.asyncio
async def test_engine_disposed_when_failure_occurs_after_creation_before_return(monkeypatch):
    """Stage 14 (segunda revisão): se `init_db()` (ou qualquer passo
    entre a criação do engine e o `return AppComponents(...)`) falhar, o
    engine já criado precisa ser descartado -- senão fica órfão até o
    GC decidir (SQLAlchemy emite warning; em backends não-SQLite pode
    significar uma conexão de rede genuinamente aberta). A exceção
    ORIGINAL precisa continuar propagando intacta, não substituída por
    outra coisa."""
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.bootstrap import build_app_components

    async def _broken_init_db(engine):
        raise RuntimeError("falha real durante init_db, engine já existe")

    monkeypatch.setattr("app.bootstrap.init_db", _broken_init_db)

    with patch.object(AsyncEngine, "dispose", autospec=True, wraps=AsyncEngine.dispose) as mocked:
        with pytest.raises(RuntimeError, match="falha real durante init_db"):
            await build_app_components(_settings())
        assert mocked.call_count == 1


# ---------------------------------------------------------------------------
# Deployment Execution Configuration Boundary V1 -- quórum padrão de
# deployment (matriz D/F/G/H/I do contrato desta slice)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_quorum_valid_ordering_composes_successfully():
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        quorum_min_for_debate=2,
        quorum_min_to_return=1,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    assert components.service is not None
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_default_quorum_invalid_ordering_rejected_as_configuration_error():
    """Matriz D -- `quorum_min_to_return > quorum_min_for_debate`
    sobrevivia hoje ao bootstrap inteiro (engine/DB/registry/service
    construídos) e só falharia na primeira `RunConfig.from_settings()`
    de um Run real; agora falha aqui, como `ConfigurationError`, ANTES
    de qualquer infraestrutura ser criada."""
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, quorum_min_for_debate=1, quorum_min_to_return=2)

    with pytest.raises(ConfigurationError):
        await build_app_components(settings)


@pytest.mark.asyncio
async def test_default_min_to_return_below_registry_cardinality_accepted():
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        quorum_min_for_debate=2,
        quorum_min_to_return=2,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_default_min_to_return_equal_to_registry_cardinality_accepted():
    """Matriz D/seção 18-G -- igualdade é válida. O registry construído
    por `build_all_providers` tem sempre 3 providers (openai/anthropic/
    gemini) hoje -- este teste usa esse fato para exercitar a igualdade
    exata sem hardcodar o número na produção (a checagem em si usa
    `len(providers)`, nunca um literal)."""
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        quorum_min_for_debate=3,
        quorum_min_to_return=3,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_default_min_to_return_above_registry_cardinality_rejected():
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, quorum_min_for_debate=4, quorum_min_to_return=4)

    with pytest.raises(ConfigurationError, match="quorum_min_to_return"):
        await build_app_components(settings)


@pytest.mark.asyncio
async def test_default_min_for_debate_above_registry_cardinality_remains_valid():
    """Matriz D/seção 9/18-A -- `min_for_debate` pode legitimamente
    exceder a cardinalidade do registry inteiro; só significa que a
    crítica nunca roda, nunca que o deployment é infactível. Esta
    slice NUNCA adiciona `min_for_debate <= provider_count`."""
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        quorum_min_for_debate=999,
        quorum_min_to_return=1,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_invalid_default_quorum_rejected_before_database_engine_created():
    """Matriz F -- sentinela direto no símbolo de produção usado por
    `build_app_components`: uma configuração de quórum padrão inválida
    nunca deve alcançar `create_engine`/`init_db`."""
    from unittest.mock import patch

    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, quorum_min_for_debate=4, quorum_min_to_return=4)

    with patch(
        "app.bootstrap.create_engine",
        side_effect=AssertionError("create_engine nunca deveria ser chamado"),
    ):
        with pytest.raises(ConfigurationError):
            await build_app_components(settings)


@pytest.mark.asyncio
async def test_invalid_ordering_quorum_rejected_before_database_engine_created():
    from unittest.mock import patch

    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, quorum_min_for_debate=1, quorum_min_to_return=2)

    with patch(
        "app.bootstrap.create_engine",
        side_effect=AssertionError("create_engine nunca deveria ser chamado"),
    ):
        with pytest.raises(ConfigurationError):
            await build_app_components(settings)


@pytest.mark.asyncio
async def test_default_quorum_registry_check_uses_actual_registry_not_hardcoded_size(monkeypatch):
    """Matriz D -- prova que a checagem usa a cardinalidade REAL do
    registry construído (`len(providers)`), não um literal/constante:
    injeta um `build_all_providers` fake que devolve só 1 provider, e
    confirma que `quorum_min_to_return=2` (que seria válido contra os 3
    providers reais) passa a ser rejeitado contra esse registry menor."""
    from app.bootstrap import build_app_components

    def _fake_build_all_providers(settings, provider_execution_policy):
        return {"openai": object()}

    monkeypatch.setattr("app.bootstrap.build_all_providers", _fake_build_all_providers)
    settings = Settings(
        _env_file=None,
        quorum_min_for_debate=2,
        quorum_min_to_return=2,
        default_claim_processor_provider="openai",
        default_judge_provider="openai",
        default_editor_provider="openai",
        default_source_analyzer_provider="openai",
    )

    with pytest.raises(ConfigurationError, match="quorum_min_to_return"):
        await build_app_components(settings)


@pytest.mark.asyncio
async def test_missing_api_keys_still_compose_successfully_with_valid_execution_config():
    """Seção 17/18-F -- ausência de API key permanece estado de
    deployment válido; nunca validação de credencial, nunca chamada de
    provider."""
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        openai_api_key=None,
        anthropic_api_key=None,
        google_api_key=None,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    assert "openai" in components.providers  # composição concluída, sem checar credencial
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_round_dispatch_timeout_lower_than_provider_attempt_timeout_is_valid():
    """Seção 18-B -- ordens independentes entre os dois timeouts
    permanecem válidas nos dois sentidos; nenhuma relação nova é
    inventada por esta slice."""
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        provider_timeout_seconds=100,
        orchestrator_round_dispatch_timeout_seconds=5.0,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_round_dispatch_timeout_higher_than_provider_attempt_timeout_is_valid():
    """Seção 18-C -- o inverso do teste acima também é válido."""
    from app.bootstrap import build_app_components

    settings = Settings(
        _env_file=None,
        provider_timeout_seconds=5,
        orchestrator_round_dispatch_timeout_seconds=100.0,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_max_output_tokens_per_call_may_exceed_max_total_tokens_end_to_end():
    """Seção 18-D -- exercitada até o RunConfig final construído por
    `RunConfig.from_settings`, não só ao nível de Settings (já coberto
    em tests/test_settings.py)."""
    from app.bootstrap import build_app_components
    from app.orchestrator.config import RunConfig

    settings = Settings(
        _env_file=None,
        default_max_output_tokens_per_call=100_000,
        default_max_total_tokens=1,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    components = await build_app_components(settings)
    try:
        run_config = RunConfig.from_settings(
            settings, question="pergunta", enabled_providers=["openai"]
        )
        assert run_config.max_output_tokens_per_call == 100_000
        assert run_config.max_total_tokens == 1
    finally:
        await components.engine.dispose()


@pytest.mark.asyncio
async def test_valid_deployment_defaults_produce_strictly_json_serializable_run_config_public():
    """Seção 14/matriz H -- para uma configuração de deployment válida
    (aceita pelo boundary desta slice), `RunConfig.from_settings(...)`
    -> `RunConfigPublic` -> serialização JSON ESTRITA (mesma disciplina
    de `fastapi.responses.JSONResponse`, que usa `allow_nan=False`)
    precisa ter sucesso pros campos numéricos relevantes -- nunca
    Infinity/NaN sobrevivendo até a superfície pública."""
    import json

    from app.bootstrap import build_app_components
    from app.orchestrator.config import RunConfig
    from app.presentation.mappers import run_config_public

    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    components = await build_app_components(settings)
    try:
        run_config = RunConfig.from_settings(
            settings, question="pergunta", enabled_providers=["openai"]
        )
        public = run_config_public(run_config)
        dumped = public.model_dump(mode="json")
        # allow_nan=False -- levanta ValueError se qualquer campo numérico
        # fosse NaN/Infinity; sucesso aqui prova serializabilidade estrita.
        json.dumps(dumped, allow_nan=False)
    finally:
        await components.engine.dispose()


def test_non_finite_deployment_defaults_rejected_before_reaching_public_serialization():
    """Contraparte negativa do teste acima -- um deployment com
    `default_max_cost_usd=inf` é rejeitado na composição de `Settings`
    (ver tests/test_settings.py), então NUNCA existe um `RunConfig`/
    `RunConfigPublic` construído a partir dele pra alcançar
    `JSONResponse`/serialização estrita em primeiro lugar."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_max_cost_usd=float("inf"))


# ---------------------------------------------------------------------------
# Deployment Execution Configuration Boundary V1 (F1, repair pós-revisão
# independente -- MEDIUM) -- a reprodução da revisão independente
# demonstrou: construir Settings válido -> mutar -> build_app_components
# -> create_engine alcançado. Prova de que esse bypass agora é
# impossível, na fronteira real de composição (nunca via
# object.__setattr__/subversão de modelo de objeto Python).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_mutation_after_construction_cannot_bypass_deployment_boundary():
    """Reprodução DIRETA do achado da revisão independente (seção 2 do
    contrato deste repair): a atribuição em si é rejeitada, o Settings
    original permanece válido/inalterado, e uma composição subsequente
    com esse MESMO objeto continua bem-sucedida (prova que nada foi
    corrompido pela tentativa de mutação)."""
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    original_cost = settings.default_max_cost_usd

    with pytest.raises(ValidationError):
        settings.default_max_cost_usd = float("inf")

    assert settings.default_max_cost_usd == original_cost

    components = await build_app_components(settings)
    await components.engine.dispose()


@pytest.mark.asyncio
async def test_settings_mutation_attempts_never_reach_create_engine():
    """Mesma reprodução, mas com sentinela direto em `create_engine` --
    prova que mesmo se a atribuição TIVESSE (hipoteticamente) sucesso, a
    composição não prosseguiria silenciosamente com um valor inválido;
    aqui a atribuição já é rejeitada antes de qualquer chamada."""
    from unittest.mock import patch

    settings = Settings(_env_file=None)

    with patch(
        "app.bootstrap.create_engine",
        side_effect=AssertionError("create_engine nunca deveria ser chamado"),
    ):
        with pytest.raises(ValidationError):
            settings.orchestrator_round_dispatch_timeout_seconds = float("inf")


@pytest.mark.asyncio
async def test_app_components_settings_reference_remains_frozen_after_composition():
    """Seção 8 do contrato -- `AppComponents.settings` referencia o
    MESMO objeto `Settings` congelado (nunca uma cópia mutável nova) --
    tentar mutar através dela continua rejeitado depois que a composição
    já terminou."""
    from app.bootstrap import build_app_components

    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:")
    components = await build_app_components(settings)
    try:
        assert components.settings is settings
        with pytest.raises(ValidationError):
            components.settings.default_max_output_tokens_per_call = 0
        assert components.settings.default_max_output_tokens_per_call == (
            settings.default_max_output_tokens_per_call
        )
    finally:
        await components.engine.dispose()
