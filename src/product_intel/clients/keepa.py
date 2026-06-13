"""Keepa price-history client (Amazon-only, canonical source).

Paid + token-metered (~€49/mo). Subscribe only during active tracking windows
of a product above ~$500; the interface (``PriceHistoryClient``) keeps the
dependency swappable. For any non-Amazon URL this returns an *unavailable*
history with a note, so the Price Context Worker can flag "no history
available" rather than fabricating a trend.

Keepa encodes Amazon prices in cents already and timestamps as "Keepa minutes"
(minutes since 2011-01-01 UTC); ``_decode`` converts both to our conventions.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import PriceHistory
from .base import ClientNotConfigured, PriceHistoryClient

# Keepa epoch: 2011-01-01 00:00 UTC, in unix seconds.
_KEEPA_EPOCH_S = 1293840000


def _is_amazon(url: str | None) -> bool:
    return bool(url) and "amazon." in url.lower()


def _asin_from_url(url: str) -> str | None:
    import re

    m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", url)
    return m.group(1) if m else None


class KeepaClient(PriceHistoryClient):
    def __init__(self, api_key: str | None, base_url: str, domain: int = 1, timeout: float = 30.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._domain = domain  # 1 = amazon.com
        self._timeout = timeout

    def history(self, *, rye_product_id: str | None = None, url: str | None = None) -> PriceHistory:
        if not _is_amazon(url):
            return PriceHistory(
                source="keepa",
                available=False,
                note="no history available (Keepa is Amazon-only; this is a non-Amazon retailer)",
            )
        if not self._api_key:
            raise ClientNotConfigured("KEEPA_API_KEY is not set")
        asin = _asin_from_url(url or "")
        if not asin:
            return PriceHistory(source="keepa", available=False, note="could not extract ASIN from URL")

        params = {"key": self._api_key, "domain": self._domain, "asin": asin, "history": 1}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(f"{self._base_url}/product", params=params)
            resp.raise_for_status()
            return self._parse(resp.json())

    @staticmethod
    def _parse(payload: dict[str, Any]) -> PriceHistory:
        products = payload.get("products") or []
        if not products:
            return PriceHistory(source="keepa", available=False, note="no product returned")
        csv = (products[0].get("csv") or [])
        # csv[0] is the AMAZON price series: [keepa_minute, price_cents, ...].
        series = csv[0] if csv and csv[0] else []
        points = _decode(series)
        if not points:
            return PriceHistory(source="keepa", available=False, note="empty price series")
        return PriceHistory(source="keepa", available=True, points=points)


def _decode(series: list[int]) -> list[tuple[float, int]]:
    points: list[tuple[float, int]] = []
    for i in range(0, len(series) - 1, 2):
        minute, cents = series[i], series[i + 1]
        if cents is None or cents < 0:  # -1 means "no price" in Keepa
            continue
        points.append((_KEEPA_EPOCH_S + minute * 60, int(cents)))
    return points
