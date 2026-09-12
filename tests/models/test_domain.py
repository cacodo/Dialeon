from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.domain import (
    Claim,
    ClaimAssessment,
    ClaimSupport,
    EvidenceRef,
    JudgeVerdict,
    ModelResponse,
)
from app.models.provider_models import ProviderErrorInfo, ProviderErrorType


# ---------------------------------------------------------------------------
# ModelResponse
# ---------------------------------------------------------------------------


def _success_response(**overrides) -> ModelResponse:
    fields = dict(
        provider="openai",
        model="gpt-test",
        round_number=1,
        status="success",
        response_text="Brasília é a capital do Brasil.",
        latency_ms=120,
        attempts=1,
    )
    fields.update(overrides)
    fields.setdefault("requested_model", fields["model"])
    return ModelResponse(**fields)


def test_model_response_is_frozen():
    response = _success_response()
    with pytest.raises(ValidationError):
        response.response_text = "outra coisa"  # type: ignore[misc]


def test_model_response_success_requires_text():
    with pytest.raises(ValidationError, match="response_text"):
        _success_response(response_text=None)


def test_model_response_success_cannot_have_error():
    error = ProviderErrorInfo(type=ProviderErrorType.TIMEOUT, message="x", retryable=True)
    with pytest.raises(ValidationError, match="error"):
        _success_response(error=error)


def test_model_response_error_status_requires_error_info():
    with pytest.raises(ValidationError, match="error"):
        ModelResponse(
            provider="openai",
            requested_model="gpt-test",
            model="gpt-test",
            round_number=1,
            status="error",
            response_text=None,
            latency_ms=50,
            attempts=1,
        )


def test_model_response_error_status_cannot_have_text():
    error = ProviderErrorInfo(type=ProviderErrorType.AUTH, message="x", retryable=False)
    with pytest.raises(ValidationError, match="response_text"):
        ModelResponse(
            provider="openai",
            requested_model="gpt-test",
            model="gpt-test",
            round_number=1,
            status="error",
            response_text="isso não deveria existir",
            error=error,
            latency_ms=50,
            attempts=1,
        )


def test_model_response_valid_error_case():
    error = ProviderErrorInfo(type=ProviderErrorType.RATE_LIMIT, message="x", retryable=True)
    response = ModelResponse(
        provider="openai",
        requested_model="gpt-test",
        model="gpt-test",
        round_number=1,
        status="error",
        response_text=None,
        error=error,
        latency_ms=30,
        attempts=1,
    )
    assert response.status == "error"
    assert response.error.type == ProviderErrorType.RATE_LIMIT


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


def _support(model_response_id: str, provider: str, model: str = "test-model") -> ClaimSupport:
    return ClaimSupport(model_response_id=model_response_id, provider=provider, model=model)


def _claim(**overrides) -> Claim:
    fields = dict(
        text="TCP garante entrega confiável de dados.",
        source_model_response_id="resp-1",
        round_introduced=1,
        status="active",
        supporting_model_response_ids=[
            _support("resp-1", "openai"),
            _support("resp-2", "anthropic"),
        ],
        total_models_in_round=3,
    )
    fields.update(overrides)
    return Claim(**fields)


def test_claim_is_frozen():
    claim = _claim()
    with pytest.raises(ValidationError):
        claim.status = "resolved"  # type: ignore[misc]


def test_supporting_models_is_derived_and_deduplicated():
    """O mesmo provider/model respondendo em 2 responses diferentes
    (ex.: 2 rodadas) conta uma vez só em supporting_models."""
    claim = _claim(
        supporting_model_response_ids=[
            _support("resp-1", "openai", "gpt-test"),
            _support("resp-2", "anthropic", "claude-test"),
            _support("resp-3", "openai", "gpt-test"),  # mesmo modelo, response diferente
        ],
        total_models_in_round=3,
    )
    assert claim.supporting_models == ["openai/gpt-test", "anthropic/claude-test"]


def test_multiple_response_ids_from_same_model_are_preserved_in_source_of_truth():
    """A fonte de verdade (supporting_model_response_ids) NÃO deduplica —
    só a projeção supporting_models deduplica. Isso é o que corrige o
    problema original: dá pra saber que 'openai' apoiou via 2 responses
    específicos diferentes, não só que 'openai' apoiou."""
    claim = _claim(
        supporting_model_response_ids=[
            _support("resp-rodada-1", "openai", "gpt-test"),
            _support("resp-rodada-2", "openai", "gpt-test"),
        ],
        total_models_in_round=2,
    )
    response_ids = [s.model_response_id for s in claim.supporting_model_response_ids]
    assert response_ids == ["resp-rodada-1", "resp-rodada-2"]
    assert claim.supporting_models == ["openai/gpt-test"]  # deduplicado na projeção


def test_supporting_model_ratio_is_derived_correctly():
    claim = _claim(
        supporting_model_response_ids=[_support("resp-1", "openai"), _support("resp-2", "anthropic")],
        total_models_in_round=4,
    )
    assert claim.supporting_model_ratio == 0.5


def test_supporting_model_ratio_uses_deduplicated_count_not_raw_response_count():
    """2 responses do mesmo modelo não devem inflar o ratio como se
    fossem 2 modelos diferentes apoiando."""
    claim = _claim(
        supporting_model_response_ids=[
            _support("resp-1", "openai"),
            _support("resp-2", "openai"),
        ],
        total_models_in_round=4,
    )
    assert claim.supporting_model_ratio == 0.25  # 1 modelo único / 4, não 2/4


def test_supporting_model_ratio_cannot_be_set_directly():
    with pytest.raises(ValidationError, match="supporting_model_ratio"):
        Claim(
            text="alguma afirmação",
            source_model_response_id="resp-1",
            round_introduced=1,
            status="active",
            supporting_model_response_ids=[_support("resp-1", "openai")],
            total_models_in_round=3,
            supporting_model_ratio=0.99,  # type: ignore[call-arg]
        )


def test_supporting_models_cannot_be_set_directly():
    """Mesma garantia que supporting_model_ratio: supporting_models agora
    também é computed_field, então passá-lo no construtor é rejeitado
    como campo extra."""
    with pytest.raises(ValidationError, match="supporting_models"):
        Claim(
            text="alguma afirmação",
            source_model_response_id="resp-1",
            round_introduced=1,
            status="active",
            supporting_model_response_ids=[_support("resp-1", "openai")],
            total_models_in_round=3,
            supporting_models=["openai"],  # type: ignore[call-arg]
        )


def test_supporting_model_ratio_appears_in_serialization():
    claim = _claim(
        supporting_model_response_ids=[_support("resp-1", "openai")], total_models_in_round=2
    )
    dumped = claim.model_dump()
    assert dumped["supporting_model_ratio"] == 0.5
    assert dumped["supporting_models"] == ["openai/test-model"]


def test_supporting_models_deduplicated_cannot_exceed_total():
    with pytest.raises(ValidationError, match="total_models_in_round"):
        _claim(
            supporting_model_response_ids=[
                _support("resp-1", "openai"),
                _support("resp-2", "anthropic"),
                _support("resp-3", "gemini"),
                _support("resp-4", "mistral"),
            ],
            total_models_in_round=3,
        )


def test_supporting_model_response_ids_cannot_be_empty():
    with pytest.raises(ValidationError):
        _claim(supporting_model_response_ids=[])


def test_supporting_model_response_ids_rejects_duplicate_response_id():
    """O mesmo ModelResponse não pode aparecer 2 vezes na lista — isso
    seria a mesma resposta 'apoiando' duplicadamente, diferente do caso
    válido de 2 responses DIFERENTES do mesmo modelo."""
    with pytest.raises(ValidationError, match="model_response_id"):
        _claim(
            supporting_model_response_ids=[
                _support("resp-1", "openai"),
                _support("resp-1", "openai"),
            ]
        )


def test_claim_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _claim(confidence=1.5)
    with pytest.raises(ValidationError):
        _claim(confidence=-0.1)


def test_claim_superseded_status_requires_superseded_by():
    with pytest.raises(ValidationError, match="superseded_by"):
        _claim(status="superseded", superseded_by=None)


def test_claim_superseded_by_only_valid_with_superseded_status():
    with pytest.raises(ValidationError, match="superseded_by"):
        _claim(status="active", superseded_by="some-other-claim-id")


def test_claim_valid_superseded_case():
    claim = _claim(status="superseded", superseded_by="claim-2")
    assert claim.status == "superseded"
    assert claim.superseded_by == "claim-2"


def test_claim_lineage_via_parent_and_superseded_by():
    """Simula uma claim sendo 'atualizada' (evolução 1→1, não fusão)
    durante o debate: em vez de editar a original (impossível, é
    frozen), cria-se uma nova claim apontando pra ela via
    parent_claim_id, e a original é substituída (nova instância dela
    mesma com status='superseded')."""
    original = _claim()

    revised = _claim(
        text="TCP garante entrega confiável via retransmissão e ACKs.",
        parent_claim_id=original.id,
        status="active",
    )

    original_superseded = _claim(
        text=original.text,
        source_model_response_id=original.source_model_response_id,
        round_introduced=original.round_introduced,
        status="superseded",
        superseded_by=revised.id,
        supporting_model_response_ids=original.supporting_model_response_ids,
        total_models_in_round=original.total_models_in_round,
    )

    assert revised.parent_claim_id == original.id
    assert revised.merged_from_claim_ids == []
    assert original_superseded.superseded_by == revised.id


def test_claim_merge_via_merged_from_claim_ids():
    """Simula o agrupamento semântico (v3, item 6): 3 claims brutas
    equivalentes viram 1 claim canônica. Fusão é many→1 e usa
    merged_from_claim_ids, não parent_claim_id."""
    raw_1 = _claim(text="TCP garante entrega dos dados.")
    raw_2 = _claim(text="O protocolo TCP fornece entrega confiável.")
    raw_3 = _claim(text="TCP é orientado à transmissão confiável.")

    canonical = _claim(
        text="TCP garante entrega confiável de dados via retransmissão.",
        source_model_response_id=None,
        merged_from_claim_ids=[raw_1.id, raw_2.id, raw_3.id],
        supporting_model_response_ids=[
            _support("resp-a", "openai"),
            _support("resp-b", "anthropic"),
            _support("resp-c", "gemini"),
        ],
        total_models_in_round=3,
    )

    assert set(canonical.merged_from_claim_ids) == {raw_1.id, raw_2.id, raw_3.id}
    assert canonical.parent_claim_id is None  # fusão não é evolução 1→1


def test_claim_merged_from_claim_ids_cannot_reference_itself():
    claim_id = "claim-fusao-auto-ref"
    with pytest.raises(ValidationError, match="merged_from_claim_ids"):
        _claim(id=claim_id, merged_from_claim_ids=[claim_id, "outra-claim"])


def test_claim_merged_from_claim_ids_rejects_duplicates():
    with pytest.raises(ValidationError, match="merged_from_claim_ids"):
        _claim(merged_from_claim_ids=["claim-x", "claim-x"])


def test_claim_external_evidence_independent_of_supporting_ratio():
    """Uma claim pode ter ratio alto (muitos modelos concordam) e ainda
    assim nenhuma evidência externa — os dois campos não se influenciam."""
    claim_id = "claim-com-evidencia"
    evidence = EvidenceRef(
        claim_id=claim_id,
        summary="Nenhuma fonte externa consultada ainda.",
        verification_method="none",
    )
    claim = _claim(
        id=claim_id,
        supporting_model_response_ids=[
            _support("resp-1", "openai"),
            _support("resp-2", "anthropic"),
            _support("resp-3", "gemini"),
        ],
        total_models_in_round=3,
        external_evidence=evidence,
    )
    assert claim.supporting_model_ratio == 1.0
    assert claim.external_evidence.verification_method == "none"

    claim_sem_evidencia = _claim(
        supporting_model_response_ids=[
            _support("resp-1", "openai"),
            _support("resp-2", "anthropic"),
            _support("resp-3", "gemini"),
        ],
        total_models_in_round=3,
    )
    assert claim_sem_evidencia.supporting_model_ratio == 1.0
    assert claim_sem_evidencia.external_evidence is None


def test_claim_external_evidence_must_reference_this_claim_id():
    """external_evidence.claim_id precisa bater com o id da própria claim —
    evita o estado nonsense de uma claim carregar, aninhada, a evidência
    de outra claim."""
    evidence_de_outra_claim = EvidenceRef(
        claim_id="claim-completamente-diferente",
        summary="Evidência que pertence a outra claim.",
        verification_method="none",
    )
    with pytest.raises(ValidationError, match="external_evidence"):
        _claim(external_evidence=evidence_de_outra_claim)


def test_claim_parent_claim_id_cannot_reference_itself():
    claim_id = "claim-auto-ref"
    with pytest.raises(ValidationError, match="parent_claim_id"):
        _claim(id=claim_id, parent_claim_id=claim_id)


def test_claim_superseded_by_cannot_reference_itself():
    claim_id = "claim-auto-ref-2"
    with pytest.raises(ValidationError, match="superseded_by"):
        _claim(id=claim_id, status="superseded", superseded_by=claim_id)


# ---------------------------------------------------------------------------
# JudgeVerdict
# ---------------------------------------------------------------------------


def _verdict(**overrides) -> JudgeVerdict:
    fields = dict(
        evaluated_through_round=1,
        judge_model="claude-test",
        confidence=0.8,
        reasoning="Baseado nos argumentos apresentados, a claim X é consistente com Y.",
    )
    fields.update(overrides)
    return JudgeVerdict(**fields)


def test_judge_verdict_is_frozen():
    verdict = _verdict()
    with pytest.raises(ValidationError):
        verdict.confidence = 0.1  # type: ignore[misc]


def test_judge_verdict_requires_reasoning():
    with pytest.raises(ValidationError):
        _verdict(reasoning="")


def test_judge_verdict_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _verdict(confidence=1.1)
    with pytest.raises(ValidationError):
        _verdict(confidence=-0.01)


def test_judge_verdict_triggered_self_review_requires_reference():
    with pytest.raises(ValidationError, match="self_review_of"):
        _verdict(triggered_self_review=True, self_review_of=None)


def test_judge_verdict_self_review_of_requires_triggered_flag():
    with pytest.raises(ValidationError, match="self_review_of"):
        _verdict(triggered_self_review=False, self_review_of="verdict-1")


def test_judge_verdict_valid_self_review_case():
    original = _verdict()
    review = _verdict(triggered_self_review=True, self_review_of=original.id)
    assert review.self_review_of == original.id
    assert review.triggered_self_review is True


def test_judge_verdict_self_review_of_cannot_reference_itself():
    verdict_id = "verdict-auto-ref"
    with pytest.raises(ValidationError, match="self_review_of"):
        _verdict(id=verdict_id, triggered_self_review=True, self_review_of=verdict_id)


# ---------------------------------------------------------------------------
# Claim — origem exclusiva (Etapa 5)
# ---------------------------------------------------------------------------


def test_claim_rejects_having_both_source_and_merge():
    with pytest.raises(ValidationError, match="EXATAMENTE UMA origem"):
        _claim(
            source_model_response_id="resp-1",
            merged_from_claim_ids=["raw-a", "raw-b"],
        )


def test_claim_rejects_having_neither_source_nor_merge():
    with pytest.raises(ValidationError, match="EXATAMENTE UMA origem"):
        _claim(source_model_response_id=None, merged_from_claim_ids=[])


def test_claim_raw_with_only_source_is_valid():
    claim = _claim(source_model_response_id="resp-1", merged_from_claim_ids=[])
    assert claim.source_model_response_id == "resp-1"
    assert claim.merged_from_claim_ids == []


def test_claim_canonical_with_only_merge_is_valid():
    claim = _claim(
        source_model_response_id=None,
        merged_from_claim_ids=["raw-a", "raw-b"],
    )
    assert claim.source_model_response_id is None
    assert claim.merged_from_claim_ids == ["raw-a", "raw-b"]


def test_claim_merge_rejects_single_member():
    with pytest.raises(ValidationError, match="ao menos 2 ids"):
        _claim(source_model_response_id=None, merged_from_claim_ids=["raw-a"])


# ---------------------------------------------------------------------------
# EvidenceRef
# ---------------------------------------------------------------------------


def test_evidence_ref_requires_summary():
    with pytest.raises(ValidationError):
        EvidenceRef(claim_id="claim-1", summary="", verification_method="none")


def test_evidence_ref_rejects_invalid_verification_method():
    with pytest.raises(ValidationError):
        EvidenceRef(
            claim_id="claim-1",
            summary="resumo válido",
            verification_method="magic",  # type: ignore[arg-type]
        )


def test_evidence_ref_is_frozen():
    evidence = EvidenceRef(claim_id="claim-1", summary="resumo", verification_method="none")
    with pytest.raises(ValidationError):
        evidence.summary = "outro resumo"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ClaimAssessment / JudgeVerdict.claim_assessments (Etapa 6)
# ---------------------------------------------------------------------------


def _assessment(**overrides) -> ClaimAssessment:
    fields = dict(
        claim_id="claim-1",
        verdict="supported",
        explanation="A claim é sustentada pelos argumentos apresentados no debate.",
    )
    fields.update(overrides)
    return ClaimAssessment(**fields)


def test_claim_assessment_all_five_verdict_values_are_valid():
    for verdict in ("supported", "partially_supported", "rejected", "conflicting", "unresolved"):
        assessment = _assessment(verdict=verdict)
        assert assessment.verdict == verdict


def test_claim_assessment_rejects_unknown_verdict():
    with pytest.raises(ValidationError):
        _assessment(verdict="disproven")  # type: ignore[arg-type]


def test_claim_assessment_requires_explanation():
    with pytest.raises(ValidationError):
        _assessment(explanation="")


def test_claim_assessment_is_frozen():
    assessment = _assessment()
    with pytest.raises(ValidationError):
        assessment.verdict = "rejected"  # type: ignore[misc]


def test_judge_verdict_accepts_claim_assessments():
    verdict = _verdict(
        claim_assessments=[
            _assessment(claim_id="claim-1", verdict="supported"),
            _assessment(claim_id="claim-2", verdict="rejected"),
        ]
    )
    assert len(verdict.claim_assessments) == 2


def test_judge_verdict_rejects_duplicate_claim_id_in_assessments():
    with pytest.raises(ValidationError, match="claim_id duplicado"):
        _verdict(
            claim_assessments=[
                _assessment(claim_id="claim-1", verdict="supported"),
                _assessment(claim_id="claim-1", verdict="rejected"),
            ]
        )


def test_judge_verdict_evaluated_through_round_ge_1():
    with pytest.raises(ValidationError):
        _verdict(evaluated_through_round=0)


def test_judge_verdict_debate_limitations_default_empty():
    verdict = _verdict()
    assert verdict.debate_limitations == []
    assert verdict.claim_assessments == []
    assert verdict.best_arguments_by == {}
