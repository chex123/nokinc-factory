"""Published per-token list prices and model context ceilings.

Costs are list-price equivalents calculated from provider-reported usage, not
provider invoices. Update this rate card from the cited provider sources when
prices or published effective dates change. Provider-effective bounds are kept
separate from repository knowledge bounds; when a provider boundary is absent,
the knowledge bound prevents inventing a backdated price. No spend threshold is
enforced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field

from nokinc_factory.domain.review_base import Count, Identifier, ReviewModel
from nokinc_factory.ports.model import ModelUsage


@dataclass(frozen=True)
class ModelContextLimits:
    context_window_tokens: int
    max_input_tokens: int
    max_output_tokens: int


class ModelCostEstimate(ReviewModel):
    """Reproducible list-price estimate stored with model-run telemetry."""

    cost_nanodollars: Count
    price_card_id: Identifier
    source_url: str = Field(min_length=1, max_length=500)
    priced_on: date


@dataclass(frozen=True)
class _TokenRates:
    provider: str
    model: str
    price_card_id: str
    input_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal | None
    cache_write_usd_per_million: Decimal | None
    output_usd_per_million: Decimal
    known_from: date
    known_until: date | None
    source_url: str
    provider_effective_from: date | None
    provider_effective_until: date | None
    region: str | None = None
    long_context_threshold: int | None = None
    long_context_input_multiplier: Decimal = Decimal("1")
    long_context_output_multiplier: Decimal = Decimal("1")

    def applies(self, *, as_of: date, region: str) -> bool:
        """Prefer provider-effective bounds, falling back to repository knowledge dates."""
        applicable_from = self.provider_effective_from
        if applicable_from is None:
            applicable_from = self.known_from
        applicable_until = self.provider_effective_until
        if applicable_until is None:
            applicable_until = self.known_until
        return (
            applicable_from <= as_of
            and (applicable_until is None or as_of < applicable_until)
            and (self.region is None or self.region == region)
        )


_OPENAI_LUNA_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
_OPENAI_GPT6_LUNA_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6-luna"
_OPENAI_ASTRA_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6-astra"
_GOOGLE_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
_AWS_SOURCE = "https://aws.amazon.com/bedrock/pricing/"

_RATES = (
    _TokenRates(
        provider="openai",
        model="gpt-6-luna",
        price_card_id="openai-gpt-6-luna-2026-09",
        input_usd_per_million=Decimal("0.10"),
        cached_input_usd_per_million=Decimal("0.01"),
        cache_write_usd_per_million=Decimal("0.125"),
        output_usd_per_million=Decimal("0.50"),
        known_from=date(2026, 9, 28),
        known_until=None,
        source_url=_OPENAI_GPT6_LUNA_SOURCE,
        provider_effective_from=None,
        provider_effective_until=None,
        long_context_threshold=272_000,
        long_context_input_multiplier=Decimal("2"),
        long_context_output_multiplier=Decimal("1.5"),
    ),
    _TokenRates(
        provider="openai",
        model="gpt-5.6-luna",
        price_card_id="openai-2026-09",
        input_usd_per_million=Decimal("0.20"),
        cached_input_usd_per_million=Decimal("0.02"),
        cache_write_usd_per_million=Decimal("0.25"),
        output_usd_per_million=Decimal("1.20"),
        known_from=date(2026, 9, 27),
        known_until=None,
        source_url=_OPENAI_LUNA_SOURCE,
        provider_effective_from=date(2026, 9, 27),
        provider_effective_until=None,
        long_context_threshold=272_000,
        long_context_input_multiplier=Decimal("2"),
        long_context_output_multiplier=Decimal("1.5"),
    ),
    _TokenRates(
        provider="openai",
        model="gpt-6-astra",
        price_card_id="openai-2026-09",
        input_usd_per_million=Decimal("10"),
        cached_input_usd_per_million=Decimal("1"),
        cache_write_usd_per_million=Decimal("12.5"),
        output_usd_per_million=Decimal("50"),
        known_from=date(2026, 9, 27),
        known_until=None,
        source_url=_OPENAI_ASTRA_SOURCE,
        provider_effective_from=date(2026, 9, 27),
        provider_effective_until=None,
        long_context_threshold=272_000,
        long_context_input_multiplier=Decimal("2"),
        long_context_output_multiplier=Decimal("1.5"),
    ),
    _TokenRates(
        provider="google",
        model="gemini-3.8-flash",
        price_card_id="google-gemini-2026-09",
        input_usd_per_million=Decimal("0.75"),
        cached_input_usd_per_million=Decimal("0.075"),
        cache_write_usd_per_million=None,
        output_usd_per_million=Decimal("3.75"),
        known_from=date(2026, 9, 27),
        known_until=None,
        source_url=_GOOGLE_SOURCE,
        provider_effective_from=date(2026, 9, 24),
        provider_effective_until=date(2027, 1, 1),
    ),
    _TokenRates(
        provider="google",
        model="gemini-3.8-flash",
        price_card_id="google-gemini-2027-01",
        input_usd_per_million=Decimal("1.50"),
        cached_input_usd_per_million=Decimal("0.15"),
        cache_write_usd_per_million=None,
        output_usd_per_million=Decimal("7.50"),
        known_from=date(2026, 9, 27),
        known_until=None,
        source_url=_GOOGLE_SOURCE,
        provider_effective_from=date(2027, 1, 1),
        provider_effective_until=None,
    ),
    _TokenRates(
        provider="aws-bedrock",
        model="amazon.nova-pro-v1:0",
        price_card_id="aws-bedrock-us-east-1-2026-09",
        input_usd_per_million=Decimal("0.80"),
        cached_input_usd_per_million=Decimal("0.20"),
        cache_write_usd_per_million=Decimal("0"),
        output_usd_per_million=Decimal("3.20"),
        known_from=date(2026, 9, 27),
        known_until=None,
        source_url=_AWS_SOURCE,
        provider_effective_from=date(2026, 9, 1),
        provider_effective_until=None,
        region="us-east-1",
    ),
)

_CONTEXT_LIMITS = {
    ("openai", "gpt-6-luna"): ModelContextLimits(
        context_window_tokens=1_050_000,
        max_input_tokens=922_000,
        max_output_tokens=128_000,
    ),
    ("openai", "gpt-5.6-luna"): ModelContextLimits(
        context_window_tokens=1_050_000,
        max_input_tokens=922_000,
        max_output_tokens=128_000,
    ),
    ("openai", "gpt-6-astra"): ModelContextLimits(
        context_window_tokens=1_050_000,
        max_input_tokens=922_000,
        max_output_tokens=128_000,
    ),
    ("google", "gemini-3.8-flash"): ModelContextLimits(
        context_window_tokens=1_048_576,
        max_input_tokens=1_048_576,
        max_output_tokens=65_536,
    ),
    ("aws-bedrock", "amazon.nova-pro-v1:0"): ModelContextLimits(
        context_window_tokens=300_000,
        max_input_tokens=290_000,
        max_output_tokens=10_000,
    ),
}


def model_context_limits(*, provider: str, model: str) -> ModelContextLimits | None:
    """Return the documented maximum for one exact configured model ID."""
    return _CONTEXT_LIMITS.get((provider, model))


def estimate_model_cost(
    *,
    provider: str,
    model: str,
    usage: ModelUsage,
    as_of: date | None = None,
    region: str = "us-east-1",
) -> ModelCostEstimate | None:
    """Calculate provider-reported usage at published list rates, with no budget cap."""
    pricing_date = as_of or datetime.now(UTC).date()
    rates = next((
        item for item in _RATES
        if item.provider == provider and item.model == model
        and item.applies(as_of=pricing_date, region=region)
    ), None)
    if rates is None:
        return None
    if usage.cache_write_input_tokens and rates.cache_write_usd_per_million is None:
        return None

    total_input_tokens = (
        usage.input_tokens + usage.cached_input_tokens + usage.cache_write_input_tokens
    )
    input_multiplier = Decimal("1")
    output_multiplier = Decimal("1")
    if (
        rates.long_context_threshold is not None
        and total_input_tokens > rates.long_context_threshold
    ):
        input_multiplier = rates.long_context_input_multiplier
        output_multiplier = rates.long_context_output_multiplier

    cost = (
        Decimal(usage.input_tokens) * rates.input_usd_per_million * input_multiplier
        + Decimal(usage.cached_input_tokens)
        * (rates.cached_input_usd_per_million or Decimal("0"))
        * input_multiplier
        + Decimal(usage.cache_write_input_tokens)
        * (rates.cache_write_usd_per_million or Decimal("0"))
        * input_multiplier
        + Decimal(usage.output_tokens)
        * rates.output_usd_per_million
        * output_multiplier
    ) * Decimal("1000")

    return ModelCostEstimate(
        cost_nanodollars=int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        price_card_id=rates.price_card_id,
        source_url=rates.source_url,
        priced_on=pricing_date,
    )


__all__ = [
    "ModelContextLimits",
    "ModelCostEstimate",
    "estimate_model_cost",
    "model_context_limits",
]