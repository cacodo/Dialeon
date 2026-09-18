from __future__ import annotations

import pytest

from app.debate.claim_extraction_coverage import (
    CRITIQUE_ROUND_NUMBER,
    INITIAL_ROUND_NUMBER,
    ClaimExtractionTargetStatus,
    compute_claim_extraction_targets,
    summarize_claim_extraction_coverage,
)
from app.debate.processing_record import ClaimProcessingAttempt
from app.models.domain import ModelResponse
from app.models.provider_models import TokenUsage


def _response(status: str = "success", **overrides) -> ModelResponse:
    fields = dict(
        provider="openai",
        model="gpt-test",
        requested_model="gpt-test",
        round_number=1,
        status=status,
        response_text="resposta de teste" if status == "success" else None,
        usage=TokenUsage(input_tokens=10, output_tokens=5) if status == "success" else None,
        latency_ms=10,
        attempts=1,
    )
    fields.update(overrides)
    return ModelResponse(**fields)


def _extraction_attempt(
    round_number: int,
    target_model_response_id: str,
    parse_status: str,
    attempt_number: int = 1,
) -> ClaimProcessingAttempt:
    common = dict(
        operation="extraction",
        round_number=round_number,
        attempt_number=attempt_number,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        target_model_response_id=target_model_response_id,
        transport_status="success",
        transport_attempts=1,
        latency_ms=10,
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        cost_usd=0.0,
    )
    if parse_status == "accepted":
        return ClaimProcessingAttempt(
            raw_output_text='{"claims": []}', parse_status="accepted", **common
        )
    return ClaimProcessingAttempt(
        raw_output_text="isto não é JSON",
        parse_status=parse_status,
        parse_error_message="malformado",
        **common,
    )


# ---------------------------------------------------------------------------
# Status por alvo -- accepted / failed / not_attempted
# ---------------------------------------------------------------------------


def test_accepted_target_status():
    r = _response()
    attempts = [_extraction_attempt(INITIAL_ROUND_NUMBER, r.id, "accepted")]
    targets = compute_claim_extraction_targets([(INITIAL_ROUND_NUMBER, [r])], attempts)
    assert len(targets) == 1
    assert targets[0].status == ClaimExtractionTargetStatus.ACCEPTED


def test_failed_target_status_from_single_rejected_attempt():
    r = _response()
    attempts = [_extraction_attempt(INITIAL_ROUND_NUMBER, r.id, "malformed")]
    targets = compute_claim_extraction_targets([(INITIAL_ROUND_NUMBER, [r])], attempts)
    assert targets[0].status == ClaimExtractionTargetStatus.FAILED


def test_not_attempted_target_status_with_zero_attempts():
    r = _response()
    targets = compute_claim_extraction_targets([(INITIAL_ROUND_NUMBER, [r])], [])
    assert targets[0].status == ClaimExtractionTargetStatus.NOT_ATTEMPTED


def test_error_response_is_never_an_eligible_target():
    """Uma ModelResponse que já falhou no dispatch (status='error') nunca
    foi candidata a extração -- não aparece como alvo elegível nenhum,
    mesmo que zero tentativas existam pra ela (o que seria indistinguível
    de not_attempted se ela contasse)."""
    from app.models.provider_models import ProviderErrorInfo, ProviderErrorType

    r = _response(
        status="error",
        response_text=None,
        usage=None,
        error=ProviderErrorInfo(type=ProviderErrorType.API_ERROR, message="falhou", retryable=True),
    )
    targets = compute_claim_extraction_targets([(INITIAL_ROUND_NUMBER, [r])], [])
    assert targets == []


# ---------------------------------------------------------------------------
# Retry-safe -- malformado então aceito / duas falhas
# ---------------------------------------------------------------------------


def test_malformed_then_accepted_retry_covers_target_exactly_once():
    r = _response()
    attempts = [
        _extraction_attempt(INITIAL_ROUND_NUMBER, r.id, "malformed", attempt_number=1),
        _extraction_attempt(INITIAL_ROUND_NUMBER, r.id, "accepted", attempt_number=2),
    ]
    targets = compute_claim_extraction_targets([(INITIAL_ROUND_NUMBER, [r])], attempts)
    assert len(targets) == 1
    assert targets[0].status == ClaimExtractionTargetStatus.ACCEPTED
    coverage = summarize_claim_extraction_coverage(targets)
    assert coverage.accepted_count == 1
    assert coverage.failed_count == 0
    assert coverage.missing_count == 0
    assert coverage.is_complete is True


def test_two_failed_attempts_leave_target_missing_exactly_once():
    r = _response()
    attempts = [
        _extraction_attempt(INITIAL_ROUND_NUMBER, r.id, "malformed", attempt_number=1),
        _extraction_attempt(
            INITIAL_ROUND_NUMBER, r.id, "inconsistent_references", attempt_number=2
        ),
    ]
    targets = compute_claim_extraction_targets([(INITIAL_ROUND_NUMBER, [r])], attempts)
    assert len(targets) == 1
    assert targets[0].status == ClaimExtractionTargetStatus.FAILED
    coverage = summarize_claim_extraction_coverage(targets)
    assert coverage.accepted_count == 0
    assert coverage.failed_count == 1
    assert coverage.missing_count == 1
    assert coverage.is_complete is False


# ---------------------------------------------------------------------------
# Round-safe -- rodada 1 e crítica nunca se cobrem
# ---------------------------------------------------------------------------


def test_round_one_and_critique_targets_never_cover_each_other_even_with_colliding_ids():
    """Prova estrutural: mesmo com o MESMO response_id reaparecendo nas
    duas rodadas (construção deliberada, nunca acontece organicamente já
    que ModelResponse.id é sempre um UUID novo -- mas a derivação nunca
    deveria depender disso implicitamente), uma extração aceita na
    rodada 1 NUNCA conta como cobertura pro alvo homônimo da rodada 2, e
    vice-versa -- a chave é (round_number, response_id), nunca
    response_id sozinho."""
    shared_id = "resp-mesmo-id-nas-duas-rodadas"
    r1 = _response(id=shared_id, round_number=INITIAL_ROUND_NUMBER)
    r2 = _response(id=shared_id, round_number=CRITIQUE_ROUND_NUMBER)

    # Só a rodada 1 tem uma extração aceita pro id compartilhado.
    attempts = [_extraction_attempt(INITIAL_ROUND_NUMBER, shared_id, "accepted")]

    targets = compute_claim_extraction_targets(
        [(INITIAL_ROUND_NUMBER, [r1]), (CRITIQUE_ROUND_NUMBER, [r2])], attempts
    )
    assert len(targets) == 2
    by_round = {t.round_number: t.status for t in targets}
    assert by_round[INITIAL_ROUND_NUMBER] == ClaimExtractionTargetStatus.ACCEPTED
    # O alvo da rodada 2 com o MESMO response_id NUNCA é coberto pela
    # tentativa aceita da rodada 1.
    assert by_round[CRITIQUE_ROUND_NUMBER] == ClaimExtractionTargetStatus.NOT_ATTEMPTED

    coverage = summarize_claim_extraction_coverage(targets)
    assert coverage.eligible_count == 2
    assert coverage.accepted_count == 1
    assert coverage.not_attempted_count == 1
    assert coverage.is_complete is False


def test_round_scoped_query_only_returns_targets_from_requested_rounds():
    """Um chamador que só passa a rodada 1 (ex.: DebateEngine, no
    short-circuit de falha total ANTES da crítica existir) nunca vê
    alvos da rodada 2 -- mesmo que existam respostas de round_number=2
    em algum lugar do sistema, elas simplesmente não são passadas."""
    r1a = _response(round_number=INITIAL_ROUND_NUMBER)
    r1b = _response(round_number=INITIAL_ROUND_NUMBER)
    attempts = [
        _extraction_attempt(INITIAL_ROUND_NUMBER, r1a.id, "accepted"),
        _extraction_attempt(INITIAL_ROUND_NUMBER, r1b.id, "malformed"),
    ]
    targets = compute_claim_extraction_targets(
        [(INITIAL_ROUND_NUMBER, [r1a, r1b])], attempts
    )
    assert len(targets) == 2
    assert all(t.round_number == INITIAL_ROUND_NUMBER for t in targets)


# ---------------------------------------------------------------------------
# Agrupamento/reconciliação nunca contaminam a contagem de extração
# ---------------------------------------------------------------------------


def test_grouping_and_reconciliation_attempts_are_ignored():
    """`operation != 'extraction'` nunca participa da derivação --
    agrupamento/reconciliação não têm `target_model_response_id` (ver
    ClaimProcessingAttempt._targets_match_operation), então nunca
    colidiriam de qualquer forma, mas o filtro é explícito."""
    r = _response()
    grouping_attempt = ClaimProcessingAttempt(
        operation="grouping",
        round_number=INITIAL_ROUND_NUMBER,
        attempt_number=1,
        provider="anthropic",
        requested_model="claude-sonnet-5",
        model="claude-sonnet-5",
        target_claim_ids=["c1", "c2"],
        transport_status="success",
        transport_attempts=1,
        raw_output_text='{"groups": [], "ungrouped_claim_ids": ["c1", "c2"]}',
        parse_status="accepted",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        cost_usd=0.0,
        latency_ms=10,
    )
    targets = compute_claim_extraction_targets(
        [(INITIAL_ROUND_NUMBER, [r])], [grouping_attempt]
    )
    assert targets[0].status == ClaimExtractionTargetStatus.NOT_ATTEMPTED


@pytest.mark.parametrize("eligible", [0])
def test_empty_targets_summarize_to_complete_coverage(eligible):
    """Nenhum alvo elegível (ex.: rodada de crítica que não ocorreu, ou
    lista vazia) -- vacuamente "completo", nunca "incompleto" por
    ausência de alvos."""
    coverage = summarize_claim_extraction_coverage([])
    assert coverage.eligible_count == eligible
    assert coverage.is_complete is True
