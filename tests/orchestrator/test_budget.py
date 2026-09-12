from __future__ import annotations

import inspect

import pytest

from app.models.provider_models import TokenUsage
from app.orchestrator.budget import compute_budget_exceeded, sum_usage_and_cost
from app.orchestrator.config import QuorumPolicy, RunConfig


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="pergunta de teste",
        enabled_providers=["openai"],
        max_cost_usd=1.00,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        overall_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)


# ---------------------------------------------------------------------------
# Os 6 casos exatos de budget com unknown
# ---------------------------------------------------------------------------


def test_known_0_99_below_limit_1_00_is_open():
    exceeded = compute_budget_exceeded(0, 0, 0.99, _run_config(max_cost_usd=1.00))
    assert exceeded is False


def test_known_1_00_equals_limit_is_closed():
    exceeded = compute_budget_exceeded(0, 0, 1.00, _run_config(max_cost_usd=1.00))
    assert exceeded is True


def test_known_1_50_with_unknown_still_closed_known_alone_already_exceeds():
    """O caso central da correção: unknown NUNCA apaga um subtotal
    conhecido que já excede o limite sozinho."""
    exceeded = compute_budget_exceeded(0, 0, 1.50, _run_config(max_cost_usd=1.00))
    assert exceeded is True


def test_known_0_50_with_unknown_is_open_but_not_a_guarantee():
    """known abaixo do limite -> gate aberto — mas isso NUNCA significa
    'garantidamente dentro do orçamento real', só 'nenhum subtotal
    conhecido excedeu'. A incerteza fica em
    has_unknown_accounting_components, fora desta função."""
    exceeded = compute_budget_exceeded(0, 0, 0.50, _run_config(max_cost_usd=1.00))
    assert exceeded is False


def test_known_zero_with_unknown_is_open():
    exceeded = compute_budget_exceeded(0, 0, 0.0, _run_config(max_cost_usd=1.00))
    assert exceeded is False


def test_known_zero_without_unknown_is_open_normally():
    exceeded = compute_budget_exceeded(0, 0, 0.0, _run_config(max_cost_usd=1.00))
    assert exceeded is False


# ---------------------------------------------------------------------------
# has_unknown_accounting_components NUNCA participa da decisão booleana
# ---------------------------------------------------------------------------


def test_compute_budget_exceeded_has_no_unknown_parameter():
    params = list(inspect.signature(compute_budget_exceeded).parameters.keys())
    assert "has_unknown_accounting_components" not in params
    assert "has_unknown_accounting_components" not in params
    assert params == [
        "total_input_tokens",
        "total_output_tokens",
        "known_total_cost_usd",
        "run_config",
    ]


# ---------------------------------------------------------------------------
# Regressão dos 3 pontos de token budget (>=)
# ---------------------------------------------------------------------------


def test_token_budget_6999_of_7000_is_open():
    exceeded = compute_budget_exceeded(6999, 0, 0.0, _run_config(max_total_tokens=7000))
    assert exceeded is False


def test_token_budget_7000_of_7000_is_closed():
    exceeded = compute_budget_exceeded(7000, 0, 0.0, _run_config(max_total_tokens=7000))
    assert exceeded is True


def test_token_budget_7001_of_7000_is_closed():
    exceeded = compute_budget_exceeded(7001, 0, 0.0, _run_config(max_total_tokens=7000))
    assert exceeded is True


def test_token_budget_independent_of_cost_even_with_free_provider():
    """Provider conhecido-grátis (custo=0.0, não desconhecido) ainda
    estoura o budget de tokens — token budget nunca é ignorado por causa
    de custo monetário zero."""
    exceeded = compute_budget_exceeded(
        8000, 0, 0.0, _run_config(max_total_tokens=7000, max_cost_usd=100.0)
    )
    assert exceeded is True


# ---------------------------------------------------------------------------
# sum_usage_and_cost — assinatura e propagação do flag
# ---------------------------------------------------------------------------


class _FakeRecord:
    def __init__(
        self, input_tokens=None, output_tokens=None, cost_usd=None,
        had_uncertain_prior_attempts=False,
    ):
        self.usage = (
            TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
            if input_tokens is not None or output_tokens is not None
            else None
        )
        self.cost_usd = cost_usd
        self.had_uncertain_prior_attempts = had_uncertain_prior_attempts


def test_sum_usage_and_cost_returns_four_values():
    result = sum_usage_and_cost([_FakeRecord(100, 50, 0.01)])
    assert len(result) == 4


def test_sum_usage_and_cost_empty_list_is_all_zero_no_unknown():
    total_input, total_output, known_cost, has_unknown = sum_usage_and_cost([])
    assert (total_input, total_output, known_cost, has_unknown) == (0, 0, 0.0, False)


def test_sum_usage_and_cost_all_known_no_unknown_flag():
    records = [
        _FakeRecord(100, 50, 0.01),
        _FakeRecord(200, 100, 0.02),
    ]
    total_input, total_output, known_cost, has_unknown = sum_usage_and_cost(records)
    assert total_input == 300
    assert total_output == 150
    assert known_cost == pytest.approx(0.03)
    assert has_unknown is False


def test_sum_usage_and_cost_one_unknown_in_the_middle():
    records = [
        _FakeRecord(100, 50, 0.01),
        _FakeRecord(200, 100, None),  # custo desconhecido
        _FakeRecord(50, 25, 0.02),
    ]
    total_input, total_output, known_cost, has_unknown = sum_usage_and_cost(records)
    assert total_input == 350
    assert total_output == 175
    assert known_cost == pytest.approx(0.03)  # só os 2 conhecidos somados
    assert has_unknown is True


def test_sum_usage_and_cost_all_unknown_zero_known_but_flag_true():
    records = [_FakeRecord(100, 50, None), _FakeRecord(200, 100, None)]
    total_input, total_output, known_cost, has_unknown = sum_usage_and_cost(records)
    assert known_cost == 0.0
    assert has_unknown is True  # nunca confundir com "genuinamente grátis"


def test_sum_usage_and_cost_known_zero_is_not_unknown():
    records = [_FakeRecord(100, 50, 0.0)]
    _, _, known_cost, has_unknown = sum_usage_and_cost(records)
    assert known_cost == 0.0
    assert has_unknown is False
