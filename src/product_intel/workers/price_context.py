"""Price Context Worker.

Section 2.1 / Section 4: Keepa for Amazon history; Rye/Channel3 live offers for
the cross-retailer spread; and — critically — an explicit "no history
available" flag for non-Amazon retailers rather than a fabricated trend.

Output is a structured :class:`PriceContext` the monitor and ``stage_handoff``
both consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..clients.base import ClientNotConfigured, PriceHistoryClient
from ..models import PriceHistory


@dataclass
class PriceContext:
    current_subunits: Optional[int]
    history: PriceHistory
    amazon_all_time_low_subunits: Optional[int] = None
    percentile: Optional[float] = None  # where current sits in known history (0=lowest)
    notes: list[str] = field(default_factory=list)

    @property
    def history_available(self) -> bool:
        return self.history.available

    def summary(self) -> str:
        if not self.history.available:
            note = self.history.note or "no external price history"
            return f"history: {note}"
        atl = self.amazon_all_time_low_subunits
        pct = f"{self.percentile:.0f}th pct" if self.percentile is not None else "n/a"
        atl_s = f"${atl / 100:,.2f}" if atl is not None else "n/a"
        return f"history: {len(self.history.points)} points, ATL {atl_s}, current at {pct}"


class PriceContextWorker:
    def __init__(self, price_history: PriceHistoryClient):
        self._history = price_history

    def get_context(
        self,
        *,
        url: str | None,
        rye_product_id: str | None = None,
        current_subunits: int | None = None,
    ) -> PriceContext:
        try:
            history = self._history.history(rye_product_id=rye_product_id, url=url)
        except ClientNotConfigured as e:
            return PriceContext(
                current_subunits=current_subunits,
                history=PriceHistory(source="keepa", available=False, note=str(e)),
                notes=["price-history client not configured; tracking will cold-start its own baseline"],
            )

        ctx = PriceContext(current_subunits=current_subunits, history=history)
        if history.available and history.points:
            ctx.amazon_all_time_low_subunits = history.amazon_low()
            if current_subunits is not None:
                ctx.percentile = _percentile(history, current_subunits)
        else:
            ctx.notes.append(
                "non-Amazon or empty history: monitor builds its own baseline from day one (honest cold-start)"
            )
        return ctx


def _percentile(history: PriceHistory, current_subunits: int) -> float:
    """Percentile rank of ``current`` within historical prices (0 = lowest)."""
    prices = sorted(p for _, p in history.points)
    if not prices:
        return 0.0
    below = sum(1 for p in prices if p < current_subunits)
    return 100.0 * below / len(prices)
