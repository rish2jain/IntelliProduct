"""Rye Product Data normalization client.

Pass any merchant URL, get back normalized product data: stable id, brand,
price in subunits, availability enum, purchasability flag (~91% reliability
across the top 450 US stores per the design doc). This is "most of the
normalization layer" — used to firm up Channel3 candidates and to re-verify a
live price at handoff time via a legitimate data API (not session scraping).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import Availability, Product
from .base import ClientNotConfigured, NormalizationClient


class RyeClient(NormalizationClient):
    def __init__(self, api_key: str | None, base_url: str, timeout: float = 20.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def normalize(self, url: str) -> Product:
        if not self._api_key:
            raise ClientNotConfigured("RYE_API_KEY is not set")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base_url}/v1/product",
                headers=headers,
                json={"url": url},
            )
            resp.raise_for_status()
            return self._parse(resp.json(), url)

    @staticmethod
    def _parse(payload: dict[str, Any], url: str) -> Product:
        p = payload.get("product", payload)
        price = p.get("price") or {}
        return Product(
            title=p.get("title", ""),
            url=p.get("url", url),
            merchant=p.get("merchant") or p.get("store", "") or "unknown",
            price_subunits=int(price.get("value") or price.get("price_subunits") or 0),
            currency=price.get("currency", "USD"),
            availability=_availability(p.get("availability")),
            purchasable=bool(p.get("isPurchasable", p.get("purchasable", False))),
            brand=p.get("brand"),
            rye_product_id=p.get("id"),
            attributes=p.get("attributes") or {},
        )


def _availability(value: Any) -> Availability:
    try:
        return Availability(str(value))
    except (ValueError, TypeError):
        return Availability.UNKNOWN
