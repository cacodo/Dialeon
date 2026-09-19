from __future__ import annotations

import json

import pytest

from app.debate.claim_extraction import CLAIM_GROUPING_CONTRACT_VERSION, group_claims
from tests.council.fixtures import run_config as _run_config
from app.models.domain import Claim, ClaimSupport
from app.models.provider_models import ModelIdentitySource
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response


def _raw_claim(
    claim_id_suffix: str,
    provider: str,
    total: int = 3,
    model_identity_source: ModelIdentitySource | None = None,
) -> Claim:
    return Claim(
        text=f"claim de {provider}",
        source_model_response_id=f"resp-{claim_id_suffix}",
        round_introduced=1,
        status="active",
        supporting_model_response_ids=[
            ClaimSupport(
                model_response_id=f"resp-{claim_id_suffix}",
                provider=provider,
                model="m",
                model_identity_source=model_identity_source,
            )
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
async def test_grouping_preserves_each_members_model_identity_source_verbatim():
    """Cross-round/merge regression (repair pós-revisão independente) --
    `group_claims` (via `_merge_supports`) NUNCA reconstrói um
    ClaimSupport novo: reusa a MESMA instância dos membros fundidos, por
    design (ver `app/debate/claim_extraction.py::_merge_supports` --
    dedup por `model_response_id`, `merged.append(support)` sem
    reconstrução). Prova isso na fronteira pública: 3 membros com 3
    model_identity_source DIFERENTES entre si sobrevivem intactos, cada
    um no seu próprio support, depois da fusão."""
    a = _raw_claim("a", "openai", model_identity_source=ModelIdentitySource.PROVIDER_REPORTED)
    b = _raw_claim("b", "anthropic", model_identity_source=ModelIdentitySource.REQUESTED_FALLBACK)
    c = _raw_claim("c", "gemini", model_identity_source=None)
    payload = json.dumps(
        {
            "groups": [
                {"member_claim_ids": [a.id, b.id, c.id], "canonical_text": "texto unificado"}
            ],
            "ungrouped_claim_ids": [],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, _attempts = await group_claims(
        [a, b, c], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    merged = canonical[0]
    by_provider = {s.provider: s.model_identity_source for s in merged.supporting_model_response_ids}
    assert by_provider["openai"] == ModelIdentitySource.PROVIDER_REPORTED
    assert by_provider["anthropic"] == ModelIdentitySource.REQUESTED_FALLBACK
    assert by_provider["gemini"] is None


@pytest.mark.asyncio
async def test_singleton_group_is_normalized_to_ungrouped_without_a_retry():
    """INTENCIONALMENTE atualizado (claim_grouping_v3): antes, um grupo de
    1 membro era rejeitado como "malformed" e consumia o retry
    estruturado. Agora é a ÚNICA deformidade estrutural tolerada no
    agrupamento intra-round: normalizado deterministicamente (o id vira
    ungrouped, o canonical_text do grupo unitário é descartado), aceito
    como `accepted_normalized`, SEM retry. Cobertura/normalização em
    detalhe: tests/debate/test_grouping_singleton_normalization.py."""
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    payload = json.dumps(
        {
            "groups": [{"member_claim_ids": [a.id], "canonical_text": "x"}],
            "ungrouped_claim_ids": [b.id],
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert [x.parse_status for x in attempts] == ["accepted_normalized"]
    assert len(provider.received_requests) == 1  # nenhum retry consumido
    assert canonical == []  # nenhuma claim canônica criada a partir do singleton


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
async def test_same_round_grouping_round_introduced_equals_round_number():
    """19 -- fusão DENTRO da rodada 1: round_introduced=1, comportamento
    inalterado por `_build_canonical_claim` ter generalizado a regra pra
    max(member.round_introduced) -- todo membro aqui já compartilha
    round_introduced=1, então max() é idêntico ao round_number de antes."""
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
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
    assert canonical[0].round_introduced == 1


@pytest.mark.asyncio
async def test_round2_grouping_round_introduced_equals_two():
    """19 -- fusão DENTRO da rodada 2 (crítica): round_introduced=2, mesma
    generalização, mesmo resultado de antes."""
    a = _raw_claim("a", "openai")
    a = a.model_copy(update={"round_introduced": 2})
    b = _raw_claim("b", "anthropic")
    b = b.model_copy(update={"round_introduced": 2})
    payload = json.dumps(
        {"groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}], "ungrouped_claim_ids": []}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    canonical, _ = await group_claims(
        [a, b], round_number=2, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert canonical[0].round_introduced == 2


@pytest.mark.asyncio
async def test_ordinary_grouping_never_sets_support_scope_model_count():
    """18/19 -- agrupamento DENTRO de uma rodada nunca precisa de um
    universo de suporte maior que o da própria rodada --
    support_scope_model_count fica None, denominador continua sendo
    total_models_in_round (comportamento histórico)."""
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
    assert canonical[0].support_scope_model_count is None
    assert canonical[0].total_models_in_round == 3


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


# ---------------------------------------------------------------------------
# Provider-Neutral Request Provenance V1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_grouping_attempt_carries_request_provenance():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    payload = json.dumps(
        {"groups": [{"member_claim_ids": [a.id, b.id], "canonical_text": "x"}], "ungrouped_claim_ids": []}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    _canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(), prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert attempts[0].request_provenance is not None
    assert attempts[0].request_provenance.contract_version == CLAIM_GROUPING_CONTRACT_VERSION


@pytest.mark.asyncio
async def test_malformed_then_success_grouping_attempts_share_identical_request_provenance():
    a = _raw_claim("a", "openai")
    b = _raw_claim("b", "anthropic")
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "não é JSON"),
            text_response(
                "anthropic", json.dumps({"groups": [], "ungrouped_claim_ids": [a.id, b.id]})
            ),
        ],
    )

    _canonical, attempts = await group_claims(
        [a, b], round_number=1, grouper=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(), prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.0,
    )

    assert len(attempts) == 2
    assert attempts[0].request_provenance is not None
    assert attempts[0].request_provenance == attempts[1].request_provenance
