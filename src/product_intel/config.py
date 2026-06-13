"""Environment-driven configuration.

No secrets in code. API keys come from the environment; absence of a key
puts the corresponding client into an explicit *not-configured* state rather
than failing silently or fabricating data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_db_path() -> Path:
    # Default under the user's home so launchd-run daemons and interactive MCP
    # sessions share one SQLite file. Override with PRODUCT_INTEL_DB.
    root = Path(os.environ.get("PRODUCT_INTEL_HOME", str(Path.home() / ".product-intel")))
    return root / "product-intel.sqlite3"


@dataclass(frozen=True)
class Config:
    db_path: Path

    # Discovery / normalization
    channel3_api_key: str | None
    channel3_base_url: str
    rye_api_key: str | None
    rye_base_url: str

    # Price history (swappable; Keepa is the default Amazon source)
    keepa_api_key: str | None
    keepa_base_url: str

    # Alerting
    ntfy_topic: str | None
    ntfy_base_url: str
    pushover_token: str | None
    pushover_user: str | None

    # Monitor loop
    monitor_interval_seconds: int

    # Phase B: effective-price layer — manual offer YAML, refreshed weekly
    offers_file: Path

    # Phase C: review synthesis — local inference (Ollama on the Mac Studio)
    ollama_base_url: str
    ollama_model: str
    ollama_embed_model: str
    # Review sources (each optional; absence -> source skipped with a coverage note)
    reddit_client_id: str | None
    reddit_client_secret: str | None
    reddit_user_agent: str
    youtube_api_key: str | None
    bestbuy_api_key: str | None

    # Phase D: Layer 3 market context
    brave_api_key: str | None
    market_scan_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        db_path = Path(os.environ.get("PRODUCT_INTEL_DB", str(_default_db_path())))
        home = db_path.parent
        return cls(
            db_path=db_path,
            channel3_api_key=os.environ.get("CHANNEL3_API_KEY"),
            channel3_base_url=os.environ.get("CHANNEL3_BASE_URL", "https://api.channel3.dev"),
            rye_api_key=os.environ.get("RYE_API_KEY"),
            rye_base_url=os.environ.get("RYE_BASE_URL", "https://api.rye.com"),
            keepa_api_key=os.environ.get("KEEPA_API_KEY"),
            keepa_base_url=os.environ.get("KEEPA_BASE_URL", "https://api.keepa.com"),
            ntfy_topic=os.environ.get("NTFY_TOPIC"),
            ntfy_base_url=os.environ.get("NTFY_BASE_URL", "https://ntfy.sh"),
            pushover_token=os.environ.get("PUSHOVER_TOKEN"),
            pushover_user=os.environ.get("PUSHOVER_USER"),
            monitor_interval_seconds=int(os.environ.get("MONITOR_INTERVAL_SECONDS", "21600")),
            offers_file=Path(os.environ.get("OFFERS_FILE", str(home / "offers.yaml"))),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:30b"),
            ollama_embed_model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            reddit_client_id=os.environ.get("REDDIT_CLIENT_ID"),
            reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET"),
            reddit_user_agent=os.environ.get("REDDIT_USER_AGENT", "product-intel/0.2"),
            youtube_api_key=os.environ.get("YOUTUBE_API_KEY"),
            bestbuy_api_key=os.environ.get("BESTBUY_API_KEY"),
            brave_api_key=os.environ.get("BRAVE_API_KEY"),
            market_scan_interval_seconds=int(
                os.environ.get("MARKET_SCAN_INTERVAL_SECONDS", "604800")  # weekly
            ),
        )
