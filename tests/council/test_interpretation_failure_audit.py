"""Audit-truth na fronteira response -> interpretation (repair do H1).

Invariante: uma vez que um provider RETORNOU uma resposta, essa chamada
permanece truthfully auditável e contabilizável -- mesmo quando a
interpretação da saída (parse/schema/validação de referências) falha de
um jeito fora do vocabulário de erro antecipado por cada estágio.

Triggers REAIS reproduzidos (nunca monkeypatch do parser):
  A. inteiro JSON com mais de 4300 dígitos -> `json.loads` levanta
     `ValueError` puro (não `json.JSONDecodeError`);
  B. aninhamento JSON profundo -> `json.loads` levanta `RecursionError`.

Pipeline REAL inteiro (DebateEngine, SourceAnalyzer, SingleJudge, Editor,
CouncilRunner, CouncilExecutionService, CouncilRepository em SQLite de
arquivo), com um provider fake que implementa só `_call_api` -- então
usage/cost/pricing_provenance/latência/finish_reason vêm do
`LLMProvider.complete()` real, nunca fabricados pelo teste.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from app.application.service import CouncilExecutionService
from app.council.runner import CouncilRunner
from app.debate.claim_extraction import CLAIM_EXTRACTION_CONTRACT_VERSION
from app.debate.debate_engine import DebateEngine
from app.debate.processing_record import ClaimProcessingAttempt
from app.editor.attempt import EditorAttempt
from app.editor.compose import Editor
from app.editor.context import EDITOR_CONTRACT_VERSION
from app.editor.primary_answer import PRIMARY_ANSWER_CONTRACT_VERSION
from app.judge.attempt import JudgeAttempt
from app.judge.context import JUDGE_CONTRACT_VERSION
from app.judge.single_judge import SingleJudge
from app.models.provider_models import (
    CompletionRequest,
    ProviderErrorInfo,
    ProviderErrorType,
    ProviderExecutionPolicy,
    TokenUsage,
)
from app.orchestrator.config import QuorumPolicy, RunConfig
from app.providers.base import LLMProvider
from app.providers.pricing import ModelRate, PricingRegistry
from app.source_analysis.analyzer import SourceAnalyzer
from app.source_analysis.attempt import SourceAnalysisAttempt
from app.source_analysis.context import SOURCE_ANALYSIS_CONTRACT_VERSION
from app.storage.database import create_engine, init_db, make_session_factory
from app.storage.records import CompletedRunRecord
from app.storage.repository import CouncilRepository
from app.structured_output import INTERPRETATION_FAILURE_MESSAGE

_BIGINT = '{"x": 1' + "1" * 5000 + "}"
_DEEP = "[" * 100_000 + "]" * 100_000
PAYLOADS = {"bigint": _BIGINT, "deep": _DEEP}

_INPUT_TOKENS = 100
_OUTPUT_TOKENS = 50
_UNIT_COST = _INPUT_TOKENS * 1.0 / 1_000_000 + _OUTPUT_TOKENS * 2.0 / 1_000_000
_PROVIDERS = ("openai", "anthropic", "gemini")
_PRICING = PricingRegistry(
    {
        (p, f"{p}-reported"): ModelRate(
            input_usd_per_million_tokens=1.0, output_usd_per_million_tokens=2.0
        )
        for p in _PROVIDERS
    },
    source_id="interpretation-audit-test",
)
_SOURCE = "Paris é a capital da França."


def _json_after(body: str, header: str):
    rest = body[body.index("\n", body.index(header)) + 1 :]
    return json.JSONDecoder().raw_decode(rest)[0]


def _stage_of(request: CompletionRequest) -> str:
    system = request.system_prompt or ""
    body = request.messages[0].content
    if not system:
        return "participant"
    if "rodada de crítica" in system:
        return "critique"
    if "extrator de afirmações" in system:
        return "extraction_r2" if "CLAIMS_ANTERIORES" in body else "extraction_r1"
    if "Você analisa uma FONTE" in system:
        return "source"
    if "Você é o juiz" in system:
        return "judge"
    if "planejador de apresentação" in system:
        return "editor"
    if "Você seleciona, entre afirmações" in system:
        return "primary"
    if "Você realiza linguisticamente" in system:
        return "realization"
    if "Você revisa semanticamente" in system:
        return "review"
    raise AssertionError(f"estágio não reconhecido: {system[:60]!r}")


def _valid_output(name: str, stage: str, request: CompletionRequest) -> str:
    body = request.messages[0].content
    if stage == "participant":
        return f"{name}: Paris é a capital da França."
    if stage == "critique":
        return "Concordo com as claims anteriores."
    if stage == "extraction_r1":
        return json.dumps(
            {"claims": [{"text": f"Paris é a capital da França ({name})", "revises_claim_id": None}]}
        )
    if stage == "extraction_r2":
        return json.dumps({"claims": []})
    if stage == "source":
        claims = _json_after(body, "CLAIMS_ATUAIS")
        return json.dumps(
            {"claim_relations": [{"claim_id": c["claim_id"], "relation": "unresolved"} for c in claims]}
        )
    if stage == "judge":
        claims = _json_after(body, "CLAIMS ATUAIS A AVALIAR")
        return json.dumps(
            {
                "claim_assessments": [
                    {"claim_id": c["id"], "verdict": "supported", "explanation": "ok"}
                    for c in claims
                ],
                "best_arguments_by": {},
                "debate_limitations": [],
                "confidence": 0.8,
                "reasoning": "r",
            }
        )
    if stage == "editor":
        return json.dumps({"opening_style": "direct", "closing_style": "concise"})
    if stage == "primary":
        ids = [a["id"] for a in _json_after(body, "AFIRMACOES_AVALIADAS")]
        return json.dumps({"central_conclusion": ids[:1], "supporting_reasons": ids[1:3]})
    if stage == "realization":
        primary = _json_after(body, "PRIMARY_ANSWER_AUTORITATIVA")
        blocks = [
            {"claim_ids": [item["claim_id"]], "text": item["claim_text"]}
            for section in primary["sections"]
            for item in section["items"]
        ]
        return json.dumps({"blocks": blocks})
    if stage == "review":
        digest = _json_after(body, "CANDIDATE_DIGEST")
        return json.dumps({"candidate_digest": digest, "decision": "accept", "issue_codes": []})
    raise AssertionError(stage)


@dataclass
class _CallLog:
    calls: list[tuple[str, str]] = field(default_factory=list)


class _RoutedProvider(LLMProvider):
    """Implementa SÓ `_call_api`: toda resposta passa pelo
    `LLMProvider.complete()` real (pricing, provenance, latência)."""

    def __init__(self, name: str, log: _CallLog, poison: dict[str, list[int] | None], payload: str,
                 reported_model: str | None = None, finish_reason: str = "stop"):
        super().__init__("fake-key", 30.0, 0, _PRICING)
        self.provider_name = name
        self._log = log
        self._poison = poison  # estágio -> índices (0-based) das chamadas envenenadas; None = todas
        self._payload = payload
        self._seen: dict[str, int] = {}
        self._reported_model = reported_model or f"{name}-reported"
        self._finish_reason = finish_reason

    @property
    def default_model(self) -> str:
        return f"{self.provider_name}-requested"

    async def _call_api(self, request):
        stage = _stage_of(request)
        self._log.calls.append((self.provider_name, stage))
        index = self._seen.get(stage, 0)
        self._seen[stage] = index + 1
        poisoned = stage in self._poison and (
            self._poison[stage] is None or index in self._poison[stage]
        )
        text = self._payload if poisoned else _valid_output(self.provider_name, stage, request)
        usage = TokenUsage(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS)
        finish = self._finish_reason if poisoned else "stop"
        return text, usage, self._reported_model, finish


def _run_config() -> RunConfig:
    return RunConfig(
        question="Qual é a capital da França?",
        enabled_providers=_PROVIDERS,
        max_cost_usd=1.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=4096,
        max_output_tokens_grouping=8192,
        max_output_tokens_judge=8192,
        quorum=QuorumPolicy(min_for_debate=2, min_to_return=1),
        round_dispatch_timeout_seconds=30.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
        source_text=_SOURCE,
    )


async def _execute(tmp_path, *, poison, payload, reported_model=None, finish_reason="stop"):
    """Roda o pipeline REAL e devolve (resultado_em_memória, registro_recarregado, log)."""
    log = _CallLog()
    providers = {
        name: _RoutedProvider(
            name,
            log,
            poison if name == "anthropic" else {},
            payload,
            reported_model=reported_model if name == "anthropic" else None,
            finish_reason=finish_reason,
        )
        for name in _PROVIDERS
    }
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
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
    finally:
        await engine.dispose()
    assert isinstance(record, CompletedRunRecord)
    return result, record.council_run_result, log


def _stage_attempts(result, stage: str):
    if stage == "extraction_r1":
        return [a for a in result.debate_result.claim_processing_attempts if a.round_number == 1]
    if stage == "extraction_r2":
        return [a for a in result.debate_result.claim_processing_attempts if a.round_number == 2]
    if stage == "source":
        return list(result.source_analysis_result.attempts)
    if stage == "judge":
        return list(result.judge_result.attempts)
    if stage == "editor":
        return list(result.editor_result.attempts)
    if stage == "primary":
        return list(result.editor_result.primary_answer_attempts)
    raise AssertionError(stage)


_CONTRACTS = {
    "extraction_r1": CLAIM_EXTRACTION_CONTRACT_VERSION,
    "extraction_r2": CLAIM_EXTRACTION_CONTRACT_VERSION,
    "source": SOURCE_ANALYSIS_CONTRACT_VERSION,
    "judge": JUDGE_CONTRACT_VERSION,
    "editor": EDITOR_CONTRACT_VERSION,
    "primary": PRIMARY_ANSWER_CONTRACT_VERSION,
}


def _recorded_call_count(result) -> int:
    """Cada chamada real de provider aparece EXATAMENTE uma vez na auditoria."""
    debate = result.debate_result
    editor = result.editor_result
    count = len(debate.initial_result.responses)
    if debate.critique_round is not None:
        count += len(debate.critique_round.round_result.responses)
    count += len(debate.claim_processing_attempts)
    if result.source_analysis_result is not None:
        count += len(result.source_analysis_result.attempts)
    count += len(result.judge_result.attempts)
    count += len(editor.attempts) + len(editor.primary_answer_attempts)
    count += len(editor.linguistic_realization_attempts)
    count += len(editor.linguistic_semantic_review_attempts)
    return count


def _assert_truthful_interpretation_failure(attempt, payload: str, stage: str, *, priced=True):
    assert attempt.transport_status == "success"
    assert attempt.parse_status == "interpretation_failed"
    assert attempt.parse_error_message == INTERPRETATION_FAILURE_MESSAGE
    assert attempt.raw_output_text == payload
    assert attempt.provider == "anthropic"
    assert attempt.requested_model == "anthropic-requested"
    assert attempt.usage == TokenUsage(input_tokens=_INPUT_TOKENS, output_tokens=_OUTPUT_TOKENS)
    assert attempt.provider_finish_reason == "stop"
    assert attempt.transport_attempts == 1
    assert attempt.request_provenance is not None
    assert attempt.request_provenance.contract_version == _CONTRACTS[stage]
    if priced:
        assert attempt.model == "anthropic-reported"
        assert attempt.cost_usd == pytest.approx(_UNIT_COST)
        assert attempt.pricing_provenance is not None
        assert attempt.pricing_provenance.source_id == "interpretation-audit-test"
    else:
        assert attempt.cost_usd is None
        assert attempt.pricing_provenance is None


def _assert_accounting_matches_real_calls(result, reloaded, log: _CallLog, *, priced=True):
    calls = len(log.calls)
    assert _recorded_call_count(result) == calls
    assert _recorded_call_count(reloaded) == calls
    for r in (result, reloaded):
        assert r.total_input_tokens == calls * _INPUT_TOKENS
        assert r.total_output_tokens == calls * _OUTPUT_TOKENS
        if priced:
            assert r.total_cost_usd == pytest.approx(calls * _UNIT_COST)
            assert r.has_unknown_accounting_components is False
        else:
            assert r.has_unknown_accounting_components is True


def _assert_reload_is_lossless(result, reloaded):
    assert reloaded.model_dump(mode="json") == result.model_dump(mode="json")


# Estágio -> (quantas chamadas daquele estágio a pipeline faz com tudo
# envenenado, verificação do fallback JÁ PREVISTO pelo contrato do estágio).
def _check_all_failed(stage: str, result):
    debate = result.debate_result
    if stage == "extraction_r1":
        assert debate.debate_skipped_reason == "all_initial_extractions_failed"
        assert result.judge_result.verdict_unavailable_reason == "claim_extraction_failed"
        assert result.editor_result.final_answer.status == "deterministic_no_verdict"
    elif stage == "extraction_r2":
        assert debate.debate_skipped_reason is None
        assert debate.claim_extraction_missing_response_count == 3
        assert result.judge_result.verdict is not None
        assert any("Cobertura de extração" in item for item in result.final_answer.limitations)
    elif stage == "source":
        assert result.source_analysis_result.skipped_reason == "source_analysis_output_invalid"
        assert result.judge_result.verdict is not None
    elif stage == "judge":
        assert result.judge_result.verdict is None
        assert result.judge_result.verdict_unavailable_reason == "judge_output_invalid"
        assert result.editor_result.final_answer.status == "deterministic_no_verdict"
    elif stage == "editor":
        assert result.editor_result.fallback_reason == "editor_output_invalid"
        assert result.editor_result.final_answer.status == "deterministic_from_verdict"
    elif stage == "primary":
        assert result.editor_result.fallback_reason is None
        assert result.editor_result.primary_answer_fallback_reason == "primary_answer_output_invalid"
        assert result.editor_result.final_answer.primary_answer is None
    else:
        raise AssertionError(stage)


_EXPECTED_POISONED_ATTEMPTS = {
    # 3 respostas bem-sucedidas por rodada x 2 tentativas estruturadas cada
    "extraction_r1": 6,
    "extraction_r2": 6,
    "source": 2,
    "judge": 2,
    "editor": 2,
    "primary": 2,
}

STAGES = tuple(_EXPECTED_POISONED_ATTEMPTS)


@pytest.mark.parametrize("payload_name", sorted(PAYLOADS))
@pytest.mark.parametrize("stage", STAGES)
async def test_every_retry_poisoned_keeps_calls_auditable_and_uses_stage_fallback(
    tmp_path, stage, payload_name
):
    payload = PAYLOADS[payload_name]

    result, reloaded, log = await _execute(tmp_path, poison={stage: None}, payload=payload)

    _check_all_failed(stage, result)
    attempts = _stage_attempts(result, stage)
    assert len(attempts) == _EXPECTED_POISONED_ATTEMPTS[stage]
    assert [a.attempt_number for a in attempts[:2]] == [1, 2]
    for attempt in attempts:
        _assert_truthful_interpretation_failure(attempt, payload, stage)
    assert sum(1 for _p, s in log.calls if s == stage) == len(attempts)
    _assert_accounting_matches_real_calls(result, reloaded, log)
    _assert_reload_is_lossless(result, reloaded)


@pytest.mark.parametrize("payload_name", sorted(PAYLOADS))
@pytest.mark.parametrize("stage", STAGES)
async def test_poisoned_first_attempt_then_retry_succeeds(tmp_path, stage, payload_name):
    payload = PAYLOADS[payload_name]

    result, reloaded, log = await _execute(tmp_path, poison={stage: [0]}, payload=payload)

    attempts = _stage_attempts(result, stage)
    _assert_truthful_interpretation_failure(attempts[0], payload, stage)
    assert attempts[0].attempt_number == 1
    assert attempts[1].attempt_number == 2
    assert attempts[1].parse_status == "accepted"
    # o retry seguiu o contrato normal do estágio: a run terminou no caminho de sucesso
    assert result.judge_result.verdict is not None
    assert result.editor_result.fallback_reason is None
    assert result.editor_result.primary_answer_fallback_reason is None
    if stage == "source":
        assert result.source_analysis_result.skipped_reason is None
    if stage.startswith("extraction"):
        assert result.debate_result.claim_extraction_missing_response_count == 0
    _assert_accounting_matches_real_calls(result, reloaded, log)
    _assert_reload_is_lossless(result, reloaded)


async def test_unknown_cost_stays_honestly_unknown_on_interpretation_failure(tmp_path):
    """Modelo reportado sem preço registrado: custo desconhecido NUNCA vira
    zero só porque a interpretação falhou; a chamada continua registrada."""
    result, reloaded, log = await _execute(
        tmp_path, poison={"judge": None}, payload=_BIGINT, reported_model="unpriced-model"
    )

    attempts = _stage_attempts(result, "judge")
    assert len(attempts) == 2
    for attempt in attempts:
        _assert_truthful_interpretation_failure(attempt, _BIGINT, "judge", priced=False)
        assert attempt.model == "unpriced-model"
    assert result.judge_result.verdict_unavailable_reason == "judge_output_invalid"
    _assert_accounting_matches_real_calls(result, reloaded, log, priced=False)
    _assert_reload_is_lossless(result, reloaded)


@pytest.mark.parametrize("stage", ["extraction_r1", "judge"])
async def test_confirmed_truncation_still_stops_retry_after_interpretation_failure(
    tmp_path, stage
):
    """Precedência existente preservada: truncamento CONFIRMADO pelo motivo
    nativo do provider nunca é repetido (retry idêntico não corrige isso),
    também quando a interpretação falhou fora do vocabulário antecipado."""
    result, reloaded, log = await _execute(
        tmp_path, poison={stage: None}, payload=_BIGINT, finish_reason="max_tokens"
    )

    attempts = _stage_attempts(result, stage)
    per_target = 3 if stage == "extraction_r1" else 1  # sem retry: 1 tentativa por alvo
    assert len(attempts) == per_target
    assert all(a.attempt_number == 1 for a in attempts)
    assert all(a.parse_status == "interpretation_failed" for a in attempts)
    assert all(a.provider_finish_reason == "max_tokens" for a in attempts)
    if stage == "judge":
        assert result.judge_result.verdict_unavailable_reason == "judge_output_truncated"
    _assert_accounting_matches_real_calls(result, reloaded, log)
    _assert_reload_is_lossless(result, reloaded)

async def test_ordinary_malformed_output_keeps_its_existing_status(tmp_path):
    """Regressão negativa: saída malformada COMUM continua `malformed` --
    o repair nunca reclassifica o vocabulário já antecipado."""
    result, _reloaded, _log = await _execute(tmp_path, poison={"judge": None}, payload="nope")

    attempts = _stage_attempts(result, "judge")
    assert [a.parse_status for a in attempts] == ["malformed", "malformed"]
    assert result.judge_result.verdict_unavailable_reason == "judge_output_invalid"


async def test_application_bug_outside_interpretation_boundary_still_propagates(
    tmp_path, monkeypatch
):
    """A fronteira protegida é SÓ response -> interpretação. Uma exceção da
    aplicação ANTES da chamada (aqui: construção do request do Judge)
    continua um erro interno inesperado -- nunca convertida em "saída
    inválida do modelo"."""
    import app.judge.single_judge as single_judge

    def _broken(*args, **kwargs):
        raise RuntimeError("bug da aplicação")

    monkeypatch.setattr(single_judge, "build_judge_request", _broken)

    with pytest.raises(RuntimeError, match="bug da aplicação"):
        await _execute(tmp_path, poison={}, payload="")


async def test_exception_before_provider_response_is_a_transport_failure_not_interpretation(
    tmp_path, monkeypatch
):
    """Antes de qualquer resposta existir (o próprio `_call_api` falha), não
    há saída pra interpretar: continua sendo erro de TRANSPORTE registrado
    (`parse_status="not_attempted"`), nunca `interpretation_failed`."""
    original = _RoutedProvider._call_api

    async def _failing_judge_call(self, request):
        if self.provider_name == "anthropic" and _stage_of(request) == "judge":
            self._log.calls.append((self.provider_name, "judge"))
            raise RuntimeError("conexão caiu antes da resposta")
        return await original(self, request)

    monkeypatch.setattr(_RoutedProvider, "_call_api", _failing_judge_call)

    result, reloaded, log = await _execute(tmp_path, poison={}, payload="")

    attempts = _stage_attempts(result, "judge")
    assert len(attempts) == 1
    assert attempts[0].transport_status == "error"
    assert attempts[0].parse_status == "not_attempted"
    assert attempts[0].raw_output_text is None
    assert result.judge_result.verdict_unavailable_reason == "judge_transport_failed"
    _assert_reload_is_lossless(result, reloaded)


async def test_boundary_is_the_interpretation_call_not_a_list_of_exception_types(
    tmp_path, monkeypatch
):
    """A fronteira protegida é a CHAMADA de interpretação de uma resposta já
    retornada -- qualquer exceção levantada dentro dela (aqui um `TypeError`
    arbitrário) preserva a chamada como `interpretation_failed` e segue o
    fallback do estágio, em vez de apagar a chamada da auditoria. Um status
    distinto (nunca `malformed`) mantém isso distinguível de saída
    malformada antecipada."""
    import app.judge.single_judge as single_judge

    def _unexpected(*args, **kwargs):
        raise TypeError("falha inesperada dentro da interpretação")

    monkeypatch.setattr(single_judge, "_parse_and_validate", _unexpected)

    result, reloaded, log = await _execute(tmp_path, poison={}, payload="")

    attempts = _stage_attempts(result, "judge")
    assert [a.parse_status for a in attempts] == ["interpretation_failed"] * 2
    assert all(a.parse_error_message == INTERPRETATION_FAILURE_MESSAGE for a in attempts)
    assert result.judge_result.verdict_unavailable_reason == "judge_output_invalid"
    _assert_accounting_matches_real_calls(result, reloaded, log)


def _attempt_fields(cls, **overrides) -> dict:
    fields = dict(
        attempt_number=1,
        provider="anthropic",
        requested_model="m",
        model="m",
        transport_status="success",
        transport_attempts=1,
        raw_output_text=_BIGINT,
        parse_status="interpretation_failed",
        parse_error_message=INTERPRETATION_FAILURE_MESSAGE,
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=1,
    )
    if cls is ClaimProcessingAttempt:
        fields.update(operation="extraction", round_number=1, target_model_response_id="r1")
    fields.update(overrides)
    return fields


_ATTEMPT_CLASSES = (ClaimProcessingAttempt, JudgeAttempt, SourceAnalysisAttempt, EditorAttempt)


@pytest.mark.parametrize("cls", _ATTEMPT_CLASSES, ids=lambda c: c.__name__)
def test_interpretation_failed_is_a_valid_truthful_attempt_status(cls):
    attempt = cls(**_attempt_fields(cls))
    assert attempt.parse_status == "interpretation_failed"


@pytest.mark.parametrize("cls", _ATTEMPT_CLASSES, ids=lambda c: c.__name__)
def test_interpretation_failed_requires_error_message(cls):
    with pytest.raises(ValidationError, match="parse_error_message"):
        cls(**_attempt_fields(cls, parse_error_message=None))


@pytest.mark.parametrize("cls", _ATTEMPT_CLASSES, ids=lambda c: c.__name__)
def test_interpretation_failed_never_coexists_with_transport_error(cls):
    with pytest.raises(ValidationError, match="not_attempted"):
        cls(
            **_attempt_fields(
                cls,
                transport_status="error",
                transport_error=ProviderErrorInfo(
                    type=ProviderErrorType.TIMEOUT, message="x", retryable=True
                ),
                raw_output_text=None,
            )
        )
