from __future__ import annotations

import json

import pytest

from app.debate.claim_extraction import extract_claims
from app.models.domain import Claim, ClaimSupport, ModelResponse
from app.models.provider_models import TokenUsage
from tests.council.fixtures import run_config as _run_config
from tests.debate.fakes import ScriptedProvider, text_response, transport_error_response


def _response(**overrides) -> ModelResponse:
    fields = dict(
        provider="openai",
        model="gpt-test",
        round_number=1,
        status="success",
        response_text="Brasília é a capital do Brasil.",
        usage=TokenUsage(input_tokens=20, output_tokens=10),
        latency_ms=100,
        attempts=1,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return ModelResponse(**fields)


def _old_claim(claim_id: str, text: str, total: int = 2) -> Claim:
    """Constrói uma claim 'do round 1' com id fixo (pra referenciar em
    testes de revises_claim_id) — precisa passar id explicitamente já
    que normalmente é gerado automaticamente."""
    return Claim(
        id=claim_id,
        text=text,
        source_model_response_id="resp-old",
        round_introduced=1,
        status="active",
        supporting_model_response_ids=[
            ClaimSupport(model_response_id="resp-old", provider="openai", model="gpt-test")
        ],
        total_models_in_round=total,
    )


@pytest.mark.asyncio
async def test_extraction_with_zero_claims_is_valid():
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert claims == []
    assert len(attempts) == 1
    assert attempts[0].parse_status == "accepted"


@pytest.mark.asyncio
async def test_extraction_produces_claims_correctly_linked():
    payload = json.dumps({"claims": [{"text": "Brasília é a capital.", "revises_claim_id": None}]})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    response = _response()

    claims, attempts, _verifications = await extract_claims(
        response, round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert len(claims) == 1
    claim = claims[0]
    assert claim.text == "Brasília é a capital."
    assert claim.source_model_response_id == response.id
    assert claim.round_introduced == 1
    assert claim.status == "active"
    assert claim.parent_claim_id is None
    assert claim.total_models_in_round == 3
    assert claim.supporting_model_response_ids[0].model_response_id == response.id
    assert claim.supporting_model_response_ids[0].provider == "openai"
    assert attempts[0].parse_status == "accepted"
    assert attempts[0].target_model_response_id == response.id


@pytest.mark.asyncio
async def test_malformed_json_retries_once_then_succeeds():
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "isto não é JSON"),
            text_response("anthropic", '{"claims": []}'),
        ],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert claims == []
    assert len(attempts) == 2
    assert attempts[0].parse_status == "malformed"
    assert attempts[0].attempt_number == 1
    assert attempts[1].parse_status == "accepted"
    assert attempts[1].attempt_number == 2


@pytest.mark.asyncio
async def test_schema_mismatch_is_malformed():
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", '{"claims": [{"revises_claim_id": null}]}'),  # falta 'text'
            text_response("anthropic", '{"claims": []}'),
        ],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "malformed"
    assert claims == []


@pytest.mark.asyncio
async def test_retry_exhausted_yields_zero_claims_without_crashing():
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "json ruim 1"),
            text_response("anthropic", "json ruim 2"),
        ],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert claims == []
    assert len(attempts) == 2  # esgotou o máximo de 2 tentativas
    assert all(a.parse_status == "malformed" for a in attempts)


@pytest.mark.asyncio
async def test_transport_error_is_not_retried_by_this_layer():
    provider = ScriptedProvider("anthropic", [transport_error_response("anthropic")])
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert claims == []
    assert len(attempts) == 1  # sem retry — LLMProvider já esgotou o dele
    assert attempts[0].transport_status == "error"
    assert attempts[0].parse_status == "not_attempted"
    assert provider.received_requests  # só 1 chamada de fato


@pytest.mark.asyncio
async def test_round_1_rejects_revises_claim_id():
    payload = json.dumps(
        {"claims": [{"text": "algo", "revises_claim_id": "algum-id-antigo"}]}
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response("anthropic", '{"claims": []}'),
        ],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=None,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "inconsistent_references"
    assert claims == []  # a 2a tentativa (sem revises_claim_id) foi aceita, 0 claims


@pytest.mark.asyncio
async def test_round_2_accepts_valid_revision_reference():
    old_claim_1 = _old_claim("claim-antiga-1", "Brasília foi fundada em 1960.")
    old_claim_2 = _old_claim("claim-antiga-2", "O Brasil tem 26 estados.")
    payload = json.dumps(
        {"claims": [{"text": "correção da claim anterior", "revises_claim_id": "claim-antiga-1"}]}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])
    claims, attempts, _verifications = await extract_claims(
        _response(round_number=2), round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim_1, old_claim_2],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "accepted"
    assert len(claims) == 1
    assert claims[0].parent_claim_id == "claim-antiga-1"
    assert claims[0].source_model_response_id is not None  # origem própria continua honesta


@pytest.mark.asyncio
async def test_round_2_rejects_reference_outside_context():
    old_claim_1 = _old_claim("claim-antiga-1", "Brasília foi fundada em 1960.")
    payload = json.dumps(
        {"claims": [{"text": "correção", "revises_claim_id": "id-que-nao-estava-no-contexto"}]}
    )
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", payload),
            text_response("anthropic", '{"claims": []}'),
        ],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(round_number=2), round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim_1],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    assert attempts[0].parse_status == "inconsistent_references"
    assert claims == []


@pytest.mark.asyncio
async def test_extraction_rejects_status_error_response():
    from app.models.provider_models import ProviderErrorInfo, ProviderErrorType

    error_response = _response(
        status="error",
        response_text=None,
        usage=None,
        error=ProviderErrorInfo(type=ProviderErrorType.TIMEOUT, message="x", retryable=True),
    )
    with pytest.raises(ValueError, match="status='success'"):
        await extract_claims(
            error_response,
            round_number=1, total_models_in_round=3,
            extractor=ScriptedProvider("anthropic", []),
            max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)


# ---------------------------------------------------------------------------
# Correção pós-Etapa-5: revises_claim_id precisa de id+texto, não só id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_2_request_contains_id_to_text_association_not_just_ids():
    """A LLM não consegue decidir semanticamente qual claim está sendo
    revisada só com uma lista opaca de UUIDs — o request real precisa
    conter o TEXTO de cada claim anterior associado ao seu id."""
    old_claim_1 = _old_claim("claim-antiga-1", "Brasília foi fundada em 1960.")
    old_claim_2 = _old_claim("claim-antiga-2", "O Brasil tem 26 estados e um DF.")
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])

    await extract_claims(
        _response(round_number=2), round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim_1, old_claim_2],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert len(provider.received_requests) == 1
    body = provider.received_requests[0].messages[0].content

    # o id sozinho não basta — o TEXTO de cada claim precisa estar associado a ele
    assert old_claim_1.id in body
    assert old_claim_1.text in body
    assert old_claim_2.id in body
    assert old_claim_2.text in body

    # confirma que é uma associação estruturada id->texto (JSON), não uma
    # lista solta de ids sem contexto nenhum
    payload_section = body.split("CLAIMS_ANTERIORES", 1)[1]
    parsed = json.loads(payload_section.split(":", 1)[1].strip())
    assert {"id": old_claim_1.id, "text": old_claim_1.text} in parsed
    assert {"id": old_claim_2.id, "text": old_claim_2.text} in parsed


@pytest.mark.asyncio
async def test_round_1_extraction_request_never_mentions_known_claims_section():
    """Sem known_claims (round 1), a seção CLAIMS_ANTERIORES nem aparece
    — reforça que não há confusão possível sobre o que é referenciável."""
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])
    await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=None,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)
    body = provider.received_requests[0].messages[0].content
    assert "CLAIMS_ANTERIORES" not in body


# ---------------------------------------------------------------------------
# Correção pós-Etapa-5: revises_claim_id só pra revisão, nunca mera contestação
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_distinguishes_revision_from_mere_contestation():
    """Teste conceitual: o prompt de extração precisa deixar explícito
    que revises_claim_id é só pra correção/substituição, e que mera
    discordância/contestação NÃO deve preencher esse campo — senão a
    claim contestada desapareceria de get_current_claims() como se
    tivesse sido substituída, quando na verdade só foi discordada."""
    old_claim = _old_claim("claim-antiga-1", "X é verdadeiro.")
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])

    await extract_claims(
        _response(round_number=2), round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    system_prompt = provider.received_requests[0].system_prompt
    lowered = system_prompt.lower()
    # menciona explicitamente correção/revisão/substituição como o critério positivo
    assert any(word in lowered for word in ("corrige", "revisa", "substitui"))
    # e menciona explicitamente que discordância/contestação NÃO conta
    assert "discord" in lowered or "contesta" in lowered


@pytest.mark.asyncio
async def test_mere_disagreement_can_still_be_extracted_as_independent_claim():
    """Uma claim que meramente discorda de outra (revises_claim_id=None,
    mesmo com known_claims disponíveis) continua sendo uma claim válida
    e independente — o schema/validação não exige nem força
    revises_claim_id, só valida quando ele É preenchido."""
    old_claim = _old_claim("claim-antiga-1", "X é verdadeiro.")
    payload = json.dumps(
        {"claims": [{"text": "Discordo — X é falso.", "revises_claim_id": None}]}
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, _verifications = await extract_claims(
        _response(round_number=2), round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert attempts[0].parse_status == "accepted"
    assert len(claims) == 1
    assert claims[0].parent_claim_id is None  # independente — não substitui old_claim


# ---------------------------------------------------------------------------
# Etapa 15 — isolamento entre extração de Claim e verificação numérica
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_numeric_proposal_does_not_invalidate_valid_claim():
    """VALID CLAIM + INVALID NUMERIC PROPOSAL = VALID CLAIM + invalid_proposal
    -- nunca 'extração malformada'. O payload de proposed_numeric_assertion
    é deliberadamente Any/permissivo em ExtractedClaimDraft -- só a claim
    em si passa pela validação atômica de ClaimExtractionOutput."""
    payload = json.dumps(
        {
            "claims": [
                {
                    "text": "2 + 2 é 5, segundo o texto.",
                    "revises_claim_id": None,
                    "proposed_numeric_assertion": {
                        "left": "2",
                        "operator": "^^^ operador inválido",
                        "right": "2",
                        "asserted_result": "5",
                    },
                }
            ]
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert len(claims) == 1
    assert claims[0].text == "2 + 2 é 5, segundo o texto."
    assert attempts[-1].parse_status == "accepted"  # extração NÃO foi rejeitada
    assert len(verifications) == 1
    assert verifications[0].state == "invalid_proposal"
    assert verifications[0].claim_id == claims[0].id


@pytest.mark.asyncio
async def test_malformed_numeric_proposal_does_not_trigger_extraction_retry():
    """A metadata do Stage 15 sozinha nunca deveria consumir a segunda
    tentativa de output estruturado -- só um output MALFORMADO de verdade
    (JSON quebrado, schema errado) o faz. Confirma isso rodando com
    UMA única resposta scriptada: se houvesse retry, o provider
    scriptado ficaria sem resposta e o teste falharia."""
    payload = json.dumps(
        {
            "claims": [
                {
                    "text": "claim válida",
                    "revises_claim_id": None,
                    "proposed_numeric_assertion": "isso nem é um dict",
                }
            ]
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert len(attempts) == 1  # uma única tentativa consumida, não duas
    assert len(claims) == 1
    assert verifications[0].state == "invalid_proposal"


@pytest.mark.asyncio
async def test_valid_numeric_proposal_attached_to_correct_claim():
    payload = json.dumps(
        {
            "claims": [
                {
                    "text": "2 + 2 é 4.",
                    "revises_claim_id": None,
                    "proposed_numeric_assertion": {
                        "left": "2", "operator": "+", "right": "2", "asserted_result": "4",
                    },
                },
                {"text": "claim sem asserção numérica", "revises_claim_id": None},
            ]
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, _attempts, verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert len(claims) == 2
    assert len(verifications) == 1  # a segunda claim nunca gera registro (never_attempted)
    assert verifications[0].claim_id == claims[0].id
    assert verifications[0].state == "supports"


# ---------------------------------------------------------------------------
# Etapa 17A (B2) — retry bloqueado por budget já esgotado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_blocked_when_budget_already_exhausted_by_first_attempt():
    """Só 1 resposta roteirizada -- se o retry fosse tentado mesmo com
    budget esgotado (bug), o ScriptedProvider levantaria AssertionError
    interna por esgotar o roteiro."""
    provider = ScriptedProvider("anthropic", [text_response("anthropic", "não é JSON válido")])

    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(max_cost_usd=0.05),
        prior_input_tokens=0, prior_output_tokens=0, prior_cost_usd=0.06,  # já excedido
    )

    assert len(attempts) == 1  # sem retry
    assert claims == []


# ---------------------------------------------------------------------------
# Etapa 17A.1 (Objetivo D) — tolerância a cerca de código Markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fenced_json_extraction_output_succeeds():
    payload = '```json\n{"claims": [{"text": "claim qualquer", "revises_claim_id": null}]}\n```'
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(), prior_input_tokens=0, prior_output_tokens=0,
        prior_cost_usd=0.0,
    )

    assert attempts[-1].parse_status == "accepted"
    assert len(claims) == 1


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo A) — Round-2 não deve reintroduzir concordância/
# reafirmação/defesa pura como claim nova
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_2_prompt_instructs_against_pure_agreement_restatement():
    """O texto do prompt precisa deixar explícito que concordância,
    reafirmação, defesa ou mera referência a uma claim já listada NÃO
    deve virar claim nova -- só existe forma de testar isso
    deterministicamente checando o CONTEÚDO do prompt (a extração real é
    responsabilidade do modelo, nunca de filtragem determinística
    aplicada depois)."""
    old_claim = _old_claim("claim-antiga-1", "X é verdadeiro.")
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])

    await extract_claims(
        _response(round_number=2), round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    system_prompt = provider.received_requests[0].system_prompt
    lowered = system_prompt.lower()
    assert "concordo com a claim" in lowered
    assert "concordância" in lowered or "reafirmação" in lowered
    assert "proposição factual" in lowered


@pytest.mark.asyncio
async def test_round_1_prompt_never_mentions_agreement_restatement_guidance():
    """Round 1 não tem known_claims -- não existe "concordância com uma
    claim anterior" possível, então o prompt do round 1 continua
    byte-a-byte sem essa seção nova (mesma garantia já provada pra
    CLAIMS_ANTERIORES em
    test_round_1_extraction_request_never_mentions_known_claims_section)."""
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])
    await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=None,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    system_prompt = provider.received_requests[0].system_prompt
    assert "Concordo com a claim" not in system_prompt
    assert "proposição factual" not in system_prompt.lower()


@pytest.mark.asyncio
async def test_pure_agreement_response_yields_zero_new_claims():
    """Documenta o contrato: quando o extrator (bem-comportado, seguindo
    o prompt novo) devolve 0 claims para um trecho de puro
    concordância/reafirmação, a aplicação não tenta inventar nada --
    0 continua sendo um resultado válido, igual já era pra qualquer
    resposta sem afirmação extraível."""
    old_claim = _old_claim("claim-antiga-1", "PostgreSQL pode travar updates concorrentes.")
    payload = json.dumps({"claims": []})
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, _verifications = await extract_claims(
        _response(round_number=2, response_text="Concordo com a claim X. Mantenho minha posição sobre Y."),
        round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert attempts[0].parse_status == "accepted"
    assert claims == []


@pytest.mark.asyncio
async def test_agreement_plus_new_proposition_yields_only_the_new_claim():
    """Documenta o contrato: quando a crítica mistura concordância pura
    com uma proposição genuinamente nova, o extrator (bem-comportado)
    devolve só a proposição nova -- a aplicação converte exatamente o
    que foi devolvido, sem inflar nem descartar."""
    old_claim = _old_claim("claim-antiga-1", "A claim X é forte demais.")
    payload = json.dumps(
        {
            "claims": [
                {
                    "text": "PostgreSQL também pode travar updates na mesma linha.",
                    "revises_claim_id": None,
                }
            ]
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, _verifications = await extract_claims(
        _response(
            round_number=2,
            response_text=(
                "Concordo com a claim X, mas ela é forte demais porque "
                "PostgreSQL também pode travar updates na mesma linha."
            ),
        ),
        round_number=2, total_models_in_round=2,
        extractor=provider, max_output_tokens_per_call=1024,
        known_claims=[old_claim],
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert attempts[0].parse_status == "accepted"
    assert len(claims) == 1
    assert claims[0].text == "PostgreSQL também pode travar updates na mesma linha."
    assert claims[0].parent_claim_id is None


# ---------------------------------------------------------------------------
# Etapa 17A.2 (Objetivo C/D) — truncamento conhecido cancela o retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_truncation_on_malformed_output_skips_retry():
    """Se a 1a tentativa vier com JSON malformado (truncado) E
    provider_finish_reason confirmar corte por teto de output, a 2a
    tentativa (idêntica) NUNCA é disparada -- só 1 resposta roteirizada;
    se o retry fosse tentado, o ScriptedProvider esgotaria o roteiro e
    levantaria AssertionError."""
    provider = ScriptedProvider(
        "anthropic",
        [text_response("anthropic", '{"claims": [{"text": "corta', provider_finish_reason="max_tokens")],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert claims == []
    assert len(attempts) == 1
    assert attempts[0].parse_status == "malformed"
    assert attempts[0].provider_finish_reason == "max_tokens"
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_malformed_output_without_truncation_signal_still_retries():
    """Regressão: um output malformado SEM motivo de truncamento
    confirmado continua se comportando exatamente como antes (retry
    normal) -- a mudança da Etapa 17A.2 nunca reduz retry pra malformação
    "comum"."""
    provider = ScriptedProvider(
        "anthropic",
        [
            text_response("anthropic", "isto não é JSON", provider_finish_reason="end_turn"),
            text_response("anthropic", '{"claims": []}'),
        ],
    )
    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,)

    assert claims == []
    assert len(attempts) == 2
    assert attempts[1].parse_status == "accepted"


# ---------------------------------------------------------------------------
# Calibração de granularidade de extração (investigação de "claim
# amplification" upstream) -- SOMENTE contratos de CONTEÚDO DO PROMPT.
#
# Por que não há teste comportamental "este texto produz N claims": a
# extração real (decidir se uma sentença composta vira 1 ou 2 claims) é
# julgamento do modelo, nunca aplicado por filtragem determinística
# depois (mesma disciplina já estabelecida por
# test_round_2_prompt_instructs_against_pure_agreement_restatement,
# cujo docstring documenta exatamente esse mesmo motivo). Um
# ScriptedProvider só prova que a aplicação processa corretamente
# QUALQUER JSON que o "modelo" (roteirizado) devolva -- nunca que um
# modelo real faria a mesma escolha de granularidade. Por isso todo
# teste abaixo verifica o TEXTO do prompt enviado, nunca a contagem de
# claims resultante de uma chamada real.
# ---------------------------------------------------------------------------


async def _sent_system_prompt(round_number: int, known_claims: list[Claim] | None) -> str:
    provider = ScriptedProvider("anthropic", [text_response("anthropic", '{"claims": []}')])
    await extract_claims(
        _response(round_number=round_number),
        round_number=round_number,
        total_models_in_round=2,
        extractor=provider,
        max_output_tokens_per_call=1024,
        known_claims=known_claims,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )
    return provider.received_requests[0].system_prompt


@pytest.mark.asyncio
async def test_granularity_contract_states_independently_judgeable_proposition_principle():
    """9 -- o prompt precisa comunicar o critério central: uma claim por
    proposição avaliável de forma independente sem perder o sentido
    essencial."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "proposição" in prompt
    assert "independente" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_rejects_mechanical_clause_splitting():
    """9 -- o prompt precisa deixar explícito que NÃO é pra criar uma
    claim por oração/cláusula gramatical automaticamente."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "cláusula" in prompt or "oração" in prompt
    assert "automaticamente" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_requires_qualifier_preservation():
    """9/3 -- o prompt precisa instruir explicitamente a preservação de
    qualificadores essenciais (incerteza/condição/frequência), com
    exemplos concretos, e proibir removê-los pra criar uma afirmação
    categórica."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "qualificador" in prompt
    assert "provavelmente" in prompt
    assert "categórica" in prompt or "categorica" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_allows_causal_relations_as_claims():
    """9/Correção da investigação -- o prompt NÃO pode declarar relações
    causais como não-claims; precisa dizer explicitamente que uma
    relação causal PODE ser, ela mesma, uma claim legítima e
    independentemente avaliável."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "causal" in prompt
    assert "legítima" in prompt or "legitima" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_preserves_hedging_and_disagreement():
    """9 -- o prompt precisa instruir a preservação de incerteza,
    contrastes e discordâncias genuínas, nunca apagá-las ou fundi-las
    numa afirmação mais forte."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "incerteza" in prompt
    assert "discordâncias" in prompt or "discordancias" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_forbids_positive_negative_restatement_duplication():
    """9 -- o prompt precisa proibir criar uma segunda claim só por
    reformular a mesma proposição de forma positiva/negativa ou
    equivalente (o caso 'resultado é 42, não 40')."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "positiva" in prompt and "negativa" in prompt
    assert "reforça" in prompt or "reforçada" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_forbids_isolating_dependent_explanatory_fragments():
    """9 -- o prompt precisa proibir transformar um fragmento
    explicativo em claim própria quando ele não pode ser avaliado
    sozinho, desconectado da afirmação da qual depende."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "fragmento explicativo" in prompt
    assert "desconectado" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_keeps_independent_propositions_separate_in_one_sentence():
    """9 -- o prompt precisa deixar explícito que proposições
    genuinamente independentes na MESMA frase não devem ser fundidas."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "mesma frase" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_has_no_numerical_claim_count_target():
    """4/9 -- o prompt precisa dizer explicitamente que NÃO existe meta
    numérica de claims, e que uma resposta com várias proposições
    independentes deve gerar várias claims -- nunca uma instrução
    POSITIVA pra minimizar/preferir poucas claims (a palavra "minimizar"
    pode aparecer dentro da própria NEGAÇÃO -- "nem significa minimizar"
    -- por isso a checagem é pela frase negada completa, não pela
    ausência bruta da palavra)."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "meta numérica" in prompt or "meta numerica" in prompt
    assert "nem significa minimizar" in prompt
    assert "prefira poucas" not in prompt
    assert "no máximo" not in prompt
    assert "no maximo" not in prompt


@pytest.mark.asyncio
async def test_granularity_contract_worked_examples_present():
    """5 -- os 3 exemplos calibrados (SQLite+FastAPI; superaquecimento
    provável com causa; 42 não 40) precisam estar presentes
    verbatim o bastante pra serem localizáveis."""
    prompt = await _sent_system_prompt(1, None)
    assert "SQLite" in prompt and "FastAPI" in prompt
    assert "superaqueceu" in prompt.lower() or "superaquecimento" in prompt.lower()
    assert "42" in prompt and "40" in prompt


@pytest.mark.asyncio
def _categorical_and_unqualified(
    text: str, categorical_phrase: str, qualifier: str = "provavelmente"
) -> bool:
    """Helper de TESTE (nunca lógica de produção -- só existe aqui, pra
    tornar as checagens de escopo de qualificador precisas e legíveis,
    ver docstring de test_causal_example_preserves_qualifier_scope_faithfully).

    Confirma que `categorical_phrase` aparece em `text` como cláusula
    categórica intacta, sem `qualifier` prefixado nem inserido dentro
    dela. Rejeita as duas formas de mutação adversarial que uma
    checagem positiva ingênua deixaria passar:

    1. PREFIXO -- '{qualifier} {categorical_phrase}' (ex.: 'provavelmente
       o dispositivo superaqueceu'): a substring categórica original
       ainda aparece CONTÍGUA dentro do texto mutado, então só checar
       `categorical_phrase in text` não pegaria isso -- por isso a
       checagem negativa explícita do prefixo.
    2. INSERÇÃO NO MEIO -- ex.: 'o fluxo de ar provavelmente estava
       bloqueado': quebra a contiguidade da frase categórica original
       (a substring exata some), então a própria checagem positiva de
       contiguidade já falha nesse caso, sem precisar de regra
       adicional."""
    if categorical_phrase not in text:
        return False
    if f"{qualifier} {categorical_phrase}" in text:
        return False
    return True


_EXPECTED_CAUSAL_EXAMPLE_SOURCE = (
    "O dispositivo superaqueceu e o fluxo de ar estava bloqueado; o "
    "bloqueio do fluxo de ar provavelmente causou o superaquecimento"
)


@pytest.mark.asyncio
async def test_causal_example_preserves_qualifier_scope_faithfully():
    """Correção pós-revisão independente (3ª rodada, endurecimento de
    contrato de teste -- achado MEDIUM) -- as versões anteriores deste
    teste usavam âncoras de substring fracas o bastante pra continuar
    passando sob mutações que reintroduzem exatamente o defeito original
    de escopo de qualificador, só que numa posição diferente (ex.:
    'Provavelmente o dispositivo superaqueceu e...', ou 'o fluxo de ar
    provavelmente estava bloqueado'). Este teste protege a distribuição
    de certeza COMPLETA das três proposições, na FONTE e na
    DECOMPOSIÇÃO, separadamente.

    FONTE (isolada entre aspas simples logo após "(b) '", nunca o
    exemplo inteiro nem o prompt inteiro -- não é um snapshot do
    prompt): verificada por IGUALDADE EXATA contra o texto fixo
    esperado. Isto é deliberado e permitido -- o exemplo (b) é uma
    frase-fonte FIXA, não prosa livre; qualquer mutação de posição do
    qualificador (prefixo no evento, inserção no meio da cláusula do
    fluxo de ar, ou remoção do qualificador da causal) produz uma
    string DIFERENTE da esperada e falha aqui imediatamente, sem
    precisar enumerar cada mutação possível uma a uma.

    DECOMPOSIÇÃO (o comentário após a fonte, cuja prosa ao redor das
    cláusulas fixas pode variar -- nunca checada por igualdade exata):
    usa `_categorical_and_unqualified` pra confirmar que as duas
    proposições categóricas (superaquecimento, bloqueio de ar) aparecem
    intactas, sem 'provavelmente' nem como prefixo nem inserido no
    meio, e que 'provavelmente causou' (a cláusula causal qualificada)
    está presente.

    Este teste FALHA contra TODAS as variantes inseguras conhecidas:
    (A) 'O dispositivo provavelmente superaqueceu porque...' (hedge
        direto no evento); (B) 'O dispositivo superaqueceu, provavelmente
        porque...' (hedge ainda ambíguo sobre o bloqueio de ar); (C)
        'Provavelmente o dispositivo superaqueceu e...' (qualificador
        prefixado no início, escopo sobre a frase inteira); (D) 'o
        fluxo de ar provavelmente estava bloqueado' (qualificador
        inserido dentro da 2a proposição categórica) -- as quatro
        produzem uma frase-fonte diferente da esperada, então a
        igualdade exata já rejeita todas. (E)/(F) as mesmas duas
        últimas mutações aplicadas à DECOMPOSIÇÃO em vez da fonte são
        rejeitadas por `_categorical_and_unqualified` (verificado
        isoladamente antes deste teste, com texto extraído
        programaticamente da produção -- ver histórico de revisão).

    Nenhuma dessas checagens prova comportamento de um provider real --
    só que o TEXTO do prompt (produzido deterministicamente por esta
    função) continua exatamente como o exemplo pretende ensinar."""
    prompt = await _sent_system_prompt(1, None)
    start = prompt.index("(b)")
    end = prompt.index("(c)")
    example_b = prompt[start:end]

    # isola só a frase-fonte citada, entre as aspas simples logo após "(b) '"
    source_marker = "(b) '"
    source_start = example_b.index(source_marker) + len(source_marker)
    source_end = example_b.index("'", source_start)
    source_sentence = example_b[source_start:source_end]
    decomposition = example_b[source_end:].lower()

    # FONTE -- igualdade exata: rejeita mutações A-D de uma vez, sem
    # depender de enumerar cada uma delas como uma âncora separada.
    assert source_sentence == _EXPECTED_CAUSAL_EXAMPLE_SOURCE

    # DECOMPOSIÇÃO -- as duas proposições categóricas, intactas, sem
    # 'provavelmente' prefixado nem inserido no meio (rejeita mutações
    # E/F).
    assert _categorical_and_unqualified(decomposition, "o dispositivo superaqueceu")
    assert _categorical_and_unqualified(decomposition, "o fluxo de ar estava bloqueado")

    # DECOMPOSIÇÃO -- a cláusula causal qualificada está presente,
    # explicitamente.
    assert "provavelmente causou o superaquecimento" in decomposition

    # instrução explícita contra as duas direções da falha (mantida das
    # revisões anteriores)
    assert "mova" in decomposition and "qualificador" in decomposition
    assert "categórica" in decomposition or "categorica" in decomposition


@pytest.mark.asyncio
async def test_granularity_contract_applies_to_round_1():
    """6 -- a calibração de granularidade está presente no round 1
    (sem known_claims), confirmando que faz parte do prompt BASE
    compartilhado, não de um bloco exclusivo de round 2."""
    prompt = (await _sent_system_prompt(1, None)).lower()
    assert "critério de granularidade" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_applies_to_round_2():
    """6 -- a MESMA calibração de granularidade também chega ao round 2
    -- não é substituída nem omitida quando known_claims está presente."""
    old_claim = _old_claim("claim-antiga-1", "X é verdadeiro.")
    prompt = (await _sent_system_prompt(2, [old_claim])).lower()
    assert "critério de granularidade" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_coexists_with_round_2_restatement_and_revision_guidance():
    """6/10 -- a calibração de granularidade convive com (nunca
    substitui) as regras já existentes de round 2: supressão de mera
    reafirmação e distinção revisão-vs-contestação continuam presentes,
    inalteradas em significado, no MESMO prompt que já carrega a nova
    orientação de granularidade."""
    old_claim = _old_claim("claim-antiga-1", "X é verdadeiro.")
    prompt = (await _sent_system_prompt(2, [old_claim])).lower()

    # granularidade presente
    assert "critério de granularidade" in prompt
    # supressão de reafirmação presente e inalterada (mesmas âncoras já
    # usadas por test_round_2_prompt_instructs_against_pure_agreement_restatement)
    assert "concordo com a claim" in prompt
    assert "concordância" in prompt or "reafirmação" in prompt
    # distinção revisão-vs-contestação presente e inalterada (mesmas
    # âncoras já usadas por test_prompt_distinguishes_revision_from_mere_contestation)
    assert any(word in prompt for word in ("corrige", "revisa", "substitui"))
    assert "discord" in prompt or "contesta" in prompt


@pytest.mark.asyncio
async def test_granularity_contract_never_instructs_minimizing_claims():
    """4 -- checagem negativa direta: nenhuma variação de "minimize"/
    "reduza"/"prefira menos" aparece em nenhum dos dois rounds."""
    for round_number, known in ((1, None), (2, [_old_claim("c1", "Y.")])):
        prompt = (await _sent_system_prompt(round_number, known)).lower()
        for forbidden in ("minimize", "reduza a quantidade", "prefira menos", "prefira poucas"):
            assert forbidden not in prompt


@pytest.mark.asyncio
async def test_scripted_output_with_many_independent_claims_is_processed_without_alteration():
    """Complemento -- prova que a APLICAÇÃO (nunca o julgamento de
    granularidade em si, ver docstring da seção) processa corretamente
    uma extração com VÁRIAS claims independentes já roteirizadas, sem
    fundir/descartar nenhuma -- confirma que nada na aplicação impõe uma
    contagem máxima ou mínima por resposta."""
    payload = json.dumps(
        {
            "claims": [
                {"text": "A escola começou com 240 alunos.", "revises_claim_id": None},
                {"text": "30 alunos saíram.", "revises_claim_id": None},
                {"text": "54 alunos entraram.", "revises_claim_id": None},
                {"text": "A escola terminou com 264 alunos.", "revises_claim_id": None},
            ]
        }
    )
    provider = ScriptedProvider("anthropic", [text_response("anthropic", payload)])

    claims, attempts, _verifications = await extract_claims(
        _response(), round_number=1, total_models_in_round=3,
        extractor=provider, max_output_tokens_per_call=1024,
        run_config=_run_config(),
        prior_input_tokens=0,
        prior_output_tokens=0,
        prior_cost_usd=0.0,
    )

    assert len(claims) == 4
    assert attempts[0].parse_status == "accepted"
    assert {c.text for c in claims} == {
        "A escola começou com 240 alunos.",
        "30 alunos saíram.",
        "54 alunos entraram.",
        "A escola terminou com 264 alunos.",
    }
