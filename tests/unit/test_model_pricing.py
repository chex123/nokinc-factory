from datetime import date

from nokinc_factory.application.model_pricing import (
    estimate_model_cost,
    model_context_limits,
)
from nokinc_factory.ports.model import ModelUsage


def test_luna_list_price_uses_provider_reported_cached_and_uncached_tokens() -> None:
    estimate = estimate_model_cost(
        provider="openai",
        model="gpt-5.6-luna",
        usage=ModelUsage(
            input_tokens=6_000,
            cached_input_tokens=4_000,
            output_tokens=500,
        ),
        as_of=date(2026, 9, 27),
    )

    assert estimate is not None
    assert estimate.cost_nanodollars == 1_880_000
    assert estimate.price_card_id == "openai-2026-09"


def test_astra_applies_published_long_context_multiplier() -> None:
    estimate = estimate_model_cost(
        provider="openai",
        model="gpt-6-astra",
        usage=ModelUsage(input_tokens=273_000, output_tokens=1_000),
        as_of=date(2026, 9, 27),
    )

    assert estimate is not None
    assert estimate.cost_nanodollars == 5_535_000_000


def test_gemini_rate_card_uses_the_effective_date() -> None:
    usage = ModelUsage(input_tokens=1_000, output_tokens=1_000)

    current = estimate_model_cost(
        provider="google",
        model="gemini-3.8-flash",
        usage=usage,
        as_of=date(2026, 9, 27),
    )
    next_rate = estimate_model_cost(
        provider="google",
        model="gemini-3.8-flash",
        usage=usage,
        as_of=date(2027, 1, 1),
    )

    assert current is not None and current.cost_nanodollars == 4_500_000
    assert next_rate is not None and next_rate.cost_nanodollars == 9_000_000


def test_unknown_model_has_no_fabricated_list_price() -> None:
    assert estimate_model_cost(
        provider="openai",
        model="unpriced-model",
        usage=ModelUsage(input_tokens=100, output_tokens=100),
        as_of=date(2026, 9, 27),
    ) is None


def test_unpriced_google_cache_write_returns_no_cost_estimate() -> None:
    estimate = estimate_model_cost(
        provider="google",
        model="gemini-3.8-flash",
        usage=ModelUsage(
            input_tokens=100,
            cache_write_input_tokens=1,
            output_tokens=10,
        ),
        as_of=date(2026, 9, 27),
    )

    assert estimate is None


def test_nova_cost_is_unknown_outside_the_published_region() -> None:
    estimate = estimate_model_cost(
        provider="aws-bedrock",
        model="amazon.nova-pro-v1:0",
        usage=ModelUsage(input_tokens=100, output_tokens=10),
        as_of=date(2026, 9, 27),
        region="eu-west-1",
    )

    assert estimate is None


def test_model_context_limits_reflect_each_configured_provider_maximum() -> None:
    luna = model_context_limits(provider="openai", model="gpt-5.6-luna")
    gemini = model_context_limits(provider="google", model="gemini-3.8-flash")
    nova = model_context_limits(provider="aws-bedrock", model="amazon.nova-pro-v1:0")

    assert luna.max_input_tokens == 922_000
    assert luna.context_window_tokens == 1_050_000
    assert gemini.max_input_tokens == 1_048_576
    assert nova.max_input_tokens == 290_000
    assert nova.context_window_tokens == 300_000