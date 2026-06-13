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
from .clients.websearch import WebSearchClient
from .config import Config
from .reviews.embeddings import Embedder
from .reviews.llm import LocalLLM
from .reviews.sources import ReviewSource


def _use_fakes() -> bool:
    return os.environ.get("PRODUCT_INTEL_FAKE", "").lower() in {"1", "true", "yes"}


@dataclass
class Clients:
    discovery: DiscoveryClient
    normalizer: NormalizationClient
    price_history: PriceHistoryClient
    web_search: WebSearchClient | None
    review_sources: list[ReviewSource]
    llm: LocalLLM
    embedder: Embedder


def build_clients(config: Config) -> Clients:
    if _use_fakes():
        from .clients.fakes import (
            FakeDiscoveryClient,
            FakeNormalizationClient,
            FakePriceHistoryClient,
            FakeWebSearchClient,
        )
        from .reviews.embeddings import HashEmbedder
        from .reviews.fakes import fake_sources
        from .reviews.llm import HeuristicLLM

        return Clients(
            discovery=FakeDiscoveryClient(),
            normalizer=FakeNormalizationClient(),
            price_history=FakePriceHistoryClient(),
            web_search=FakeWebSearchClient(),
            review_sources=fake_sources(),
            llm=HeuristicLLM(),
            embedder=HashEmbedder(),
        )

    from .clients.channel3 import Channel3Client
    from .clients.keepa import KeepaClient
    from .clients.rye import RyeClient
    from .clients.websearch import BraveSearchClient
    from .reviews.embeddings import OllamaEmbedder
    from .reviews.llm import OllamaLLM
    from .reviews.sources import (
        BestBuySource,
        ExpertSource,
        RedditSource,
        YouTubeSource,
    )

    return Clients(
        discovery=Channel3Client(config.channel3_api_key, config.channel3_base_url),
        normalizer=RyeClient(config.rye_api_key, config.rye_base_url),
        price_history=KeepaClient(config.keepa_api_key, config.keepa_base_url),
        web_search=BraveSearchClient(config.brave_api_key),
        review_sources=[
            ExpertSource(),
            BestBuySource(config.bestbuy_api_key),
            RedditSource(
                config.reddit_client_id, config.reddit_client_secret, config.reddit_user_agent
            ),
            YouTubeSource(config.youtube_api_key),
        ],
        llm=OllamaLLM(config.ollama_base_url, config.ollama_model),
        embedder=OllamaEmbedder(config.ollama_base_url, config.ollama_embed_model),
    )
