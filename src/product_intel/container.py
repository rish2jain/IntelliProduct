"""Dependency wiring.

Selects real HTTP clients or in-memory fakes. Set ``PRODUCT_INTEL_FAKE=1`` to
run the whole stack offline (tests, demos, and first-run exploration before any
API keys exist). Otherwise real clients are used; a missing key surfaces as
:class:`ClientNotConfigured` at call time, handled gracefully by the workers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .clients.base import DiscoveryClient, NormalizationClient, PriceHistoryClient
from .config import Config


def _use_fakes() -> bool:
    return os.environ.get("PRODUCT_INTEL_FAKE", "").lower() in {"1", "true", "yes"}


@dataclass
class Clients:
    discovery: DiscoveryClient
    normalizer: NormalizationClient
    price_history: PriceHistoryClient


def build_clients(config: Config) -> Clients:
    if _use_fakes():
        from .clients.fakes import (
            FakeDiscoveryClient,
            FakeNormalizationClient,
            FakePriceHistoryClient,
        )

        return Clients(
            discovery=FakeDiscoveryClient(),
            normalizer=FakeNormalizationClient(),
            price_history=FakePriceHistoryClient(),
        )

    from .clients.channel3 import Channel3Client
    from .clients.keepa import KeepaClient
    from .clients.rye import RyeClient

    return Clients(
        discovery=Channel3Client(config.channel3_api_key, config.channel3_base_url),
        normalizer=RyeClient(config.rye_api_key, config.rye_base_url),
        price_history=KeepaClient(config.keepa_api_key, config.keepa_base_url),
    )
