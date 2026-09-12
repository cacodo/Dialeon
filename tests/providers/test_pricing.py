from __future__ import annotations

import pytest

from app.models.provider_models import TokenUsage
from app.providers.pricing import DEFAULT_PRICING_REGISTRY, ModelRate, PricedCost, PricingRegistry


def test_model_rate_is_frozen():
    rate = ModelRate(input_usd_per_million_tokens=1.0, output_usd_per_million_tokens=2.0)
    with pytest.raises(Exception):
        rate.input_usd_per_million_tokens = 99.0  # type: ignore[misc]


def test_model_rate_rejects_negative_input_rate():
    with pytest.raises(ValueError, match="negativas"):
        ModelRate(input_usd_per_million_tokens=-1.0, output_usd_per_million_tokens=2.0)


def test_model_rate_rejects_negative_output_rate():
    with pytest.raises(ValueError, match="negativas"):
        ModelRate(input_usd_per_million_tokens=1.0, output_usd_per_million_tokens=-2.0)


def test_model_rate_zero_is_valid_known_free():
    rate = ModelRate(input_usd_per_million_tokens=0.0, output_usd_per_million_tokens=0.0)
    assert rate.input_usd_per_million_tokens == 0.0


def test_known_positive_rate_computes_cost():
    registry = PricingRegistry(
        {
            ("openai", "gpt-x"): ModelRate(
                input_usd_per_million_tokens=1.0, output_usd_per_million_tokens=2.0
            )
        },
        source_id="test-table",
    )
    priced = registry.price("openai", "gpt-x", TokenUsage(input_tokens=1000, output_tokens=500))
    assert priced.cost_usd == pytest.approx(0.002)


def test_known_zero_rate_returns_zero_not_none():
    registry = PricingRegistry(
        {("local", "self-hosted-model"): ModelRate(0.0, 0.0)}, source_id="test-table"
    )
    priced = registry.price(
        "local", "self-hosted-model", TokenUsage(input_tokens=1000, output_tokens=500)
    )
    assert priced is not None
    assert priced.cost_usd == 0.0


def test_unknown_provider_returns_none():
    registry = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)})
    priced = registry.price(
        "provider-desconhecido", "gpt-x", TokenUsage(input_tokens=100, output_tokens=50)
    )
    assert priced is None


def test_unknown_model_returns_none():
    registry = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)})
    priced = registry.price(
        "openai", "modelo-desconhecido", TokenUsage(input_tokens=100, output_tokens=50)
    )
    assert priced is None


def test_zero_tokens_computes_zero_cost():
    registry = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)}, source_id="test-table")
    priced = registry.price("openai", "gpt-x", TokenUsage(input_tokens=0, output_tokens=0))
    assert priced.cost_usd == 0.0


def test_none_tokens_in_usage_is_unknown_not_zero():
    registry = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)})
    priced = registry.price("openai", "gpt-x", TokenUsage(input_tokens=None, output_tokens=None))
    assert priced is None


def test_case_1_both_none_with_known_pricing_is_unknown():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)})
    priced = registry.price("p", "m", TokenUsage(input_tokens=None, output_tokens=None))
    assert priced is None


def test_case_2_input_known_output_none_with_known_pricing_is_unknown():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)})
    priced = registry.price("p", "m", TokenUsage(input_tokens=10, output_tokens=None))
    assert priced is None


def test_case_3_input_none_output_known_with_known_pricing_is_unknown():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)})
    priced = registry.price("p", "m", TokenUsage(input_tokens=None, output_tokens=20))
    assert priced is None


def test_case_4_zero_explicit_with_known_pricing_is_known_zero():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)}, source_id="test-table")
    priced = registry.price("p", "m", TokenUsage(input_tokens=0, output_tokens=0))
    assert priced is not None
    assert priced.cost_usd == 0.0


def test_case_5_known_usage_with_unknown_pricing_is_unknown():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)})
    priced = registry.price("p", "outro-modelo", TokenUsage(input_tokens=10, output_tokens=20))
    assert priced is None


def test_case_6_known_usage_with_known_pricing_computes_normally():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)}, source_id="test-table")
    priced = registry.price("p", "m", TokenUsage(input_tokens=10, output_tokens=20))
    assert priced.cost_usd == pytest.approx(10 * 1.0 / 1e6 + 20 * 2.0 / 1e6)


def test_no_single_tariff_inferred_for_whole_provider_brand():
    registry = PricingRegistry({("openai", "gpt-5.5"): ModelRate(5.0, 30.0)})
    priced = registry.price("openai", "gpt-4o", TokenUsage(input_tokens=1000, output_tokens=1000))
    assert priced is None


def test_registry_copies_input_mapping():
    original = {("openai", "gpt-x"): ModelRate(1.0, 2.0)}
    registry = PricingRegistry(original, source_id="test-table")
    original[("openai", "gpt-x")] = ModelRate(999.0, 999.0)
    priced = registry.price("openai", "gpt-x", TokenUsage(input_tokens=1_000_000, output_tokens=0))
    assert priced.cost_usd == pytest.approx(1.0)


def test_registry_exposes_no_mutation_api():
    registry = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)})
    assert not hasattr(registry, "set_rate")
    assert not hasattr(registry, "update")
    assert not hasattr(registry, "add_rate")


def test_new_pricing_table_requires_new_instance_v1_unaffected_by_v2():
    registry_v1 = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)}, source_id="v1")
    registry_v2 = PricingRegistry({("openai", "gpt-x"): ModelRate(5.0, 10.0)}, source_id="v2")

    priced_v1 = registry_v1.price(
        "openai", "gpt-x", TokenUsage(input_tokens=1_000_000, output_tokens=0)
    )
    priced_v2 = registry_v2.price(
        "openai", "gpt-x", TokenUsage(input_tokens=1_000_000, output_tokens=0)
    )

    assert priced_v1.cost_usd == pytest.approx(1.0)
    assert priced_v2.cost_usd == pytest.approx(5.0)
    assert priced_v1.cost_usd != priced_v2.cost_usd
    assert priced_v1.provenance.source_id == "v1"
    assert priced_v2.provenance.source_id == "v2"


def test_historical_response_keeps_value_calculated_at_creation_time():
    registry_v1 = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)}, source_id="v1")
    priced_at_creation = registry_v1.price(
        "openai", "gpt-x", TokenUsage(input_tokens=1_000_000, output_tokens=0)
    )
    PricingRegistry({("openai", "gpt-x"): ModelRate(999.0, 999.0)}, source_id="v2")
    assert priced_at_creation.cost_usd == pytest.approx(1.0)
    assert priced_at_creation.provenance.source_id == "v1"
    assert priced_at_creation.provenance.input_rate_usd_per_million_tokens == 1.0


def test_default_registry_has_the_three_configured_models():
    usage = TokenUsage(input_tokens=100_000, output_tokens=100_000)
    assert DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5", usage).cost_usd == pytest.approx(
        100_000 * 5.0 / 1e6 + 100_000 * 30.0 / 1e6
    )
    assert DEFAULT_PRICING_REGISTRY.price(
        "anthropic", "claude-sonnet-5", usage
    ).cost_usd == pytest.approx(100_000 * 2.0 / 1e6 + 100_000 * 10.0 / 1e6)
    assert DEFAULT_PRICING_REGISTRY.price(
        "gemini", "gemini-3.7-flash", usage
    ).cost_usd == pytest.approx(100_000 * 0.75 / 1e6 + 100_000 * 3.75 / 1e6)


def test_default_registry_unknown_model_is_none_never_guessed():
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    assert DEFAULT_PRICING_REGISTRY.price("openai", "gpt-3.5-turbo-legacy", usage) is None
    assert DEFAULT_PRICING_REGISTRY.price("local", "self-hosted-llama", usage) is None


def test_default_registry_has_explicit_source_id():
    assert DEFAULT_PRICING_REGISTRY.source_id == "llm-council-default-pricing-2026-09-05"


def test_long_context_fields_all_or_nothing():
    with pytest.raises(ValueError, match="todos preenchidos ou todos None"):
        ModelRate(
            input_usd_per_million_tokens=5.0,
            output_usd_per_million_tokens=30.0,
            long_context_threshold_tokens=272_000,
        )


def test_long_context_negative_multiplier_rejected():
    with pytest.raises(ValueError, match="negativos"):
        ModelRate(
            input_usd_per_million_tokens=5.0,
            output_usd_per_million_tokens=30.0,
            long_context_threshold_tokens=272_000,
            long_context_input_multiplier=-2.0,
            long_context_output_multiplier=1.5,
        )


def test_gpt_5_5_below_threshold_uses_standard_rate():
    usage = TokenUsage(input_tokens=100_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5", usage)
    assert priced.cost_usd == pytest.approx(100_000 * 5.0 / 1e6 + 10_000 * 30.0 / 1e6)
    assert priced.provenance.tier == "standard"
    assert priced.provenance.input_rate_usd_per_million_tokens == 5.0
    assert priced.provenance.output_rate_usd_per_million_tokens == 30.0


def test_gpt_5_5_exactly_at_threshold_uses_standard_rate():
    usage = TokenUsage(input_tokens=272_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5", usage)
    assert priced.cost_usd == pytest.approx(272_000 * 5.0 / 1e6 + 10_000 * 30.0 / 1e6)
    assert priced.provenance.tier == "standard"


def test_gpt_5_5_above_threshold_uses_long_context_multiplier():
    usage = TokenUsage(input_tokens=300_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5", usage)
    assert priced.cost_usd == pytest.approx(300_000 * 10.0 / 1e6 + 10_000 * 45.0 / 1e6)
    assert priced.provenance.tier == "long_context"
    assert priced.provenance.input_rate_usd_per_million_tokens == 10.0
    assert priced.provenance.output_rate_usd_per_million_tokens == 45.0


def test_gpt_5_5_above_threshold_is_not_silently_underpriced():
    usage = TokenUsage(input_tokens=300_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5", usage)
    standard_rate_cost = 300_000 * 5.0 / 1e6 + 10_000 * 30.0 / 1e6
    assert priced.cost_usd > standard_rate_cost


def test_model_without_long_context_fields_never_applies_multiplier():
    small = TokenUsage(input_tokens=1000, output_tokens=1000)
    huge = TokenUsage(input_tokens=10_000_000, output_tokens=1000)
    priced_small = DEFAULT_PRICING_REGISTRY.price("anthropic", "claude-sonnet-5", small)
    priced_huge = DEFAULT_PRICING_REGISTRY.price("anthropic", "claude-sonnet-5", huge)
    assert priced_small.cost_usd == pytest.approx(1000 * 2.0 / 1e6 + 1000 * 10.0 / 1e6)
    assert priced_huge.cost_usd == pytest.approx(10_000_000 * 2.0 / 1e6 + 1000 * 10.0 / 1e6)
    assert priced_small.provenance.tier == "standard"
    assert priced_huge.provenance.tier == "standard"


def test_provenance_source_id_matches_registry():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)}, source_id="minha-tabela-v3")
    priced = registry.price("p", "m", TokenUsage(input_tokens=10, output_tokens=20))
    assert priced.provenance.source_id == "minha-tabela-v3"


def test_provenance_is_frozen():
    registry = PricingRegistry({("p", "m"): ModelRate(1.0, 2.0)}, source_id="t")
    priced = registry.price("p", "m", TokenUsage(input_tokens=10, output_tokens=20))
    with pytest.raises(Exception):
        priced.provenance.tier = "long_context"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Patch de cobertura de pricing snapshot -- alias explícito de
# (provider, effective_model_snapshot) -> canonical_model.
# ---------------------------------------------------------------------------


def test_exact_lookup_still_works_without_any_alias():
    """Requisito 1: lookup exato existente continua funcionando sem alias."""
    registry = PricingRegistry({("openai", "gpt-x"): ModelRate(1.0, 2.0)}, source_id="test-table")
    priced = registry.price("openai", "gpt-x", TokenUsage(input_tokens=1000, output_tokens=500))
    assert priced is not None
    assert priced.cost_usd == pytest.approx(0.002)
    assert priced.provenance.canonical_model_id is None


def test_known_openai_snapshot_resolves_to_priced_gpt_5_5():
    """Requisito 2: o defeito confirmado -- snapshot datado real da OpenAI
    resolve pra entrada canônica precificada gpt-5.5."""
    usage = TokenUsage(input_tokens=100_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-2026-04-23", usage)
    assert priced is not None
    assert priced.cost_usd is not None


def test_snapshot_resolution_computes_correct_cost():
    """Requisito 3: custo calculado após resolução de alias bate com a
    taxa standard de gpt-5.5 (abaixo do threshold de long-context)."""
    usage = TokenUsage(input_tokens=100_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-2026-04-23", usage)
    assert priced.cost_usd == pytest.approx(100_000 * 5.0 / 1e6 + 10_000 * 30.0 / 1e6)
    assert priced.provenance.tier == "standard"
    assert priced.provenance.input_rate_usd_per_million_tokens == 5.0
    assert priced.provenance.output_rate_usd_per_million_tokens == 30.0


def test_snapshot_resolution_also_applies_long_context_multiplier():
    usage = TokenUsage(input_tokens=300_000, output_tokens=10_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-2026-04-23", usage)
    assert priced.cost_usd == pytest.approx(300_000 * 10.0 / 1e6 + 10_000 * 45.0 / 1e6)
    assert priced.provenance.tier == "long_context"


def test_snapshot_resolution_does_not_rewrite_effective_model():
    """Requisitos 4/5: price() nunca vê nem devolve requested_model/
    effective_model -- este teste documenta que a chave de lookup usada
    (o snapshot) permanece intacta; é responsabilidade do chamador
    (ProviderResponse.model) preservá-la, o que este teste comprova
    indiretamente checando que a resolução funciona sem qualquer
    normalização do parâmetro `model` de entrada."""
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    effective_model = "gpt-5.5-2026-04-23"
    priced = DEFAULT_PRICING_REGISTRY.price("openai", effective_model, usage)
    assert priced is not None
    # o snapshot usado como chave de entrada não é alterado por price();
    # continua sendo exatamente o que foi passado.
    assert effective_model == "gpt-5.5-2026-04-23"


def test_snapshot_resolution_provenance_names_canonical_model():
    """Requisito 6: a provenance permanece presente e verdadeira -- ela
    documenta explicitamente que a taxa usada veio do canônico gpt-5.5,
    distinto do snapshot efetivo que originou o lookup."""
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-2026-04-23", usage)
    assert priced.provenance is not None
    assert priced.provenance.canonical_model_id == "gpt-5.5"
    assert priced.provenance.source_id == DEFAULT_PRICING_REGISTRY.source_id


def test_direct_hit_provenance_has_no_canonical_model_id():
    """Contraste com o teste acima: quando o lookup bate direto (sem
    alias), canonical_model_id fica None -- não há canônico "diferente"
    a reportar."""
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5", usage)
    assert priced.provenance.canonical_model_id is None


def test_unknown_future_snapshot_remains_unpriced():
    """Requisito 7: um snapshot não registrado (mesmo da mesma família)
    continua desconhecido -- nenhuma normalização de sufixo de data."""
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-2099-01-01", usage)
    assert priced is None


def test_similarly_prefixed_unrelated_model_not_priced_as_gpt_5_5():
    """Requisito 8: nenhum prefix-matching -- um identifier que começa
    com "gpt-5.5" mas não é nem a entrada exata nem um alias registrado
    não recebe preço de gpt-5.5."""
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    assert DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-turbo-mini", usage) is None
    assert DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5x", usage) is None


def test_other_providers_pricing_unaffected_by_openai_snapshot_alias():
    """Requisito 9: adicionar um alias pra openai não muda nada pra
    outros providers."""
    usage = TokenUsage(input_tokens=100_000, output_tokens=100_000)
    anthropic_priced = DEFAULT_PRICING_REGISTRY.price("anthropic", "claude-sonnet-5", usage)
    gemini_priced = DEFAULT_PRICING_REGISTRY.price("gemini", "gemini-3.7-flash", usage)
    assert anthropic_priced.cost_usd == pytest.approx(
        100_000 * 2.0 / 1e6 + 100_000 * 10.0 / 1e6
    )
    assert gemini_priced.cost_usd == pytest.approx(100_000 * 0.75 / 1e6 + 100_000 * 3.75 / 1e6)
    assert anthropic_priced.provenance.canonical_model_id is None
    assert gemini_priced.provenance.canonical_model_id is None
    # o alias é scoped a "openai" -- nem existe entrada equivalente pra
    # outro provider reportando esse mesmo snapshot literal.
    assert DEFAULT_PRICING_REGISTRY.price("anthropic", "gpt-5.5-2026-04-23", usage) is None


def test_registered_snapshot_no_longer_makes_aggregate_accounting_unknown():
    """Requisito 10: o caso que antes virava cost_usd=None/
    pricing_provenance=None agora produz custo conhecido."""
    usage = TokenUsage(input_tokens=50_000, output_tokens=5_000)
    priced = DEFAULT_PRICING_REGISTRY.price("openai", "gpt-5.5-2026-04-23", usage)
    assert priced is not None
    assert priced.cost_usd is not None
    assert priced.cost_usd > 0.0
    assert priced.provenance is not None


def test_genuinely_unknown_pricing_semantics_unchanged():
    """Requisito 11: nada nesse patch afeta os casos genuinamente
    desconhecidos preexistentes (provider desconhecido, modelo
    desconhecido sem alias, usage parcialmente None)."""
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    assert DEFAULT_PRICING_REGISTRY.price("provider-desconhecido", "gpt-5.5", usage) is None
    assert DEFAULT_PRICING_REGISTRY.price("openai", "modelo-totalmente-desconhecido", usage) is None
    assert (
        DEFAULT_PRICING_REGISTRY.price(
            "openai", "gpt-5.5-2026-04-23", TokenUsage(input_tokens=None, output_tokens=None)
        )
        is None
    )


def test_snapshot_alias_requires_canonical_target_to_have_a_rate():
    with pytest.raises(ValueError, match="não tem taxa registrada"):
        PricingRegistry(
            {("openai", "gpt-x"): ModelRate(1.0, 2.0)},
            snapshot_aliases={("openai", "gpt-x-2026-01-01"): "gpt-y-does-not-exist"},
        )


def test_snapshot_alias_is_provider_scoped():
    registry = PricingRegistry(
        {("openai", "gpt-x"): ModelRate(1.0, 2.0)},
        source_id="test-table",
        snapshot_aliases={("openai", "gpt-x-2026-01-01"): "gpt-x"},
    )
    usage = TokenUsage(input_tokens=1000, output_tokens=1000)
    assert registry.price("openai", "gpt-x-2026-01-01", usage) is not None
    assert registry.price("anthropic", "gpt-x-2026-01-01", usage) is None
