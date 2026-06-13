"""Channel3 discovery client.

Natural-language product search across many merchants, returning normalized
candidates with live offers and merchant URLs (1,000 free searches/month per
the design doc). This is the data layer for the Discovery + Spec Worker.

The exact request/response shape is isolated to ``_parse`` so adapting to API
changes is a one-function edit. Without ``CHANNEL3_API_KEY`` set, ``search``
raises :class:`ClientNotConfigured`.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import Availability, Product
from .base import ClientNotConfigured, DiscoveryClient


class Channel3Client(DiscoveryClient):
    def __init__(self, api_key: str | None, base_url: str, timeout: float = 20.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[Product]:
        if not self._api_key:
            raise ClientNotConfigured("CHANNEL3_API_KEY is not set")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base_url}/v0/search",
                headers=headers,
                json={"query": query, "limit": limit},
            )
            resp.raise_for_status()
            return self._parse(resp.json(), limit)

    @staticmethod
    def _parse(payload: dict[str, Any], limit: int) -> list[Product]:
        items = payload.get("products") or payload.get("results") or []
        out: list[Product] = []
        for it in items[:limit]:
            price = it.get("price") or {}
            out.append(
                Product(
                    title=it.get("title", ""),
                    url=it.get("url", ""),
                    merchant=it.get("merchant") or it.get("brand", "") or "unknown",
                    price_subunits=int(price.get("price_subunits") or price.get("amount") or 0),
                    currency=price.get("currency", "USD"),
                    availability=_availability(it.get("availability")),
                    purchasable=bool(it.get("purchasable", False)),
                    brand=it.get("brand"),
                    channel3_id=it.get("id"),
                    attributes=it.get("attributes") or {},
                )
            )
        return out


def _availability(value: Any) -> Availability:
    try:
        return Availability(str(value))
    except (ValueError, TypeError):
        return Availability.UNKNOWN
