"""Repair M2: um fragmento de auditoria bruto do provider nunca impede
salvar E reler o resultado terminal.

Os dois caminhos confirmados, pelo pipeline REAL inteiro (mesmo harness
de `test_interpretation_failure_audit.py`: provider fake que implementa
só `_call_api`, então usage/custo/pricing vêm do `LLMProvider.complete()`
real; SQLite de arquivo; `get_run` relê do banco):

A. `proposed_numeric_assertion` -> `DeterministicVerificationAttempt.raw_proposal`;
B. entrada de SourceAnalysis -> `RejectedSourceEntry.raw_entry`.

Dois grupos de teste:

- CONTRATO: profundidades que qualquer runtime suportado parseia (33,
  256); o resultado lógico depende só do limite da aplicação
  (app/audit_fragment.py) e é idêntico em Python 3.11/3.13/3.14. 256
  também excede o limite fixo de serialização do pydantic-core (255),
  que quebrava `model_dump(mode="json")`/a resposta HTTP do resultado.
- FAIXA DO M2 (900-980, resposta de ~2 KB, dentro de
  `max_output_tokens_per_call=4096`): no código anterior ao repair, em
  Python 3.11 parte dessa faixa passava pelo `json.loads` e fazia o
  `json.dumps` do bind estourar `RecursionError` dentro da transação
  terminal (rollback inteiro, run presa em `running`). Se o runtime da
  vez consegue ou não parsear cada profundidade depende da pilha já
  consumida -- por isso esses testes nunca assumem o caminho: quando o
  payload foi parseado, o fragmento TEM de estar degradado; quando não
  foi, o attempt é `interpretation_failed` com o texto bruto preservado
  (repair H1). Nos dois casos a run completa, salva e relê.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from app.application.service import CouncilExecutionService
from app.audit_fragment import MAX_AUDIT_FRAGMENT_DEPTH
from app.council.runner import CouncilRunner
from app.debate.debate_engine import DebateEngine
from app.editor.compose import Editor
from app.judge.single_judge import SingleJudge
from app.models.provider_models import CompletionRequest, ProviderExecutionPolicy, TokenUsage
from app.presentation.mappers import completed_run_audit, completed_run_response
from app.providers.base import LLMProvider
from app.source_analysis.analyzer import SourceAnalyzer
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import CompletedRunRecord
from app.storage.repository import CouncilRepository
from tests.council.test_interpretation_failure_audit import (
    _INPUT_TOKENS,
    _OUTPUT_TOKENS,
    _PRICING,
    _PROVIDERS,
    _UNIT_COST,
    _CallLog,
    _assert_accounting_matches_real_calls,
    _assert_reload_is_lossless,
    _json_after,
    _run_config,
    _stage_of,
    _valid_output,
)

M2_BAND = (900, 930, 950, 960, 970, 980)
ABOVE_CONTRACT = (MAX_AUDIT_FRAGMENT_DEPTH + 1, 256)


def _nested(depth: int) -> list:
    """Lista aninhada `depth` níveis, construída iterativamente."""
    value: list = []
    for _ in range(depth - 1):
        value = [value]
    return value


def _nested_text(depth: int) -> str:
    return "[" * depth + "]" * depth


Override = Callable[[CompletionRequest], str]


class _ScriptedProvider(LLMProvider):
    """Implementa SÓ `_call_api`; `overrides[stage]` substitui a saída
    válida da primeira chamada daquele estágio."""

    def __init__(self, name: str, log: _CallLog, overrides: dict[str, Override],
                 reported_model: str | None = None):
        super().__init__("fake-key", 30.0, 0, _PRICING)
        self.provider_name = name
        self._log = log
        self._overrides = overrides
        self._seen: dict[str, int] = {}
        self._reported_model = reported_model or f"{name}-reported"

    @property
    def default_model(self) -> str:
        return f"{self.provider_name}-requested"

    async def _call_api(self, request):
        stage = _stage_of(request)
        self._log.calls.append((self.provider_name, stage))
        index = self._seen.get(stage, 0)
        self._seen[stage] = index + 1
        override = self._overrides.get(stage) if index == 0 else None
        text = override(request) if override else _valid_output(self.provider_name, stage, request)
        usage = TokenUsage(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS)
        return text, usage, self._reported_model, "stop"


async def _execute(tmp_path, overrides: dict[str, Override], *, reported_model: str | None = None):
    log = _CallLog()
    providers = {
        name: _ScriptedProvider(
            name,
            log,
            overrides if name == "anthropic" else {},
            reported_model=reported_model if name == "anthropic" else None,
        )
        for name in _PROVIDERS
    }
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'm2.db'}")
    try:
        await init_db(engine)
        repository = CouncilRepository(make_session_factory(engine))
        service = CouncilExecutionService(
            runner=CouncilRunner(
                debate_engine=DebateEngine(providers),
                source_analyzer=SourceAnalyzer(providers),
                judge=SingleJudge(providers),
                editor=Editor(providers),
            ),
            repository=repository,
            providers=providers,
            provider_execution_policy=ProviderExecutionPolicy(
                attempt_timeout_seconds=30.0, max_transport_attempts_per_completion=1
            ),
        )
        result = await service.run(_run_config())
        record = await repository.get_run(result.id)
        summaries = await repository.list_runs()
    finally:
        await engine.dispose()
    # accepted_run terminou: a linha de aceite foi substituída pelo
    # registro terminal canônico na MESMA transação.
    assert isinstance(record, CompletedRunRecord)
    assert [(s.id, s.status) for s in summaries] == [(result.id, "completed")]
    _assert_public_contract_renders(record)
    return result, record.council_run_result, log


def _assert_public_contract_renders(record: CompletedRunRecord) -> None:
    """POST /runs e GET /runs/{id}/audit serializam estes mesmos modelos
    públicos; JSON estrito (sem NaN/Infinity), como a resposta HTTP."""
    kwargs = {
        "provider_execution_policy": record.provider_execution_policy,
        "default_model_authority_snapshot": record.default_model_authority_snapshot,
    }
    result = record.council_run_result
    response = completed_run_response(result, **kwargs).model_dump(mode="json")
    audit = completed_run_audit(result, **kwargs).model_dump(mode="json")
    json.dumps(response, allow_nan=False)
    text = json.dumps(audit, allow_nan=False)
    domain_reasons = sorted(
        [a.raw_proposal_omitted_reason for a in result.debate_result.numeric_verification_attempts]
        + [
            c.raw_entry_omitted_reason
            for c in (result.source_analysis_result.claim_results if result.source_analysis_result else [])
            if c.kind == "rejected"
        ],
        key=str,
    )
    public_reasons = sorted(
        [value for key, value in _flat_items(json.loads(text)) if key.endswith("_omitted_reason")], key=str
    )
    assert public_reasons == domain_reasons


def _flat_items(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                yield key, child
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)


# ---------------------------------------------------------------------------
# A. proposed_numeric_assertion -> raw_proposal
# ---------------------------------------------------------------------------

_NUMERIC_CLAIM = "Paris é a capital da França (numérica)"


def _extraction_with_proposal(proposal_json: str) -> str:
    return (
        '{"claims": [{"text": "' + _NUMERIC_CLAIM + '", "revises_claim_id": null, '
        '"proposed_numeric_assertion": ' + proposal_json + "}]}"
    )


async def _run_numeric(tmp_path, proposal_json: str, *, reported_model=None):
    raw_text = _extraction_with_proposal(proposal_json)
    result, reloaded, log = await _execute(
        tmp_path, {"extraction_r1": lambda _request: raw_text}, reported_model=reported_model
    )
    priced = reported_model is None
    for r in (result, reloaded):
        extraction = next(
            a for a in r.debate_result.claim_processing_attempts if a.raw_output_text == raw_text
        )
        assert extraction.transport_status == "success"
        assert extraction.usage == TokenUsage(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS)
        if priced:
            assert extraction.cost_usd == pytest.approx(_UNIT_COST)
            assert extraction.pricing_provenance.source_id == "interpretation-audit-test"
        else:
            assert extraction.cost_usd is None
            assert extraction.pricing_provenance is None
        assert r.judge_result.verdict is not None
    _assert_accounting_matches_real_calls(result, reloaded, log, priced=priced)
    _assert_reload_is_lossless(result, reloaded)
    return result, reloaded, raw_text


def _numeric_attempt(r):
    """A tentativa numérica da claim extraída do payload -- e a prova de
    que a claim válida sobreviveu e foi avaliada pelo Judge."""
    claim = next(c for c in r.debate_result.claims if c.text == _NUMERIC_CLAIM)
    assert claim.id in {a.claim_id for a in r.judge_result.verdict.claim_assessments}
    attempt = next(a for a in r.debate_result.numeric_verification_attempts if a.claim_id == claim.id)
    assert attempt.state == "invalid_proposal"
    return attempt


def _payload_was_parsed(r, raw_text: str) -> bool:
    extraction = next(a for a in r.debate_result.claim_processing_attempts if a.raw_output_text == raw_text)
    if extraction.parse_status == "accepted":
        return True
    assert extraction.parse_status == "interpretation_failed"  # caminho H1, texto preservado
    return False


@pytest.mark.parametrize("depth", ABOVE_CONTRACT)
async def test_numeric_proposal_above_contract_is_degraded_explicitly(tmp_path, depth):
    result, reloaded, raw_text = await _run_numeric(tmp_path, _nested_text(depth))

    for r in (result, reloaded):
        assert _payload_was_parsed(r, raw_text)
        attempt = _numeric_attempt(r)
        assert attempt.raw_proposal is None
        assert attempt.raw_proposal_omitted_reason == "complexity_limit_exceeded"


@pytest.mark.parametrize("depth", M2_BAND)
async def test_m2_band_numeric_proposal_never_loses_the_terminal_history(tmp_path, depth):
    proposal = _nested_text(depth)
    assert len(_extraction_with_proposal(proposal)) < 2100  # ~2 KB, cabe em 4096 tokens

    result, reloaded, raw_text = await _run_numeric(tmp_path, proposal)

    for r in (result, reloaded):
        if _payload_was_parsed(r, raw_text):
            attempt = _numeric_attempt(r)
            assert attempt.raw_proposal is None
            assert attempt.raw_proposal_omitted_reason == "complexity_limit_exceeded"


async def test_numeric_proposal_at_contract_limit_is_kept_intact(tmp_path):
    result, reloaded, _raw = await _run_numeric(tmp_path, _nested_text(MAX_AUDIT_FRAGMENT_DEPTH))

    for r in (result, reloaded):
        attempt = _numeric_attempt(r)
        assert attempt.raw_proposal == _nested(MAX_AUDIT_FRAGMENT_DEPTH)
        assert attempt.raw_proposal_omitted_reason is None


async def test_normal_invalid_numeric_proposal_is_unchanged(tmp_path):
    proposal = {"kind": "arithmetic", "left": "dois", "operator": "+", "right": "2"}
    result, reloaded, _raw = await _run_numeric(tmp_path, json.dumps(proposal))

    for r in (result, reloaded):
        attempt = _numeric_attempt(r)
        assert attempt.raw_proposal == proposal
        assert attempt.raw_proposal_omitted_reason is None


async def test_degraded_numeric_proposal_keeps_unknown_cost_truthful(tmp_path):
    """Modelo sem preço conhecido: custo continua `None`, nunca inventado."""
    result, reloaded, _raw = await _run_numeric(
        tmp_path, _nested_text(256), reported_model="unpriced-model"
    )

    for r in (result, reloaded):
        assert _numeric_attempt(r).raw_proposal_omitted_reason == "complexity_limit_exceeded"


# ---------------------------------------------------------------------------
# B. SourceAnalysis -> RejectedSourceEntry.raw_entry
# ---------------------------------------------------------------------------


def _source_with_extra_entry(extra_entry_json: str) -> Override:
    def output(request: CompletionRequest) -> str:
        claims = _json_after(request.messages[0].content, "CLAIMS_ATUAIS")
        relations = [json.dumps({"claim_id": c["claim_id"], "relation": "unresolved"}) for c in claims]
        return '{"claim_relations": [' + ", ".join([*relations, extra_entry_json]) + "]}"

    return output


def _unknown_entry(nested_json: str) -> str:
    return '{"claim_id": "id-que-nao-existe", "relation": "supports", "nested": ' + nested_json + "}"


async def _run_source(tmp_path, nested_json: str, *, reported_model=None):
    entry_json = _unknown_entry(nested_json)
    result, reloaded, log = await _execute(
        tmp_path, {"source": _source_with_extra_entry(entry_json)}, reported_model=reported_model
    )
    priced = reported_model is None
    for r in (result, reloaded):
        source = r.source_analysis_result
        assert source.skipped_reason is None
        first = source.attempts[0]
        assert entry_json in first.raw_output_text  # texto bruto integral preservado
        for attempt in source.attempts:
            assert attempt.usage == TokenUsage(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS)
            if priced:
                assert attempt.cost_usd == pytest.approx(_UNIT_COST)
                assert attempt.pricing_provenance.source_id == "interpretation-audit-test"
            else:
                assert attempt.cost_usd is None
                assert attempt.pricing_provenance is None
        # toda claim corrente mantém sua relação válida
        relations = [c for c in source.claim_results if c.kind == "relation"]
        assert {c.claim_id for c in relations} == {c.id for c in r.debate_result.claims}
        assert r.judge_result.verdict is not None
    _assert_accounting_matches_real_calls(result, reloaded, log, priced=priced)
    _assert_reload_is_lossless(result, reloaded)
    return result, reloaded


def _source_payload_was_parsed(r) -> bool:
    first = r.source_analysis_result.attempts[0]
    if first.parse_status == "accepted":
        return True
    assert first.parse_status == "interpretation_failed"  # caminho H1; o retry seguiu o contrato
    assert r.source_analysis_result.attempts[1].parse_status == "accepted"
    return False


def _rejected_unknown(r):
    rejected = [c for c in r.source_analysis_result.claim_results if c.kind == "rejected"]
    assert len(rejected) == 1
    assert rejected[0].claim_id is None
    assert rejected[0].reason == "invalid_entry"
    return rejected[0]


@pytest.mark.parametrize("nested_depth", ABOVE_CONTRACT)
async def test_source_raw_entry_above_contract_is_degraded_explicitly(tmp_path, nested_depth):
    # A entrada é o 1º nível: `nested` com N níveis totaliza N + 1.
    result, reloaded = await _run_source(tmp_path, _nested_text(nested_depth))

    for r in (result, reloaded):
        assert _source_payload_was_parsed(r)
        entry = _rejected_unknown(r)
        assert entry.raw_entry is None
        assert entry.raw_entry_omitted_reason == "complexity_limit_exceeded"


@pytest.mark.parametrize("nested_depth", M2_BAND)
async def test_m2_band_source_raw_entry_never_loses_the_terminal_history(tmp_path, nested_depth):
    result, reloaded = await _run_source(tmp_path, _nested_text(nested_depth))

    for r in (result, reloaded):
        if _source_payload_was_parsed(r):
            entry = _rejected_unknown(r)
            assert entry.raw_entry is None
            assert entry.raw_entry_omitted_reason == "complexity_limit_exceeded"


async def test_source_raw_entry_within_contract_is_kept_intact(tmp_path):
    nested_depth = MAX_AUDIT_FRAGMENT_DEPTH - 1  # 31 + a entrada = 32: cabe
    result, reloaded = await _run_source(tmp_path, _nested_text(nested_depth))

    for r in (result, reloaded):
        entry = _rejected_unknown(r)
        assert entry.raw_entry == json.loads(_unknown_entry(_nested_text(nested_depth)))
        assert entry.raw_entry_omitted_reason is None


async def test_degraded_source_raw_entry_keeps_unknown_cost_truthful(tmp_path):
    result, reloaded = await _run_source(tmp_path, _nested_text(256), reported_model="unpriced-model")

    for r in (result, reloaded):
        assert _rejected_unknown(r).raw_entry_omitted_reason == "complexity_limit_exceeded"
