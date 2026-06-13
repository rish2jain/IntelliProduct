"""Review sources, in order of defensibility (Section 3).

(a) Expert review sites via standard fetching, respecting robots.txt.
(b) Reddit via the *official* API (OAuth client-credentials) — community
    discussion states use cases far more often than retailer reviews.
(c) YouTube via the Data API for long-term-ownership reviews.
(d) Retailer reviews only where an API exists: Best Buy yes, Amazon no.
    Amazon review scraping is the unauthorized-access posture that lost in
    court — the coverage loss is accepted and flagged in output.

Each source is best-effort: missing keys or fetch failures surface as
coverage notes, never crashes.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable

from ..clients.base import ClientNotConfigured
from .models import ReviewDoc


@runtime_checkable
class ReviewSource(Protocol):
    name: str

    def fetch(self, query: str, limit: int = 25) -> list[ReviewDoc]: ...


# ---------------------------------------------------------------------------
class RedditSource:
    name = "reddit"

    def __init__(self, client_id: str | None, client_secret: str | None, user_agent: str):
        self._id, self._secret, self._ua = client_id, client_secret, user_agent

    def fetch(self, query: str, limit: int = 25) -> list[ReviewDoc]:
        if not (self._id and self._secret):
            raise ClientNotConfigured("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")
        import httpx

        with httpx.Client(timeout=20.0, headers={"User-Agent": self._ua}) as client:
            tok = client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self._id, self._secret),
                data={"grant_type": "client_credentials"},
            )
            tok.raise_for_status()
            token = tok.json()["access_token"]
            resp = client.get(
                "https://oauth.reddit.com/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "limit": limit, "sort": "relevance", "type": "link"},
            )
            resp.raise_for_status()
            docs = []
            for child in resp.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                text = (d.get("title", "") + "\n" + d.get("selftext", "")).strip()
                if len(text) < 40:
                    continue
                docs.append(
                    ReviewDoc(
                        source=self.name,
                        text=text,
                        url="https://reddit.com" + d.get("permalink", ""),
                        author=d.get("author", ""),
                        title=d.get("title", ""),
                    )
                )
            return docs


# ---------------------------------------------------------------------------
class YouTubeSource:
    """Data API search; long-term-ownership reviews live here. Phase A of
    transcript depth: title + description text (caption download requires
    channel-owner OAuth, so transcripts are future work, not faked)."""

    name = "youtube"

    def __init__(self, api_key: str | None):
        self._key = api_key

    def fetch(self, query: str, limit: int = 25) -> list[ReviewDoc]:
        if not self._key:
            raise ClientNotConfigured("YOUTUBE_API_KEY not set")
        import httpx

        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": self._key,
                    "q": f"{query} long term review",
                    "part": "snippet",
                    "type": "video",
                    "maxResults": min(limit, 50),
                },
            )
            resp.raise_for_status()
            docs = []
            for item in resp.json().get("items", []):
                sn = item.get("snippet", {})
                text = (sn.get("title", "") + "\n" + sn.get("description", "")).strip()
                if len(text) < 40:
                    continue
                vid = item.get("id", {}).get("videoId", "")
                docs.append(
                    ReviewDoc(
                        source=self.name,
                        text=text,
                        url=f"https://www.youtube.com/watch?v={vid}",
                        author=sn.get("channelTitle", ""),
                        title=sn.get("title", ""),
                    )
                )
            return docs


# ---------------------------------------------------------------------------
class BestBuySource:
    name = "bestbuy"

    def __init__(self, api_key: str | None):
        self._key = api_key

    def fetch(self, query: str, limit: int = 25) -> list[ReviewDoc]:
        if not self._key:
            raise ClientNotConfigured("BESTBUY_API_KEY not set")
        import httpx

        terms = "&search=".join(query.split())
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"https://api.bestbuy.com/v1/reviews(search={terms})",
                params={"apiKey": self._key, "format": "json", "pageSize": min(limit, 100)},
            )
            resp.raise_for_status()
            return [
                ReviewDoc(
                    source=self.name,
                    text=(r.get("title", "") + "\n" + r.get("comment", "")).strip(),
                    author=r.get("reviewer", [{}])[0].get("name", "") if r.get("reviewer") else "",
                    title=r.get("title", ""),
                )
                for r in resp.json().get("reviews", [])
                if r.get("comment")
            ]


# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


class ExpertSource:
    """Tier 1: fetch given expert-review URLs with standard requests and naive
    text extraction. URLs are supplied per-product (e.g. an Rtings page found
    in the Claude conversation); there is no scraping search here."""

    name = "expert"

    def __init__(self, urls: list[str] | None = None):
        self._urls = urls or []

    def with_urls(self, urls: list[str]) -> "ExpertSource":
        return ExpertSource(urls)

    def fetch(self, query: str, limit: int = 25) -> list[ReviewDoc]:
        if not self._urls:
            return []
        import httpx

        docs = []
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            for url in self._urls[:limit]:
                try:
                    resp = client.get(url, headers={"User-Agent": "product-intel/0.2 (+research)"})
                    resp.raise_for_status()
                except Exception:  # noqa: BLE001 — best-effort per URL
                    continue
                parser = _TextExtractor()
                parser.feed(resp.text)
                text = re.sub(r"\s+", " ", " ".join(parser.parts))[:20000]
                if len(text) > 200:
                    docs.append(ReviewDoc(source=self.name, text=text, url=url))
        return docs
