"""
Historical Non-Finite Execution-Limit Public Representation V1 --
matriz de cobertura de lifecycle root (T9-T16 do contrato desta slice):
completed/quorum failure/running/failed, cada um em detail
(`GET /runs/{id}`) E audit (`GET /runs/{id}/audit`), pra um
`RunConfig` histórico com `max_cost_usd`/`round_dispatch_timeout_seconds`
= +inf.

Todos os 4 lifecycle roots passam pelo MESMO `run_config_public`
(app/presentation/mappers.py) -- esta cobertura protege o contrato de
lifecycle mesmo que a implementação un dia pare de compartilhar
exatamente a mesma função.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import full_council_run_result, now, quorum_failure_exception, run_config


def _settings() -> Settings:
    return Settings(_env_file=None)


def _infinite_run_config():
    return run_config(max_cost_usd=float("inf"), round_dispatch_timeout_seconds=float("inf"))


async def _seed_completed_infinite(components) -> str:
    result = full_council_run_result(run_config=_infinite_run_config())
    await components.repository.save_success(result)
    return result.id


async def _seed_quorum_failure_infinite(components) -> str:
    exc = quorum_failure_exception()
    return await components.repository.save_quorum_failure(
        exc, run_config=_infinite_run_config(), started_at=now(), failed_at=now()
    )


async def _seed_running_infinite(components, run_id: str) -> str:
    await components.repository.save_accepted(
        run_id,
        run_config=_infinite_run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )
    return run_id


async def _seed_unexpected_failed_infinite(components, run_id: str) -> str:
    await components.repository.save_accepted(
        run_id,
        run_config=_infinite_run_config(),
        started_at=now(),
        provider_execution_policy=components.provider_execution_policy,
    )
    await components.repository.save_unexpected_failure(
        run_id,
        failed_at=now(),
        failure_classification="WeirdBug",
        failure_message="Erro interno inesperado durante a execução.",
    )
    return run_id


def _assert_strict_json_positive_infinity(resp):
    assert resp.status_code == 200
    # Review F3 (precisão de comentário) -- `resp.json()` sozinho NÃO
    # prova serialização JSON estrita: o decodificador `json` da
    # stdlib (usado por baixo do capô aqui) aceita por padrão os
    # literais NÃO-STANDARD `Infinity`/`-Infinity`/`NaN` como extensão
    # (`parse_constant`), então um corpo de resposta que tivesse
    # vazado um `Infinity` cru ainda decodificaria "com sucesso" de
    # volta pra um float Python. `resp.json()` aqui só prova que o
    # corpo é CONSUMÍVEL como JSON (sintaticamente bem formado o
    # bastante pro decodificador permissivo). A prova POSITIVA de que
    # nenhum NaN/Infinity não-standard sobrevive é a re-serialização
    # abaixo com `allow_nan=False` -- ela levanta `ValueError` se
    # `body` contiver qualquer `float('inf')`/`float('nan')` real
    # (inclusive um que tivesse sido silenciosamente aceito pela
    # decodificação permissiva acima).
    body = resp.json()
    json.dumps(body, allow_nan=False)
    config = body["config"]
    assert config["max_cost_usd"] == "positive_infinity"
    assert config["round_dispatch_timeout_seconds"] == "positive_infinity"
    return body


# ---------------------------------------------------------------------------
# T9/T10 -- completed (detail + audit)
# ---------------------------------------------------------------------------


def test_t9_completed_detail_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_completed_infinite, app.state.components)
        resp = client.get(f"/runs/{run_id}")
    _assert_strict_json_positive_infinity(resp)


def test_t10_completed_audit_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_completed_infinite, app.state.components)
        resp = client.get(f"/runs/{run_id}/audit")
    _assert_strict_json_positive_infinity(resp)


# ---------------------------------------------------------------------------
# T11/T12 -- quorum failure (detail + audit)
# ---------------------------------------------------------------------------


def test_t11_quorum_failure_detail_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_quorum_failure_infinite, app.state.components)
        resp = client.get(f"/runs/{run_id}")
    _assert_strict_json_positive_infinity(resp)


def test_t12_quorum_failure_audit_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_quorum_failure_infinite, app.state.components)
        resp = client.get(f"/runs/{run_id}/audit")
    _assert_strict_json_positive_infinity(resp)


# ---------------------------------------------------------------------------
# T13/T14 -- running/accepted (detail + audit)
# ---------------------------------------------------------------------------


def test_t13_running_detail_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(
            _seed_running_infinite, app.state.components, "run-running-inf-1"
        )
        resp = client.get(f"/runs/{run_id}")
    _assert_strict_json_positive_infinity(resp)


def test_t14_running_audit_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(
            _seed_running_infinite, app.state.components, "run-running-inf-2"
        )
        resp = client.get(f"/runs/{run_id}/audit")
    _assert_strict_json_positive_infinity(resp)


# ---------------------------------------------------------------------------
# T15/T16 -- unexpected failed (detail + audit)
# ---------------------------------------------------------------------------


def test_t15_unexpected_failed_detail_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(
            _seed_unexpected_failed_infinite, app.state.components, "run-failed-inf-1"
        )
        resp = client.get(f"/runs/{run_id}")
    _assert_strict_json_positive_infinity(resp)


def test_t16_unexpected_failed_audit_strict_json_with_positive_infinity_token():
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(
            _seed_unexpected_failed_infinite, app.state.components, "run-failed-inf-2"
        )
        resp = client.get(f"/runs/{run_id}/audit")
    _assert_strict_json_positive_infinity(resp)
