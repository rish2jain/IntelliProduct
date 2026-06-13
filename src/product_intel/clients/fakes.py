"""In-memory fake clients for tests, offline demos, and the seed CLI.

These satisfy the same Protocols as the real clients so workers and the MCP
server can run end-to-end with zero network access or API keys. Selected at
runtime when ``PRODUCT_INTEL_FAKE=1`` (see ``container.py``).
"""

from __future__ import annotations

from ..models import Availability, PriceHistory, Product
from .base import DiscoveryClient, NormalizationClient, PriceHistoryClient

_SEED: list[Product] = [
    Product(
        title="Sony a7 IV (body only)",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        merchant="B&H",
        price_subunits=239800,
        availability=Availability.IN_STOCK,
        purchasable=True,
        brand="Sony",
        rye_product_id="rye_sony_a7iv_body",
        channel3_id="c3_sony_a7iv",
        attributes={"sensor": "33MP full-frame", "kit": "body-only"},
    ),
    Product(
        title="Sony a7 IV with 28-70mm kit lens",
        url="https://www.amazon.com/dp/B09JZ9JTLB",
        merchant="Amazon",
        price_subunits=259900,
        availability=Availability.IN_STOCK,
        purchasable=True,
        brand="Sony",
        rye_product_id="rye_sony_a7iv_kit",
        channel3_id="c3_sony_a7iv_kit",
        attributes={"sensor": "33MP full-frame", "kit": "with-28-70mm"},
    ),
    Product(
        title="Sony a7 IV (body only) — used, excellent",
        url="https://www.adorama.com/used/sony-a7-iv",
        merchant="Adorama (used)",
        price_subunits=199500,
        availability=Availability.IN_STOCK,
        purchasable=True,
        brand="Sony",
        rye_product_id="rye_sony_a7iv_used",
        attributes={"condition": "used-excellent", "kit": "body-only"},
    ),
]


class FakeDiscoveryClient(DiscoveryClient):
    def __init__(self, seed: list[Product] | None = None):
        self._seed = seed if seed is not None else _SEED

    def search(self, query: str, limit: int = 10) -> list[Product]:
        q = query.lower()
        hits = [p for p in self._seed if any(t in p.title.lower() for t in q.split())]
        return (hits or self._seed)[:limit]


class FakeNormalizationClient(NormalizationClient):
    def __init__(self, seed: list[Product] | None = None):
        self._by_url = {p.url: p for p in (seed if seed is not None else _SEED)}

    def normalize(self, url: str) -> Product:
        if url in self._by_url:
            return self._by_url[url]
        return Product(
            title="Unknown product",
            url=url,
            merchant="unknown",
            price_subunits=0,
            availability=Availability.UNKNOWN,
        )


class FakeWebSearchClient:
    """Deterministic Layer 3 search hits for offline runs and tests."""

    def search(self, query: str, limit: int = 5) -> list[dict]:
        q = query.lower()
        if "successor" in q and "sony" in q:
            return [
                {
                    "title": "Sony a7 V announcement rumored for fall",
                    "url": "https://example.com/sony-a7v-rumor",
                    "snippet": "successor to the a7 IV expected; current-gen discounts likely",
                }
            ][:limit]
        if "firmware" in q:
            return [
                {
                    "title": "Sony firmware 3.0 addresses battery drain",
                    "url": "https://example.com/sony-fw-3-0",
                    "snippet": "firmware fix for the battery drain reported by long-term owners",
                }
            ][:limit]
        return []


class FakePriceHistoryClient(PriceHistoryClient):
    """Returns synthetic Amazon history; non-Amazon -> unavailable."""

    def history(self, *, rye_product_id: str | None = None, url: str | None = None) -> PriceHistory:
        if not url or "amazon." not in url.lower():
            return PriceHistory(
                source="fake-keepa",
                available=False,
                note="no history available (non-Amazon retailer)",
            )
        base = 1293840000
        day = 86400
        # A descending-then-stable series with a recent dip to an all-time low.
        prices = [259900, 254900, 249900, 252900, 247900, 244900, 239900]
        points = [(base + i * day, c) for i, c in enumerate(prices)]
        return PriceHistory(source="fake-keepa", available=True, points=points)
