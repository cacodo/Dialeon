"""
Provider Default-Model Snapshot Provenance V1 -- testes da função
canônica pura `build_default_model_authority_snapshot`
(app/application/service.py) e do domínio `DefaultModelAuthoritySnapshot`
(app/models/provider_models.py).

Nenhuma chamada real de rede/provider aqui -- só objetos fake mínimos
com `.default_model`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.application.errors import UnknownProviderError
from app.application.service import build_default_model_authority_snapshot
from app.models.provider_models import DefaultModelAuthoritySnapshot
from tests.council.fixtures import run_config


class _FakeProvider:
    def __init__(self, default_model: str):
        self.default_model = default_model


def _providers(**default_models: str) -> dict:
    return {name: _FakeProvider(model) for name, model in default_models.items()}


# ---------------------------------------------------------------------------
# C2 -- construído dos objetos de provider REAIS, nunca de Settings
# ---------------------------------------------------------------------------


def test_c2_snapshot_built_from_actual_provider_objects_not_settings():
    """Nenhum parâmetro `Settings` sequer existe na assinatura da
    função -- estruturalmente impossível derivar o snapshot de lá."""
    import inspect

    signature = inspect.signature(build_default_model_authority_snapshot)
    assert list(signature.parameters) == ["run_config", "providers"]

    rc = run_config(
        enabled_providers=["openai"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    providers = _providers(openai="gpt-custom-value", anthropic="claude-custom-value")

    snapshot = build_default_model_authority_snapshot(rc, providers)

    assert snapshot.configured_default_models["openai"] == "gpt-custom-value"
    assert snapshot.configured_default_models["anthropic"] == "claude-custom-value"


# ---------------------------------------------------------------------------
# C3 -- membership EXATA de all_provider_authorities
# ---------------------------------------------------------------------------


def test_c3_snapshot_contains_exactly_all_provider_authorities_membership():
    rc = run_config(
        enabled_providers=["openai", "gemini"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    providers = _providers(
        openai="gpt-x", gemini="gemini-x", anthropic="claude-x", mistral="mistral-x"
    )

    snapshot = build_default_model_authority_snapshot(rc, providers)

    assert set(snapshot.configured_default_models) == rc.all_provider_authorities
    assert set(snapshot.configured_default_models) == {"openai", "gemini", "anthropic"}


# ---------------------------------------------------------------------------
# C4 -- uso duplicado de papel interno deduplica autoridade de provider
# ---------------------------------------------------------------------------


def test_c4_duplicate_role_usage_deduplicates_provider_authority():
    """`anthropic` é usado por 3 papéis internos DIFERENTES + é
    participante -- o snapshot tem exatamente UMA entrada pra ele,
    nunca 4."""
    rc = run_config(
        enabled_providers=["anthropic"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    providers = _providers(anthropic="claude-x")

    snapshot = build_default_model_authority_snapshot(rc, providers)

    assert snapshot.configured_default_models == {"anthropic": "claude-x"}
    assert len(snapshot.configured_default_models) == 1


# ---------------------------------------------------------------------------
# C5 -- provider instalado NÃO relacionado é excluído
# ---------------------------------------------------------------------------


def test_c5_unrelated_installed_provider_is_excluded():
    rc = run_config(
        enabled_providers=["openai"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    # "gemini"/"mistral" existem no registry mas NÃO são autorizados por
    # este run_config -- nunca devem aparecer no snapshot.
    providers = _providers(
        openai="gpt-x", anthropic="claude-x", gemini="gemini-x", mistral="mistral-x"
    )

    snapshot = build_default_model_authority_snapshot(rc, providers)

    assert set(snapshot.configured_default_models) == {"openai", "anthropic"}
    assert "gemini" not in snapshot.configured_default_models
    assert "mistral" not in snapshot.configured_default_models


# ---------------------------------------------------------------------------
# C6 -- provider autorizado ausente falha ANTES do aceite
# ---------------------------------------------------------------------------


def test_c6_missing_authorized_provider_fails_closed():
    rc = run_config(
        enabled_providers=["openai"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    # "anthropic" está ausente do registry -- autorizado, mas não existe.
    providers = _providers(openai="gpt-x")

    with pytest.raises(UnknownProviderError) as exc_info:
        build_default_model_authority_snapshot(rc, providers)

    assert exc_info.value.unknown_providers == ["anthropic"]


def test_c6_missing_authorized_provider_never_produces_a_partial_snapshot():
    """Nenhum snapshot PARCIAL é retornado -- a função levanta antes de
    devolver qualquer `DefaultModelAuthoritySnapshot`."""
    rc = run_config(
        enabled_providers=["openai", "gemini"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    providers = _providers(openai="gpt-x")  # gemini E anthropic ausentes

    with pytest.raises(UnknownProviderError) as exc_info:
        build_default_model_authority_snapshot(rc, providers)

    assert set(exc_info.value.unknown_providers) == {"gemini", "anthropic"}


# ---------------------------------------------------------------------------
# C7 -- modelo padrão precisa ser válido/não-vazio (mesma disciplina de
# construção de provider já existente)
# ---------------------------------------------------------------------------


def test_c7_blank_default_model_from_a_provider_object_fails_closed():
    """Se um provider REAL (nunca deveria acontecer -- os construtores
    de provider já são validados) expuser `.default_model` vazio, o
    snapshot recusa construir -- fail-closed, nunca aceita
    silenciosamente um modelo padrão vazio na provenance."""
    rc = run_config(
        enabled_providers=["openai"],
        claim_processor_provider="anthropic",
        judge_provider="anthropic",
        editor_provider="anthropic",
        source_analyzer_provider="anthropic",
    )
    providers = _providers(openai="", anthropic="claude-x")

    with pytest.raises(ValidationError):
        build_default_model_authority_snapshot(rc, providers)


# ---------------------------------------------------------------------------
# Domínio -- DefaultModelAuthoritySnapshot em si
# ---------------------------------------------------------------------------


def test_snapshot_is_frozen_and_forbids_extra_fields():
    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    with pytest.raises(ValidationError):
        snapshot.configured_default_models = {}
    with pytest.raises(ValidationError):
        DefaultModelAuthoritySnapshot(
            configured_default_models={"openai": "gpt-x"}, extra_field="x"
        )


def test_snapshot_rejects_empty_mapping():
    with pytest.raises(ValidationError):
        DefaultModelAuthoritySnapshot(configured_default_models={})


# ---------------------------------------------------------------------------
# C16/C17/C18 -- separação de provenance: este snapshot NUNCA reinterpreta
# RequestProvenance/ModelIdentitySource, e NUNCA implica que um modelo
# de fato executou.
# ---------------------------------------------------------------------------


def test_c16_request_provenance_model_none_semantics_unchanged():
    """`CompletionRequest.model=None` continua registrado como `null` em
    `RequestProvenance` -- este slice NUNCA insere
    `provider.default_model` ali. Prova estrutural: `RequestProvenance`/
    `compute_request_digest` não ganharam nenhum parâmetro/campo novo
    relacionado a `DefaultModelAuthoritySnapshot`."""
    from app.models.provider_models import CompletionRequest, Message
    from app.models.request_provenance import RequestProvenance, compute_request_digest

    assert "default_model_authority_snapshot" not in RequestProvenance.model_fields
    assert "configured_default_models" not in RequestProvenance.model_fields

    request = CompletionRequest(messages=[Message(role="user", content="x")], model=None)
    digest_with_none_model = compute_request_digest(request)

    request_with_model = CompletionRequest(
        messages=[Message(role="user", content="x")], model="gpt-explicit"
    )
    digest_with_explicit_model = compute_request_digest(request_with_model)

    # model=None e model="gpt-explicit" continuam produzindo digests
    # DIFERENTES -- o digest ainda reflete fielmente o request real,
    # nunca "corrigido" por uma autoridade de fallback configurada.
    assert digest_with_none_model != digest_with_explicit_model


def test_c17_model_identity_provenance_semantics_unchanged():
    """`ModelIdentitySource`/`ModelResponse.model_identity_source`
    continuam exigindo evidência de resposta REAL -- nenhum campo novo
    relacionado ao snapshot foi adicionado a `ModelResponse`."""
    from app.models.domain import ModelResponse

    assert "default_model_authority_snapshot" not in ModelResponse.model_fields
    assert "configured_default_models" not in ModelResponse.model_fields
    assert "model_identity_source" in ModelResponse.model_fields  # continua existindo, intocado


def test_c18_snapshot_alone_never_implies_model_execution():
    """Um `DefaultModelAuthoritySnapshot` diz "modelo X estava
    CONFIGURADO como fallback pra este provider no aceite" -- nunca
    "modelo X executou". Prova direta: o snapshot não carrega nenhum
    campo de status/resultado/evidência de chamada -- só o mapping
    provider->modelo configurado."""
    assert set(DefaultModelAuthoritySnapshot.model_fields) == {"configured_default_models"}
    # nenhum desses conceitos (status de execução, resposta, tentativa,
    # sucesso/erro) existe no snapshot -- estruturalmente incapaz de
    # provar execução.
    for forbidden_concept in ("status", "response", "attempt", "success", "error", "executed"):
        assert forbidden_concept not in DefaultModelAuthoritySnapshot.model_fields


# ---------------------------------------------------------------------------
# F2 (repair pós-revisão independente, MEDIUM) -- imutabilidade GENUÍNA
# de `configured_default_models`, não só de reatribuição do campo em
# si. `frozen=True` sozinho bloqueia
# `snapshot.configured_default_models = {...}`, mas NUNCA bloqueava
# `snapshot.configured_default_models["x"] = "y"` -- o mapping
# subjacente continuava um `dict` mutável comum.
# ---------------------------------------------------------------------------


def test_f2_direct_item_mutation_fails():
    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    with pytest.raises(TypeError):
        snapshot.configured_default_models["openai"] = "HACKED"


def test_f2_source_dict_alias_mutation_after_construction_does_not_leak_in():
    """F2 -- isolamento do dict de ORIGEM: `MappingProxyType(dict(value))`
    (ver validador de `configured_default_models`) copia o conteúdo pra
    um `dict` NOVO antes de congelar -- nunca envolve (`MappingProxyType`
    direto sobre) o dict que o chamador passou. Mutar o dict de ORIGEM
    depois da construção não pode vazar pra dentro do snapshot já
    aceito -- senão a imutabilidade seria só de fachada (o chamador
    ainda teria uma referência viva capaz de alterar a provenance
    persistida por baixo)."""
    source = {"openai": "model-A"}
    snapshot = DefaultModelAuthoritySnapshot(configured_default_models=source)

    source["openai"] = "model-B"
    source["anthropic"] = "model-C"

    assert snapshot.configured_default_models == {"openai": "model-A"}
    assert dict(snapshot.configured_default_models) == {"openai": "model-A"}


def test_f2_contents_remain_unchanged_after_failed_mutation_attempt():
    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    try:
        snapshot.configured_default_models["openai"] = "HACKED"
    except TypeError:
        pass
    assert snapshot.configured_default_models == {"openai": "gpt-x"}


def test_f2_new_key_insertion_also_fails():
    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    with pytest.raises(TypeError):
        snapshot.configured_default_models["anthropic"] = "claude-x"
    with pytest.raises(TypeError):
        del snapshot.configured_default_models["openai"]
    assert dict(snapshot.configured_default_models) == {"openai": "gpt-x"}


def test_f2_mutation_via_accepted_record_reference_fails():
    """Mutação através de uma referência obtida de `AcceptedRunRecord`
    (nunca só do objeto `DefaultModelAuthoritySnapshot` isolado)."""
    from datetime import datetime, timezone

    from app.storage.records import AcceptedRunRecord
    from tests.storage.fixtures import run_config

    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    record = AcceptedRunRecord(
        status="running",
        id="run-f2-1",
        started_at=datetime.now(timezone.utc),
        run_config=run_config(),
        default_model_authority_snapshot=snapshot,
    )
    with pytest.raises(TypeError):
        record.default_model_authority_snapshot.configured_default_models["openai"] = "HACKED"
    assert record.default_model_authority_snapshot.configured_default_models == {
        "openai": "gpt-x"
    }


def test_f2_mutation_via_completed_record_reference_fails():
    """Idem acima, via `CompletedRunRecord` (o registro terminal real)."""
    from app.storage.records import CompletedRunRecord
    from tests.storage.fixtures import full_council_run_result

    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    record = CompletedRunRecord(
        council_run_result=full_council_run_result(),
        default_model_authority_snapshot=snapshot,
    )
    with pytest.raises(TypeError):
        record.default_model_authority_snapshot.configured_default_models["openai"] = "HACKED"
    assert record.default_model_authority_snapshot.configured_default_models == {
        "openai": "gpt-x"
    }


def test_f2_mutation_via_public_representation_fails():
    """Idem acima, via a representação pública real
    (`CompletedRunResponse`, construída pelo mapper canônico
    `completed_run_response`) -- reusa o MESMO objeto de domínio
    diretamente (nunca uma cópia mutável nova, ver
    app/presentation/mappers.py), então precisa herdar a mesma
    imutabilidade genuína."""
    from app.presentation.mappers import completed_run_response
    from tests.storage.fixtures import full_council_run_result

    snapshot = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    result = full_council_run_result()

    public_response = completed_run_response(
        result,
        provider_execution_policy=None,
        default_model_authority_snapshot=snapshot,
    )

    with pytest.raises(TypeError):
        public_response.default_model_authority_snapshot.configured_default_models[
            "openai"
        ] = "HACKED"
    assert public_response.default_model_authority_snapshot.configured_default_models == {
        "openai": "gpt-x"
    }


def test_f2_serialization_wire_shape_unchanged():
    """O formato de wire continua um `dict` JSON comum -- a
    representação interna imutável (`MappingProxyType`) nunca vaza pra
    fora da serialização."""
    snapshot = DefaultModelAuthoritySnapshot(
        configured_default_models={"openai": "gpt-x", "anthropic": "claude-x"}
    )
    from types import MappingProxyType

    dumped = snapshot.model_dump(mode="json")
    assert dumped == {
        "configured_default_models": {"openai": "gpt-x", "anthropic": "claude-x"}
    }
    assert isinstance(dumped["configured_default_models"], dict)
    assert not isinstance(dumped["configured_default_models"], MappingProxyType)

    import json

    round_tripped = json.loads(snapshot.model_dump_json())
    assert round_tripped == {
        "configured_default_models": {"openai": "gpt-x", "anthropic": "claude-x"}
    }


def test_f2_reconstruction_from_persisted_json_remains_immutable():
    """Reconstrução a partir de JSON persistido (mesmo caminho de
    `_default_model_authority_snapshot_from_json`,
    app/storage/repository.py) também produz um mapping genuinamente
    imutável -- não só a instância ORIGINAL antes de serializar."""
    original = DefaultModelAuthoritySnapshot(configured_default_models={"openai": "gpt-x"})
    persisted_json = original.model_dump(mode="json")

    reconstructed = DefaultModelAuthoritySnapshot(**persisted_json)

    with pytest.raises(TypeError):
        reconstructed.configured_default_models["openai"] = "HACKED"
    assert reconstructed.configured_default_models == {"openai": "gpt-x"}
