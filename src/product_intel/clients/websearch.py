"""Web search client for Layer 3's weekly context pass (Section 5).

Used for successor-product announcements and firmware/recall checks against
open contradiction records. Brave Search is the default key-based backend;
the fake (clients/fakes.py) seeds deterministic hits for offline runs.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .base import ClientNotConfigured


@runtime_checkable
class WebSearchClient(Protocol):
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Returns [{"title", "url", "snippet"}, ...]."""
        ...


class BraveSearchClient(WebSearchClient):
    def __init__(self, api_key: str | None, timeout: float = 15.0):
        self._key = api_key
        self._timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self._key:
            raise ClientNotConfigured("BRAVE_API_KEY not set")
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": self._key},
                params={"q": query, "count": limit},
            )
            resp.raise_for_status()
            results = resp.json().get("web", {}).get("results", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
                for r in results[:limit]
            ]
