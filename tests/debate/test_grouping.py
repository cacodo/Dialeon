from __future__ import annotations

import json

import pytest

from app.debate.claim_extraction import group_claims
from tests.council.fixtures import run_config as _run_config
from app.models.domain import Claim, ClaimSupport
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response


def _raw_claim(claim_id_suffix: str, provider: str, total: int = 3) -> Claim:
    return Claim(
        text=f"claim de {provider}",
        source_model_response_id=f"resp-{claim_id_suffix}",
        round_introduced=1,
        status="active",
        supporting_model_response_ids=[
            ClaimSupport(model_response_id=f"resp-{claim_id_suffix}", provider=provider, model="m")
        ],
        total_models_in_round=total,
    )


@pytest.mark.asyncio
async def test_empty_raw_claims_short_circuits_without_calling_llm():
    provider = ScriptedProvider("anthropic", [])
    canonical, attempts = await group_claims(
        [], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert canonical == []
    assert attempts == []
    assert provider.received_requests == []


@pytest.mark.asyncio
async def test_group_of_two_or_more_creates_canonical_claim():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    c = _raw_claim("c", "gemini")
    payload = json.dumps(
        {
            "groups": [
                {"member_claim_ids": [a.id, b.id, c.id], "canonical_text": "texto unificado"}
            ],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await group_claims(
        [a, b, c], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert len(canonical) == 1
    merged = canonical[0]
    assert merged.text == "texto unificado"
    assert merged.source_model_response_id is None
    assert set(merged.merged_from_claim_ids) == {a.id, b.id, c.id}
    assert merged.total_models_in_round == 3
    assert {s.provider for s in merged.supporting_model_response_ids} == {
        "openai", "anthropic", "gemini"
    }
    assert merged.status == "consensus"  # ratio 3/3, >=2 participantes
    assert attempts[0].parse_status == "accepted"


@pytest.mark.asyncio
async def test_singleton_group_is_rejected_as_malformed():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    # grupo de 1 membro só viola ClaimGroupProposal.member_claim_ids (min 2)
    # na própria validação de schema — vira "malformed", não passa disso.
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [a.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [b.id],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response(
                "anthropic",
                json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]}),
            ),
        ],
    )

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert attempts[0].parse_status == "malformed"
    assert canonical == []  # 2a tentativa: tudo ungrouped, nenhuma canônica criada


@pytest.mark.asyncio
async def test_unknown_id_reference_is_inconsistent():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [a.id, "id-que-nao-existe"], "canonical_text": "x"}],
            "ungrouped_claim_ids": [b.id],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response(
                "anthropic",
                json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]}),
            ),
        ],
    )

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "inconsistent_references"
    assert canonical == []


@pytest.mark.asyncio
async def test_id_in_two_groups_is_inconsistent():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    c = _raw_claim("c", "gemini")
    payload = json.dumps(
        {
            "groups": [
                {"member_claim_ids": [a.id, b.id], "canonical_text": "x"},
                {"member_claim_ids": [a.id, c.id], "canonical_text": "y"},
            ],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response(
                "anthropic",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": [a.id, b.id, c.id], "canonical_text": "z"}],
                        "ungrouped_claim_ids": [],
                    }
                ),
            ),
        ],
    )

    canonical, attempts = await group_claims(
        [a, b, c], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "inconsistent_references"
    assert len(canonical) == 1  # 2a tentativa foi válida


@pytest.mark.asyncio
async def test_overlap_between_group_and_ungrouped_is_inconsistent():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [a.id],  # 'a' aparece nos dois
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response(
                "anthropic",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}],
                        "ungrouped_claim_ids": [],
                    }
                ),
            ),
        ],
    )
    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "inconsistent_references"
    assert len(canonical) == 1


@pytest.mark.asyncio
async def test_forgotten_claim_violates_completeness():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    c = _raw_claim("c", "gemini")
    # 'c' não aparece em nenhum grupo nem em ungrouped — claim esquecida.
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response(
                "anthropic",
                json.dumps(
                    {
                        "groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}],
                        "ungrouped_claim_ids": [c.id],
                    }
                ),
            ),
        ],
    )
    canonical, attempts = await group_claims(
        [a, b, c], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "inconsistent_references"
    assert "faltando" in attempts[0].parse_error_message
    assert len(canonical) == 1  # 2a tentativa cobriu todo mundo


@pytest.mark.asyncio
async def test_ungrouped_claims_are_preserved_as_themselves():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    payload = json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    # nenhuma canônica criada — 'a' e 'b' continuam sendo elas mesmas,
    # o chamador não precisa fazer nada especial.
    assert canonical == []
    assert attempts[0].parse_status == "accepted"


@pytest.mark.asyncio
async def test_grouping_failure_preserves_raw_claims_untouched():
    """Se o agrupamento falhar totalmente (esgota retries), o chamador
    simplesmente não recebe canônicas — as brutas, que já existiam antes
    de group_claims ser chamada, não são afetadas de forma alguma."""
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    provider = ScriptedProvider(
        "anthropic",
        [transport_error_response("anthropic")],
    )
    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert canonical == []
    assert len(attempts) == 1
    assert attempts[0].transport_status == "error"
    # a e b, como objetos Python, continuam intactos e válidos
    assert a.text == "claim de openai"
    assert b.text == "claim de anthropic"


@pytest.mark.asyncio
async def test_canonical_status_active_when_ratio_below_one():
    a = _raw_claim("a", "openai", total=3)
    b = _raw_claim("b", "anthropic", total=3)
    payload = json.dumps(
        {"groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}], "ungrouped_claim_ids": []}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    canonical, _ = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert canonical[0].supporting_model_ratio == pytest.approx(2 / 3)
    assert canonical[0].status == "active"  # ratio<1.0 nunca vira disputed automaticamente


# ---------------------------------------------------------------------------
# Etapa 17A (B2) — retry bloqueado por budget já esgotado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_blocked_when_budget_already_exhausted():
    a, b = _raw_claim("a", "openai"), _raw_claim("b", "anthropic")
    provider = ScriptedProvider("anthropic", [text_response("anthropic", "não é JSON válido")])

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(max_cost_usd=0.05),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.06,
    )

    assert len(attempts) == 1  # sem retry
    assert canonical == []


# ---------------------------------------------------------------------------
# Etapa 17A.1 (Objetivo D) — tolerância a cerca de código Markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_run_fenced_json_shape_succeeds():
    """Reprodução exata do Run B: JSON de agrupamento genuinamente
    válido (nenhum grupo, tudo ungrouped), mas envolto numa cerca de
    código Markdown -- antes da Etapa 17A.1 isso era rejeitado
    inteiramente como malformado."""
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    payload = (
        '```json\n{"groups": [], "ungrouped_claim_ids": ["'
        + a.id
        + '", "'
        + b.id
        + '"]}\n```'
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(), prior_input_tokens=0, prior_output_tokens=0,
        prior_cost_usd=0.0,
    )

    assert attempts[0].parse_status == "accepted"  # não mais "malformed"
    assert canonical == []  # nenhum grupo, mas o parse teve sucesso


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo C/D) — truncamento conhecido cancela o retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_truncation_on_malformed_grouping_output_skips_retry():
    """Reprodução do Run B (Evidência C): agrupamento cortado no meio por
    max_tokens não deve disparar uma 2a tentativa idêntica -- só 1
    resposta roteirizada; se o retry fosse tentado, o ScriptedProvider
    esgotaria o roteiro."""
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response(
                "anthropic",
                '{"groups": [{"member_claim_ids": ["' + a.id + '"',
                provider_finish_reason="max_tokens",
            )
        ],
    )

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert canonical == []
    assert len(attempts) == 1
    assert attempts[0].parse_status == "malformed"
    assert attempts[0].provider_finish_reason == "max_tokens"
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_malformed_grouping_output_without_truncation_signal_still_retries():
    """Regressão: malformação SEM motivo de truncamento confirmado
    continua retentando normalmente."""
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "não é JSON", provider_finish_reason="end_turn"),
            text_response(
                "anthropic", json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]})
            ),
        ],
    )

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert canonical == []
    assert len(attempts) == 2
    assert attempts[1].parse_status == "accepted"
