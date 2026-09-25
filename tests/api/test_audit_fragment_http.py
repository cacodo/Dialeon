"""Repair M2 -- fronteira HTTP real com fragmentos de auditoria que o
`json.loads` aceita mas que não são Unicode válido (substituto isolado).

provider fake (pipeline REAL: DebateEngine/SourceAnalyzer/Judge/Editor)
-> POST /runs -> persistência -> GET /runs/{id} -> GET /runs/{id}/audit.

Antes do repair, o substituto isolado salvava e relia, e só a
serialização pública (Pydantic JSON/HTTP/CLI) falhava -- um 500
permanente no audit daquele run.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.bootstrap import AppComponents
from app.config import Settings
from app.council.runner import CouncilRunner
from app.application.service import CouncilExecutionService
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.models.provider_models import ProviderExecutionPolicy
from app.source_analysis.analyzer import SourceAnalyzer
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.repository import CouncilRepository
from tests.council.test_audit_fragment_persistence import (
    _NUMERIC_CLAIM,
    _PROVIDERS,
    _ScriptedProvider,
    _extraction_with_proposal,
    _flat_items,
    _source_with_extra_entry,
)
from tests.council.test_interpretation_failure_audit import (
    _INPUT_TOKENS,
    _OUTPUT_TOKENS,
    _SOURCE,
    _UNIT_COST,
    _CallLog,
)

# Texto JSON como o provider o devolveria: escapes \\uXXXX de substitutos
# isolados (o `json.loads` da aplicação os transforma em code points
# substitutos de verdade).
FRAGMENTS = {
    "surrogate_value": '{"x": "a\\ud800b", "y": ["\\udfff"]}',
    "surrogate_key": '{"\\ud800": 1, "ok": {"\\udfff": "v"}}',
}


def _factory(overrides, log: _CallLog):
    policy = ProviderExecutionPolicy(attempt_timeout_seconds=30.0, max_transport_attempts_per_completion=1)

    async def factory(settings):
        providers = {
            name: _ScriptedProvider(name, log, overrides if name == "anthropic" else {})
            for name in _PROVIDERS
        }
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        session_factory = make_session_factory(engine)
        repository = CouncilRepository(session_factory)
        service = CouncilExecutionService(
            runner=CouncilRunner(
                debate_engine=DebateEngine(providers),
                source_analyzer=SourceAnalyzer(providers),
                judge=SingleJudge(providers),
                editor=Editor(providers),
            ),
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

    return factory


def _unknown_source_entry(fragment_json: str) -> str:
    return '{"claim_id": "id-que-nao-existe", "relation": "supports", "raw": ' + fragment_json + "}"


@pytest.mark.parametrize("name", sorted(FRAGMENTS))
def test_surrogate_fragments_never_break_run_detail_or_audit(name):
    fragment = FRAGMENTS[name]
    extraction_text = _extraction_with_proposal(fragment)
    entry_text = _unknown_source_entry(fragment)
    log = _CallLog()
    overrides = {
        "extraction_r1": lambda _request: extraction_text,
        "source": _source_with_extra_entry(entry_text),
    }
    app = create_app(settings=Settings(_env_file=None), components_factory=_factory(overrides, log))

    with TestClient(app) as client:
        created = client.post(
            "/runs",
            json={"question": "Qual é a capital da França?", "enabled_providers": list(_PROVIDERS), "source_text": _SOURCE},
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        detail = client.get(f"/runs/{run_id}")
        audit = client.get(f"/runs/{run_id}/audit")
        record = client.portal.call(app.state.components.repository.get_run, run_id)

    assert detail.status_code == 200
    assert audit.status_code == 200
    body = json.loads(audit.text)  # JSON válido
    json.dumps(body, allow_nan=False)
    items = list(_flat_items(body))

    # fragmento degradado explicitamente nos DOIS caminhos
    assert [v for k, v in items if k == "raw_proposal_omitted_reason" and v is not None] == ["non_json_value"]
    assert [v for k, v in items if k == "raw_entry_omitted_reason" and v is not None] == ["non_json_value"]
    # a claim válida sobreviveu
    assert _NUMERIC_CLAIM in [v for k, v in items if k == "text"]
    # texto bruto integral do provider preservado nos attempts
    raw_texts = [v for k, v in items if k == "raw_output_text" and isinstance(v, str)]
    assert extraction_text in raw_texts
    assert any(entry_text in text for text in raw_texts)
    # accounting truthful: toda chamada real aparece uma vez, com usage e custo
    accounting = body["accounting"]
    assert accounting["total_input_tokens"] == len(log.calls) * _INPUT_TOKENS
    assert accounting["total_output_tokens"] == len(log.calls) * _OUTPUT_TOKENS
    assert accounting["estimated_cost_usd"] == pytest.approx(len(log.calls) * _UNIT_COST)
    assert accounting["has_unknown_accounting_components"] is False

    # o mesmo run, direto no serializer Pydantic (usado pela CLI --json)
    result = record.council_run_result
    assert json.loads(result.model_dump_json()) == result.model_dump(mode="json")
