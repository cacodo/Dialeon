"""
Linguistic Realization -- integração com `Editor.compose()`: geração,
validação estrutural determinística, revisão semântica independente,
retries limitados, budget em cada estágio, falha nunca invalida o Run,
accounting (chamadas aceitas E rejeitadas das duas famílias) e identidade
truthful de provider (mesmo provider vs. dois providers). Nenhuma chamada
de provider real.
"""

from __future__ import annotations

import json

import pytest

from app.debate.claims import get_current_claims
from app.editor import compose as compose_module
from app.editor.compose import Editor
from app.editor.limitations import canonical_limitations
from app.editor.linguistic_realization import (
    LinguisticRealizationProposal,
    candidate_digest,
    primary_answer_digest,
)
from app.editor.primary_answer import PrimaryAnswerPlan, render_primary_answer, validate_plan
from app.orchestrator.config import QuorumPolicy, RunConfig
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response
from tests.editor.fixtures import (
    RealizationAwareScriptedProvider,
    debate_result,
    judge_result,
    model_response,
    raw_claim,
    verdict,
)
from app.models.domain import ClaimAssessment


def _run_config(**overrides) -> RunConfig:
    fields = dict(
        question="Qual abordagem a organização deve escolher?",
        enabled_providers=["openai"],
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


class World:
    """Debate com uma única claim `supported` -- o mínimo pra ter uma
    PrimaryAnswer selecionável e, portanto, elegível pra Linguistic
    Realization."""

    def __init__(self) -> None:
        self.claim = raw_claim("SaaS reduz a manutenção.", "r-1", id="c-sup")
        self.claims = [self.claim]
        self.current = get_current_claims(self.claims)
        assessments = [
            ClaimAssessment(claim_id="c-sup", verdict="supported", explanation="EXPLICACAO"),
        ]
        self.verdict = verdict(assessments, debate_limitations=["Sem dados empíricos."])
        self.dr = debate_result(self.claims, [model_response("openai")])
        self.jr = judge_result(self.verdict)

    def prior(self):
        return dict(
            prior_input_tokens=self.dr.cumulative_input_tokens + self.jr.judge_input_tokens,
            prior_output_tokens=self.dr.cumulative_output_tokens + self.jr.judge_output_tokens,
            prior_cost_usd=self.dr.cumulative_cost_usd + self.jr.judge_cost_usd,
        )

    def expected_primary(self, plan_payload: dict | None = None):
        if plan_payload is None:
            plan_payload = {"central_conclusion": ["c-sup"]}
        plan = PrimaryAnswerPlan.model_validate(plan_payload)
        selection = validate_plan(
            plan, verdict=self.verdict, current_claims=self.current, all_claims=self.claims
        )
        limitations = tuple(canonical_limitations(self.dr, self.verdict))
        return render_primary_answer(
            selection, based_on_verdict_id=self.verdict.id, limitations=limitations
        )


def _style_payload() -> str:
    return json.dumps({"opening_style": "direct", "closing_style": "concise"})


def _primary_payload() -> str:
    return json.dumps({"central_conclusion": ["c-sup"]})


def _realization_payload(text: str = "SaaS reduz a manutenção, de forma reescrita.") -> str:
    return json.dumps({"blocks": [{"claim_ids": ["c-sup"], "text": text}]})


def _review_payload(digest: str, decision: str = "accept", issue_codes: list | None = None) -> str:
    return json.dumps(
        {"candidate_digest": digest, "decision": decision, "issue_codes": issue_codes or []}
    )


def _expected_digest(world: World, *, text: str = "SaaS reduz a manutenção, de forma reescrita.") -> str:
    primary = world.expected_primary()
    proposal = LinguisticRealizationProposal.model_validate(
        json.loads(_realization_payload(text))
    )
    return candidate_digest(
        proposal, based_on_primary_answer_digest=primary_answer_digest(primary)
    )


async def _compose(world: World, providers: dict, *, run_config: RunConfig | None = None, **kwargs):
    editor = Editor(providers)
    return await editor.compose(
        world.dr, world.jr, run_config or _run_config(), **world.prior(), **kwargs
    )


def _provider(**kwargs) -> RealizationAwareScriptedProvider:
    return RealizationAwareScriptedProvider(
        "anthropic",
        [text_response("anthropic", _style_payload())],
        primary_responses=kwargs.pop("primary_responses", [text_response("anthropic", _primary_payload())]),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Sucesso ponta a ponta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_end_to_end_produces_an_accepted_linguistic_realization():
    world = World()
    digest = _expected_digest(world)
    provider = _provider(
        realization_responses=[
            text_response(
                "anthropic", _realization_payload(), input_tokens=50, output_tokens=20, cost_usd=0.01
            )
        ],
        review_responses=[
            text_response(
                "anthropic", _review_payload(digest), input_tokens=60, output_tokens=10, cost_usd=0.02
            )
        ],
    )

    result = await _compose(world, {"anthropic": provider})

    realization = result.final_answer.linguistic_realization
    assert realization is not None
    assert realization.based_on_primary_answer_digest == primary_answer_digest(
        world.expected_primary()
    )
    assert result.linguistic_realization_fallback_reason is None
    assert result.linguistic_semantic_review_provider == "anthropic"
    assert [a.parse_status for a in result.linguistic_realization_attempts] == ["accepted"]
    assert [a.parse_status for a in result.linguistic_semantic_review_attempts] == ["accepted"]
    # camadas anteriores continuam intactas
    assert result.final_answer.primary_answer is not None
    assert result.final_answer.natural_answer is not None
    assert result.final_answer.status == "llm_planned"
    assert result.fallback_reason is None


# ---------------------------------------------------------------------------
# Retries limitados / falhas estruturais e de formato
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structural_rejection_then_retry_succeeds_with_bounded_feedback():
    world = World()
    digest = _expected_digest(world)
    bad = json.dumps({"blocks": [{"claim_ids": ["c-inexistente"], "text": "x"}]})
    provider = _provider(
        realization_responses=[
            text_response("anthropic", bad),
            text_response("anthropic", _realization_payload()),
        ],
        review_responses=[text_response("anthropic", _review_payload(digest))],
    )

    result = await _compose(world, {"anthropic": provider})

    assert [a.parse_status for a in result.linguistic_realization_attempts] == [
        "inconsistent_references",
        "accepted",
    ]
    assert result.final_answer.linguistic_realization is not None
    first, second = provider.realization_requests
    assert first.messages[0].content != second.messages[0].content
    assert "REJEICAO_ESTRUTURAL_ANTERIOR" in second.messages[0].content


@pytest.mark.asyncio
async def test_malformed_json_then_retry_succeeds():
    world = World()
    digest = _expected_digest(world)
    provider = _provider(
        realization_responses=[
            text_response("anthropic", "isto não é json"),
            text_response("anthropic", _realization_payload()),
        ],
        review_responses=[text_response("anthropic", _review_payload(digest))],
    )

    result = await _compose(world, {"anthropic": provider})

    assert [a.parse_status for a in result.linguistic_realization_attempts] == [
        "malformed",
        "accepted",
    ]
    assert result.final_answer.linguistic_realization is not None


@pytest.mark.asyncio
async def test_two_structural_failures_fall_back_and_never_call_semantic_review():
    world = World()
    bad = json.dumps({"blocks": [{"claim_ids": ["c-inexistente"], "text": "x"}]})
    provider = _provider(realization_responses=[text_response("anthropic", bad)] * 2)

    result = await _compose(world, {"anthropic": provider})

    assert provider.review_requests == []
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "structural_rejection"
    assert result.final_answer.primary_answer is not None  # nunca invalida o run
    assert result.final_answer.status == "llm_planned"


@pytest.mark.asyncio
async def test_transport_failure_during_realization_never_retries_this_layer():
    world = World()
    provider = _provider(realization_responses=[transport_error_response("anthropic")])

    result = await _compose(world, {"anthropic": provider})

    assert len(provider.realization_requests) == 1
    assert provider.review_requests == []
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "realization_transport_failure"
    (attempt,) = result.linguistic_realization_attempts
    assert attempt.transport_status == "error" and attempt.usage is None
    assert result.has_unknown_accounting_components is True


# ---------------------------------------------------------------------------
# Revisão semântica -- aceite/rejeição/malformado/transporte
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_review_rejection_is_never_retried_and_never_presented():
    world = World()
    digest = _expected_digest(world)
    provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
        review_responses=[
            text_response(
                "anthropic", _review_payload(digest, "reject", ["semantic_omission"])
            )
        ],
    )

    result = await _compose(world, {"anthropic": provider})

    assert len(provider.review_requests) == 1  # sem retry de rejeição válida
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "semantic_review_rejection"
    (review_attempt,) = result.linguistic_semantic_review_attempts
    assert review_attempt.parse_status == "accepted"  # estruturalmente válido; só reprovado
    # a tentativa REJEITADA nunca vira a apresentação preferida, mas fica auditável
    assert result.final_answer.primary_answer is not None


@pytest.mark.asyncio
async def test_malformed_semantic_review_gets_one_retry_then_succeeds():
    world = World()
    digest = _expected_digest(world)
    provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
        review_responses=[
            text_response("anthropic", "não é json"),
            text_response("anthropic", _review_payload(digest)),
        ],
    )

    result = await _compose(world, {"anthropic": provider})

    assert [a.parse_status for a in result.linguistic_semantic_review_attempts] == [
        "malformed",
        "accepted",
    ]
    assert result.final_answer.linguistic_realization is not None


@pytest.mark.asyncio
async def test_semantic_review_with_a_digest_mismatch_is_treated_as_malformed():
    world = World()
    digest = _expected_digest(world)
    wrong_digest = "f" * 64
    provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
        review_responses=[
            text_response("anthropic", _review_payload(wrong_digest)),
            text_response("anthropic", _review_payload(digest)),
        ],
    )

    result = await _compose(world, {"anthropic": provider})

    assert [a.parse_status for a in result.linguistic_semantic_review_attempts] == [
        "malformed",
        "accepted",
    ]
    assert result.final_answer.linguistic_realization is not None


@pytest.mark.asyncio
async def test_two_malformed_semantic_reviews_fall_back_without_a_third_attempt():
    world = World()
    provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
        review_responses=[text_response("anthropic", "não é json")] * 2,
    )

    result = await _compose(world, {"anthropic": provider})

    assert len(provider.review_requests) == 2
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "malformed_semantic_review"
    assert result.final_answer.status == "llm_planned"


@pytest.mark.asyncio
async def test_transport_failure_during_semantic_review():
    world = World()
    provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
        review_responses=[transport_error_response("anthropic")],
    )

    result = await _compose(world, {"anthropic": provider})

    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "semantic_review_transport_failure"
    assert result.has_unknown_accounting_components is True


# ---------------------------------------------------------------------------
# Budget em cada estágio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_exhausted_before_realization_skips_the_call_entirely():
    world = World()
    provider = _provider()
    prior = world.prior()
    total_prior = prior["prior_input_tokens"] + prior["prior_output_tokens"]
    # Teto de tokens que fecha EXATAMENTE depois de estilo (10+5) + primary
    # (10+5) -- ambos defaults de `text_response` -- sem nunca chegar na
    # realização.
    run_config = _run_config(max_cost_usd=100.0, max_total_tokens=total_prior + 30)

    result = await _compose(world, {"anthropic": provider}, run_config=run_config)

    assert provider.realization_requests == []
    assert provider.review_requests == []
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "budget_exhausted_before_realization"
    assert result.cumulative_budget_exceeded is True
    assert result.final_answer.primary_answer is not None  # camada anterior intacta


@pytest.mark.asyncio
async def test_budget_exhausted_before_semantic_review_skips_only_the_review_call():
    world = World()
    digest = _expected_digest(world)
    prior = world.prior()
    total_prior = prior["prior_input_tokens"] + prior["prior_output_tokens"]
    # `_provider()` default: style + primary consomem exatamente 10+5 (estilo)
    # + 10+5 (primary) = 30 tokens além do prior (defaults de
    # `text_response`, ver tests/debate/fakes.py). A realização (roteirizada
    # abaixo) consome mais 5+5=10 tokens -- um teto entre prior+30
    # (exclusive) e prior+40 (exclusive) abre a realização mas fecha a
    # revisão semântica logo depois.
    provider = _provider(
        realization_responses=[
            text_response(
                "anthropic", _realization_payload(), input_tokens=5, output_tokens=5, cost_usd=0.0
            )
        ],
        review_responses=[text_response("anthropic", _review_payload(digest))],
    )
    run_config = _run_config(max_cost_usd=100.0, max_total_tokens=total_prior + 35)

    result = await _compose(world, {"anthropic": provider}, run_config=run_config)

    assert provider.realization_requests != []
    assert provider.review_requests == []
    assert result.final_answer.linguistic_realization is None
    assert (
        result.linguistic_realization_fallback_reason
        == "budget_exhausted_before_semantic_review"
    )
    assert [a.parse_status for a in result.linguistic_realization_attempts] == ["accepted"]


# ---------------------------------------------------------------------------
# Provider truthful identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_editor_and_judge_provider_is_recorded_truthfully():
    world = World()
    digest = _expected_digest(world)
    provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
        review_responses=[text_response("anthropic", _review_payload(digest))],
    )
    run_config = _run_config(editor_provider="anthropic", judge_provider="anthropic")

    result = await _compose(world, {"anthropic": provider}, run_config=run_config)

    assert result.editor_provider == result.linguistic_semantic_review_provider == "anthropic"
    (realization_attempt,) = result.linguistic_realization_attempts
    (review_attempt,) = result.linguistic_semantic_review_attempts
    assert realization_attempt.provider == review_attempt.provider == "anthropic"


@pytest.mark.asyncio
async def test_a_separate_review_provider_is_recorded_as_its_own_provider():
    world = World()
    digest = _expected_digest(world)
    editor_provider = _provider(
        realization_responses=[text_response("anthropic", _realization_payload())],
    )
    review_provider = ScriptedProvider("openai", [text_response("openai", _review_payload(digest))])
    run_config = _run_config(editor_provider="anthropic", judge_provider="openai")

    result = await _compose(
        world, {"anthropic": editor_provider, "openai": review_provider}, run_config=run_config
    )

    assert result.editor_provider == "anthropic"
    assert result.linguistic_semantic_review_provider == "openai"
    assert result.final_answer.linguistic_realization is not None
    (review_attempt,) = result.linguistic_semantic_review_attempts
    assert review_attempt.provider == "openai"


@pytest.mark.asyncio
async def test_review_provider_missing_from_the_configured_providers_falls_back_defensively():
    world = World()
    provider = _provider(realization_responses=[text_response("anthropic", _realization_payload())])
    run_config = _run_config(editor_provider="anthropic", judge_provider="openai")

    # "openai" nunca é registrado no dicionário de providers do Editor.
    result = await _compose(world, {"anthropic": provider}, run_config=run_config)

    assert provider.review_requests == []
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason == "defensive_realization_persistence_failure"
    assert result.linguistic_semantic_review_provider is None
    assert [a.parse_status for a in result.linguistic_realization_attempts] == ["accepted"]


# ---------------------------------------------------------------------------
# Nunca invalida o Run / não se aplica sem PrimaryAnswer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unexpected_exception_never_invalidates_an_otherwise_successful_run(monkeypatch):
    world = World()
    provider = _provider()

    async def boom(self, **kwargs):
        raise RuntimeError("falha inesperada simulada")

    monkeypatch.setattr(Editor, "_realize_primary_answer", boom)

    result = await _compose(world, {"anthropic": provider})

    assert result.final_answer.status == "llm_planned"
    assert result.fallback_reason is None
    assert result.final_answer.linguistic_realization is None
    assert (
        result.linguistic_realization_fallback_reason
        == "defensive_realization_persistence_failure"
    )
    assert result.final_answer.primary_answer is not None
    assert result.final_answer.answer_text  # avaliação completa preservada


@pytest.mark.asyncio
async def test_no_realization_is_attempted_when_there_is_no_selectable_primary_answer():
    only_open = verdict(
        [ClaimAssessment(claim_id="c-unr", verdict="unresolved", explanation="e")]
    )
    dr = debate_result(
        [raw_claim("O fornecedor pode falir.", "r-1", id="c-unr")], [model_response("openai")]
    )
    jr = judge_result(only_open)
    provider = _provider()

    editor = Editor({"anthropic": provider})
    result = await editor.compose(
        dr,
        jr,
        _run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )

    assert provider.realization_requests == []
    assert provider.review_requests == []
    assert result.final_answer.primary_answer is None
    assert result.final_answer.linguistic_realization is None
    assert result.linguistic_realization_fallback_reason is None
    assert result.linguistic_realization_attempts == []


# ---------------------------------------------------------------------------
# Accounting -- inclui as duas famílias de chamada, aceitas E rejeitadas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accounting_includes_both_call_families_and_every_attempt_rejected_or_accepted():
    world = World()
    digest = _expected_digest(world)
    bad_structural = json.dumps({"blocks": [{"claim_ids": ["c-inexistente"], "text": "x"}]})
    provider = RealizationAwareScriptedProvider(
        "anthropic",
        [text_response("anthropic", _style_payload(), input_tokens=1, output_tokens=1, cost_usd=0.0001)],
        primary_responses=[
            text_response(
                "anthropic", _primary_payload(), input_tokens=2, output_tokens=2, cost_usd=0.0002
            )
        ],
        realization_responses=[
            text_response(
                "anthropic", bad_structural, input_tokens=11, output_tokens=3, cost_usd=0.001
            ),
            text_response(
                "anthropic", _realization_payload(), input_tokens=13, output_tokens=5, cost_usd=0.002
            ),
        ],
        review_responses=[
            text_response(
                "anthropic", "não é json", input_tokens=17, output_tokens=7, cost_usd=0.003
            ),
            text_response(
                "anthropic", _review_payload(digest), input_tokens=19, output_tokens=9, cost_usd=0.004
            ),
        ],
    )

    result = await _compose(world, {"anthropic": provider})

    assert result.final_answer.linguistic_realization is not None
    realization_input = 11 + 13
    realization_output = 3 + 5
    realization_cost = 0.001 + 0.002
    review_input = 17 + 19
    review_output = 7 + 9
    review_cost = 0.003 + 0.004
    style_input = result.attempts[0].usage.input_tokens
    style_output = result.attempts[0].usage.output_tokens
    style_cost = result.attempts[0].cost_usd or 0.0
    primary_input = result.primary_answer_attempts[0].usage.input_tokens
    primary_output = result.primary_answer_attempts[0].usage.output_tokens
    primary_cost = result.primary_answer_attempts[0].cost_usd or 0.0

    assert result.editor_input_tokens == (
        style_input + primary_input + realization_input + review_input
    )
    assert result.editor_output_tokens == (
        style_output + primary_output + realization_output + review_output
    )
    assert result.editor_cost_usd == pytest.approx(
        style_cost + primary_cost + realization_cost + review_cost
    )
    assert result.has_unknown_accounting_components is False


@pytest.mark.asyncio
async def test_unknown_cost_never_becomes_zero_or_free():
    world = World()
    provider = _provider(
        realization_responses=[
            text_response(
                "anthropic", _realization_payload(), input_tokens=5, output_tokens=2, cost_usd=None
            )
        ],
    )

    result = await _compose(world, {"anthropic": provider})

    assert result.has_unknown_accounting_components is True
