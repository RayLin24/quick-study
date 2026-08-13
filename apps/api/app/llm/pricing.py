"""Model prices as dated snapshots.

Prices are recorded in the code rather than fetched, and each entry carries the date it was
copied and the page it came from. Two reasons: a run's recorded cost has to stay
reproducible after the provider changes its rate card, and DeepSeek's own pricing page
currently warns that an increase is planned. When that lands, the fix is a one-line edit
here plus a new ``captured_on`` -- and every historical run keeps the cost it was actually
charged.

Input tokens are billed in two buckets. A prompt prefix the provider has recently seen is
served from its cache at a small fraction of the normal rate, which for a tutorial run
matters: every chapter call repeats the same system instructions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from app.llm.errors import UnknownModelPrice
from app.llm.usage import TokenUsage

#: Provider rate cards are quoted per million tokens.
TOKENS_PER_PRICE_UNIT: Final = Decimal(1_000_000)

#: Matches ``app.db.types.Money`` (NUMERIC(12, 6)), so a stored cost is never re-rounded.
COST_QUANTUM: Final = Decimal("0.000001")

_DEEPSEEK_PRICING_PAGE: Final = "https://api-docs.deepseek.com/quick_start/pricing/"
_DEEPSEEK_SNAPSHOT_DATE: Final = date(2026, 8, 13)


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    """What one model cost per million tokens on ``captured_on``."""

    model: str
    input_cache_miss_per_million: Decimal
    input_cache_hit_per_million: Decimal
    output_per_million: Decimal
    captured_on: date
    source: str
    currency: str = "USD"

    def cost_for(self, usage: TokenUsage) -> Decimal:
        """Return the cost of ``usage``, quantised to what the database stores."""
        total = (
            usage.billable_input_tokens * self.input_cache_miss_per_million
            + usage.cached_input_tokens * self.input_cache_hit_per_million
            + usage.output_tokens * self.output_per_million
        ) / TOKENS_PER_PRICE_UNIT
        return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


DEEPSEEK_V4_FLASH_PRICE: Final = PriceSnapshot(
    model="deepseek-v4-flash",
    input_cache_miss_per_million=Decimal("0.14"),
    input_cache_hit_per_million=Decimal("0.0028"),
    output_per_million=Decimal("0.28"),
    captured_on=_DEEPSEEK_SNAPSHOT_DATE,
    source=_DEEPSEEK_PRICING_PAGE,
)

DEEPSEEK_V4_PRO_PRICE: Final = PriceSnapshot(
    model="deepseek-v4-pro",
    input_cache_miss_per_million=Decimal("0.435"),
    input_cache_hit_per_million=Decimal("0.003625"),
    output_per_million=Decimal("0.87"),
    captured_on=_DEEPSEEK_SNAPSHOT_DATE,
    source=_DEEPSEEK_PRICING_PAGE,
)

#: ``deepseek-chat`` and ``deepseek-reasoner`` are compatibility aliases for the
#: non-thinking and thinking modes of the same billed model, so they share its rate card.
PRICE_BOOK: Final[Mapping[str, PriceSnapshot]] = {
    "deepseek-v4-flash": DEEPSEEK_V4_FLASH_PRICE,
    "deepseek-v4-pro": DEEPSEEK_V4_PRO_PRICE,
    "deepseek-chat": DEEPSEEK_V4_FLASH_PRICE,
    "deepseek-reasoner": DEEPSEEK_V4_FLASH_PRICE,
}


def resolve_price(model: str) -> PriceSnapshot:
    """Return the recorded price for ``model``, refusing to guess.

    An unpriced model would silently report a zero-cost run, which is the one accounting
    error nobody notices until the invoice arrives.
    """
    price = PRICE_BOOK.get(model)
    if price is None:
        raise UnknownModelPrice(
            f"no price snapshot for {model!r}; add one to app.llm.pricing.PRICE_BOOK"
        )
    return price
