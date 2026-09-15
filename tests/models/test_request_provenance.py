"""
Provider-Neutral Request Provenance V1 -- testes da função canônica de
digest (`compute_request_digest`), do helper de construção
(`build_request_provenance`), e do value object `RequestProvenance`.

Função pura -- nenhum destes testes chama um provider real.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.provider_models import CompletionRequest, Message
from app.models.request_provenance import (
    REQUEST_DIGEST_PREFIX,
    RequestProvenance,
    build_request_provenance,
    compute_request_digest,
)


def _request(**overrides) -> CompletionRequest:
    fields = dict(
        messages=[Message(role="user", content="pergunta")],
        system_prompt="system",
        model=None,
        max_tokens=1024,
        temperature=None,
    )
    fields.update(overrides)
    return CompletionRequest(**fields)


# ---------------------------------------------------------------------------
# A -- identidade
# ---------------------------------------------------------------------------


def test_identical_completion_request_produces_identical_digest():
    r1 = _request()
    r2 = _request()
    assert compute_request_digest(r1) == compute_request_digest(r2)


def test_same_object_computed_twice_is_deterministic():
    r = _request()
    assert compute_request_digest(r) == compute_request_digest(r)


# ---------------------------------------------------------------------------
# B-H -- cada campo semântico muda o digest
# ---------------------------------------------------------------------------


def test_system_prompt_change_changes_digest():
    base = compute_request_digest(_request(system_prompt="a"))
    changed = compute_request_digest(_request(system_prompt="b"))
    assert base != changed


def test_system_prompt_none_differs_from_system_prompt_present():
    base = compute_request_digest(_request(system_prompt=None))
    changed = compute_request_digest(_request(system_prompt="a"))
    assert base != changed


def test_message_content_change_changes_digest():
    base = compute_request_digest(_request(messages=[Message(role="user", content="a")]))
    changed = compute_request_digest(_request(messages=[Message(role="user", content="b")]))
    assert base != changed


def test_message_role_change_changes_digest():
    base = compute_request_digest(_request(messages=[Message(role="user", content="x")]))
    changed = compute_request_digest(_request(messages=[Message(role="assistant", content="x")]))
    assert base != changed


def test_message_order_change_changes_digest():
    m1 = Message(role="user", content="primeiro")
    m2 = Message(role="user", content="segundo")
    base = compute_request_digest(_request(messages=[m1, m2]))
    changed = compute_request_digest(_request(messages=[m2, m1]))
    assert base != changed


def test_model_none_differs_from_explicit_model():
    base = compute_request_digest(_request(model=None))
    changed = compute_request_digest(_request(model="gpt-5.5"))
    assert base != changed


def test_max_tokens_change_changes_digest():
    base = compute_request_digest(_request(max_tokens=100))
    changed = compute_request_digest(_request(max_tokens=200))
    assert base != changed


def test_temperature_none_differs_from_explicit_temperature():
    base = compute_request_digest(_request(temperature=None))
    changed = compute_request_digest(_request(temperature=0.0))
    assert base != changed


def test_temperature_value_change_changes_digest():
    base = compute_request_digest(_request(temperature=0.1))
    changed = compute_request_digest(_request(temperature=0.9))
    assert base != changed


# ---------------------------------------------------------------------------
# I -- Unicode: UTF-8, sem normalização
# ---------------------------------------------------------------------------


def test_unicode_content_is_utf8_encoded_not_ascii_escaped():
    """`ensure_ascii=False` -- dois requests com o MESMO texto Unicode
    sempre produzem o MESMO digest; o teste em si prova determinismo,
    não a representação interna (que não é observável de fora da
    função)."""
    r1 = _request(messages=[Message(role="user", content="café ☕ 日本語")])
    r2 = _request(messages=[Message(role="user", content="café ☕ 日本語")])
    assert compute_request_digest(r1) == compute_request_digest(r2)


def test_unicode_content_differs_from_ascii_lookalike():
    r1 = _request(messages=[Message(role="user", content="café")])
    r2 = _request(messages=[Message(role="user", content="cafe")])
    assert compute_request_digest(r1) != compute_request_digest(r2)


def test_no_unicode_normalization_nfc_vs_nfd_are_distinct():
    """"é" pode ser representado como um único code point (NFC, U+00E9)
    ou como "e" + combining acute accent (NFD, U+0065 U+0301) -- Unicode-
    equivalentes visualmente, mas STRINGS PYTHON DIFERENTES. A função
    NUNCA normaliza -- os dois digests precisam ser diferentes, provando
    que nenhuma normalização (NFC/NFKC/etc.) está sendo aplicada."""
    nfc = "café"  # é = U+00E9
    nfd = "café"  # e + combining acute accent U+0301
    assert nfc != nfd  # sanity check -- são strings Python distintas
    digest_nfc = compute_request_digest(_request(messages=[Message(role="user", content=nfc)]))
    digest_nfd = compute_request_digest(_request(messages=[Message(role="user", content=nfd)]))
    assert digest_nfc != digest_nfd


# ---------------------------------------------------------------------------
# J -- ordenação canônica de chaves de objeto não afeta serialização
# ---------------------------------------------------------------------------


def test_canonical_key_ordering_is_independent_of_construction_order():
    """`sort_keys=True` -- o dict canônico é sempre serializado na MESMA
    ordem de chaves, independente da ordem em que os campos foram
    passados pro construtor do CompletionRequest (que nem influencia
    ordem de dict em Python de qualquer forma, mas o teste documenta a
    garantia explicitamente)."""
    r1 = CompletionRequest(
        messages=[Message(role="user", content="x")],
        system_prompt="sp",
        model="m",
        max_tokens=10,
        temperature=0.5,
    )
    r2 = CompletionRequest(
        temperature=0.5,
        max_tokens=10,
        model="m",
        system_prompt="sp",
        messages=[Message(role="user", content="x")],
    )
    assert compute_request_digest(r1) == compute_request_digest(r2)


# ---------------------------------------------------------------------------
# K -- provider identity / effective model não são inputs do digest
# ---------------------------------------------------------------------------


def test_digest_computation_has_no_provider_or_response_parameter():
    """Prova estrutural -- a função só aceita um CompletionRequest, não
    tem NENHUM parâmetro de provider/ProviderResponse/model efetivo por
    onde essa informação pudesse influenciar o digest."""
    import inspect

    signature = inspect.signature(compute_request_digest)
    assert list(signature.parameters) == ["request"]


def test_digest_is_identical_regardless_of_which_provider_would_execute_it():
    """Mesmo CompletionRequest, mesmo digest -- não existe forma de
    passar "qual provider" nem "qual modelo efetivo" pra função, então
    dois providers diferentes executando o MESMO request produzem o
    MESMO digest por construção."""
    r = _request()
    assert compute_request_digest(r) == compute_request_digest(r)


# ---------------------------------------------------------------------------
# L -- validação de prefixo/formato fechada
# ---------------------------------------------------------------------------


def test_request_provenance_rejects_wrong_prefix():
    with pytest.raises(ValidationError):
        RequestProvenance(contract_version="judge_v1", request_digest="sha256:" + "a" * 64)


def test_request_provenance_rejects_short_hex():
    with pytest.raises(ValidationError):
        RequestProvenance(
            contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "a" * 63
        )


def test_request_provenance_rejects_long_hex():
    with pytest.raises(ValidationError):
        RequestProvenance(
            contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "a" * 65
        )


def test_request_provenance_rejects_uppercase_hex():
    with pytest.raises(ValidationError):
        RequestProvenance(
            contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "A" * 64
        )


def test_request_provenance_rejects_non_hex_characters():
    with pytest.raises(ValidationError):
        RequestProvenance(
            contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "g" * 64
        )


def test_request_provenance_accepts_well_formed_digest():
    provenance = RequestProvenance(
        contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "a" * 64
    )
    assert provenance.contract_version == "judge_v1"


def test_request_provenance_rejects_blank_contract_version():
    with pytest.raises(ValidationError):
        RequestProvenance(contract_version="", request_digest=REQUEST_DIGEST_PREFIX + "a" * 64)


def test_request_provenance_is_frozen():
    provenance = RequestProvenance(
        contract_version="judge_v1", request_digest=REQUEST_DIGEST_PREFIX + "a" * 64
    )
    with pytest.raises(ValidationError):
        provenance.contract_version = "editor_v1"


def test_request_provenance_forbids_extra_fields():
    with pytest.raises(ValidationError):
        RequestProvenance(
            contract_version="judge_v1",
            request_digest=REQUEST_DIGEST_PREFIX + "a" * 64,
            provider="anthropic",  # nunca aceito -- ver seção 3 do contrato
        )


def test_request_provenance_never_carries_provider_or_model_fields():
    """Estrutural -- o schema fechado (`extra=forbid`) já impede isso em
    runtime, mas este teste documenta explicitamente o conjunto de
    campos permitido."""
    assert set(RequestProvenance.model_fields) == {"contract_version", "request_digest"}


# ---------------------------------------------------------------------------
# build_request_provenance -- helper de construção único
# ---------------------------------------------------------------------------


def test_build_request_provenance_combines_version_and_digest():
    r = _request()
    provenance = build_request_provenance("judge_v1", r)
    assert provenance.contract_version == "judge_v1"
    assert provenance.request_digest == compute_request_digest(r)


def test_build_request_provenance_uses_the_exact_request_object_semantics():
    """A provenance construída a partir de um request precisa bater
    byte a byte com o digest computado independentemente do MESMO
    objeto -- nunca uma reconstrução aproximada."""
    r = _request(system_prompt="prompt específico")
    provenance = build_request_provenance("editor_v1", r)
    assert provenance.request_digest == compute_request_digest(r)


# ---------------------------------------------------------------------------
# M -- fail-closed de deriva de schema: CompletionRequest field set pinado
# ---------------------------------------------------------------------------


def test_completion_request_field_set_is_pinned():
    """Accepted Question Size Boundary V1... não, Provider-Neutral
    Request Provenance V1, seção 5 do contrato -- se `CompletionRequest`
    ganhar um campo semântico novo sem que `_canonical_completion_request_payload`
    seja explicitamente revisada, este teste falha ANTES de qualquer
    digest silenciosamente ignorar esse campo. Conjunto declarado de
    forma INDEPENDENTE (nunca lido de dentro de
    `_canonical_completion_request_payload`) -- comparar contra a
    própria função canonicalizadora seria tautológico."""
    expected_fields = {"messages", "system_prompt", "model", "max_tokens", "temperature"}
    assert set(CompletionRequest.model_fields) == expected_fields
