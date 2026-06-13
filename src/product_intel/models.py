"""Core domain models.

Deliberately stdlib-only (dataclasses + enums) so the core — models, DB,
alert rules, workers — is importable and testable without ``fastmcp``,
``httpx``, or any network access. Prices are carried in integer *subunits*
(cents) end-to-end, matching the Rye Product Data API convention and avoiding
float rounding in money math.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


def now_ts() -> float:
    """Unix epoch seconds. Centralized so tests can monkeypatch if needed."""
    return time.time()


class Availability(str, Enum):
    """Normalized availability enum (mirrors Rye's availability enum).

    Layer 3 discontinuation signals key off transitions into
    ``OUT_OF_STOCK`` / ``BACKORDER`` across retailers.
    """

    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    BACKORDER = "backorder"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class AlertLayer(str, Enum):
    THRESHOLD = "layer1_threshold"
    RELATIVE_VALUE = "layer2_relative_value"
    MARKET_CONTEXT = "layer3_market_context"  # Phase D; reserved here.


@dataclass(slots=True)
class Product:
    """A normalized product/offer record.

    This is the shape the *Discovery + Spec Worker* emits: Channel3 supplies
    the candidate + merchant URL, Rye normalizes it into a stable id, brand,
    price-in-subunits, availability enum, and purchasability flag.
    """

    title: str
    url: str
    merchant: str
    price_subunits: int
    currency: str = "USD"
    availability: Availability = Availability.UNKNOWN
    purchasable: bool = False
    brand: Optional[str] = None
    rye_product_id: Optional[str] = None
    channel3_id: Optional[str] = None
    # Deep technical attributes (sensor readout, IP rating, ...). In Phase A
    # these arrive only as whatever the normalizer returns; expert-review
    # enrichment is Phase C.
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def price(self) -> float:
        return self.price_subunits / 100.0

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["availability"] = self.availability.value
        d["attributes"] = d.get("attributes") or {}
        return d


@dataclass(slots=True)
class TrackedProduct:
    """A product under continuous monitoring."""

    label: str
    merchant: str
    url: str
    target_price_subunits: int
    currency: str = "USD"
    rye_product_id: Optional[str] = None
    active: bool = True
    # Category groups tracked products for Layer 3 competitor-move detection
    # (e.g. two tracked camera bodies in "cameras" watch each other's drops).
    category: Optional[str] = None
    id: Optional[int] = None
    created_at: float = field(default_factory=now_ts)
    # Cached last price each layer alerted at, for crossing/dedup logic.
    last_alert_price_subunits: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class PriceObservation:
    """A single price reading recorded by the monitor."""

    tracked_product_id: int
    price_subunits: int
    currency: str
    availability: Availability
    source: str
    observed_at: float = field(default_factory=now_ts)
    id: Optional[int] = None


@dataclass(slots=True)
class PriceHistory:
    """External price history (e.g. Keepa) for a product.

    ``points`` is a list of (epoch_seconds, price_subunits). ``available`` is
    explicit: for non-Amazon retailers there is no external history and we
    surface an honest "no history available" rather than fabricating a trend.
    """

    source: str
    available: bool
    points: list[tuple[float, int]] = field(default_factory=list)
    note: Optional[str] = None

    def amazon_low(self) -> Optional[int]:
        if not self.points:
            return None
        return min(p for _, p in self.points)


@dataclass(slots=True)
class Alert:
    tracked_product_id: int
    layer: AlertLayer
    rule: str
    message: str
    price_subunits: int
    deep_link: str
    created_at: float = field(default_factory=now_ts)
    delivered: bool = False
    id: Optional[int] = None
    payload: dict[str, Any] = field(default_factory=dict)
