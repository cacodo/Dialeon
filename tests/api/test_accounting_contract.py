from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.presentation import mappers, schemas
from app.api.app import create_app
from app.config import Settings
from tests.api.helpers import make_components_factory
from tests.storage.fixtures import error_model_response, full_council_run_result, model_response


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _seed_success(components, result) -> str:
    await components.repository.save_success(result)
    return result.id


def test_http_contract_calls_it_estimated_cost_not_total_cost():
    assert "estimated_cost_usd" in schemas.AccountingSummary.model_fields
    assert "estimated_cost_usd" in schemas.RoundAccountingPublic.model_fields
    assert "total_cost_usd" not in schemas.AccountingSummary.model_fields
    assert "total_cost_usd" not in schemas.RoundAccountingPublic.model_fields


def test_estimated_cost_usd_none_stays_none_in_model_response():
    error_mr = error_model_response("gemini")
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={
            "responses": [*result.debate_result.initial_result.responses, error_mr],
            "has_unknown_accounting_components": True,
        }
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}/audit")

    audited_error = next(
        r for r in resp.json()["initial_round"]["responses"] if r["id"] == error_mr.id
    )
    assert audited_error["cost_usd"] is None


def test_estimated_cost_usd_zero_stays_zero_in_aggregate():
    zero_mr = model_response("local", model="self-hosted", cost_usd=0.0)
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={"responses": [*result.debate_result.initial_result.responses, zero_mr]}
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    assert resp.json()["accounting"]["estimated_cost_usd"] == result.total_cost_usd


def test_unknown_accounting_flag_preserved_in_estimated_cost_contract():
    error_mr = error_model_response("gemini")
    result = full_council_run_result()
    initial = result.debate_result.initial_result.model_copy(
        update={
            "responses": [*result.debate_result.initial_result.responses, error_mr],
            "has_unknown_accounting_components": True,
        }
    )
    debate = result.debate_result.model_copy(update={"initial_result": initial})
    result = result.model_copy(update={"debate_result": debate})

    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        resp = client.get(f"/runs/{run_id}")

    assert resp.json()["accounting"]["has_unknown_accounting_components"] is True


def test_mappers_module_never_imports_pricing_registry():
    import ast

    source = inspect.getsource(mappers)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "PricingRegistry" not in imported_names
    assert "DEFAULT_PRICING_REGISTRY" not in imported_names
    # confirma também que nenhuma chamada real .price(...) existe no corpo
    # (fora de docstrings) -- procurando por Call nodes com esse atributo
    calls_named_price = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "price"
    ]
    assert calls_named_price == []


def test_no_accounting_formula_changed_estimated_cost_equals_domain_total():
    result = full_council_run_result()
    app = create_app(settings=_settings(), components_factory=make_components_factory())
    with TestClient(app) as client:
        run_id = client.portal.call(_seed_success, app.state.components, result)
        detail = client.get(f"/runs/{run_id}").json()
        audit = client.get(f"/runs/{run_id}/audit").json()

    assert detail["accounting"]["estimated_cost_usd"] == result.total_cost_usd
    assert audit["accounting"]["estimated_cost_usd"] == result.total_cost_usd
    assert (
        audit["initial_round"]["accounting"]["estimated_cost_usd"]
        == result.debate_result.initial_result.total_cost_usd
    )
