"""Client interfaces.

Every external dependency sits behind a ``Protocol`` so it is swappable —
explicitly called for in the design doc for the Keepa price-history
dependency ("Build the Price Context Worker against an interface so the Keepa
dependency is swappable"), and applied uniformly to discovery/normalization
too. Tests and offline/demo runs inject the in-memory fakes in ``fakes.py``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import PriceHistory, Product


class ClientNotConfigured(RuntimeError):
    """Raised when a client is used without the required API key.

    The workers catch this and degrade honestly (e.g. "no history available")
    rather than fabricating data.
    """


@runtime_checkable
class DiscoveryClient(Protocol):
    """Natural-language product search across merchants (e.g. Channel3)."""

    def search(self, query: str, limit: int = 10) -> list[Product]: ...


@runtime_checkable
class NormalizationClient(Protocol):
    """Merchant-URL -> normalized product record (e.g. Rye)."""

    def normalize(self, url: str) -> Product: ...


@runtime_checkable
class PriceHistoryClient(Protocol):
    """Historical price series for a product (e.g. Keepa, Amazon-only)."""

    def history(self, *, rye_product_id: str | None = None, url: str | None = None) -> PriceHistory: ...
