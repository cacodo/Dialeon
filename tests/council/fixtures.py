from __future__ import annotations

from app.editor.result import EditorResult, FinalAnswer
from app.orchestrator.config import QuorumPolicy, RunConfig

from tests.editor.fixtures import judge_result, verdict  # noqa: F401
from tests.judge.fixtures import (  # noqa: F401
    canonical_claim,
    debate_result,
    model_response,
    raw_claim,
    round_result,
)


def final_answer(**overrides) -> FinalAnswer:
    fields = dict(
        answer_text="resposta final de teste",
        status="deterministic_no_verdict",
    )
    fields.update(overrides)
    return FinalAnswer(**fields)


def editor_result(**overrides) -> EditorResult:
    fields = dict(
        final_answer=final_answer(),
        attempts=[],
        fallback_reason="judge_verdict_unavailable",
        editor_provider="anthropic",
        cumulative_budget_exceeded=False,
    )
    fields.update(overrides)
    return EditorResult(**fields)


def run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Qual a capital do Brasil?",
        enabled_providers=["openai", "anthropic"],
        max_cost_usd=100.0,
        max_total_tokens=1_000_000,
        max_output_tokens_per_call=1024,
        max_output_tokens_grouping=1024,
        max_output_tokens_judge=1024,
        quorum=QuorumPolicy(min_for_debate=1, min_to_return=1),
        round_dispatch_timeout_seconds=5.0,
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    fields.update(overrides)
    return RunConfig(**fields)
