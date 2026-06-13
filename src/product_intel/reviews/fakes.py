"""Seeded fake review sources for offline runs and tests.

The seed corpus is crafted to exercise the whole pipeline: distinct use-case
segments (parent/indoor/sports vs traveler/outdoor vs professional/portrait),
stated ownership durations, and a deliberate battery contradiction between a
long-term Reddit owner and other sources.
"""

from __future__ import annotations

from .models import ReviewDoc

_REDDIT = [
    ReviewDoc(
        source="reddit",
        text=(
            "I shoot my kid's indoor volleyball games every weekend. The autofocus is "
            "fast and reliable for tracking players, but after 8 months the battery "
            "drains noticeably faster than when it was new."
        ),
        url="https://reddit.com/r/sonyalpha/post1",
        author="gym_dad",
        title="8 months with the a7 IV for indoor sports",
    ),
    ReviewDoc(
        source="reddit",
        text=(
            "Been using it for 1 year of travel and hiking. Battery life is excellent "
            "on long trips, and the weight is fine for backpacking with one lens."
        ),
        url="https://reddit.com/r/sonyalpha/post2",
        author="trail_shooter",
        title="One year of travel with the a7 IV",
    ),
    ReviewDoc(
        source="reddit",
        text=(
            "Hobbyist here, mostly landscape on weekends outdoors. The screen is sharp "
            "and the autofocus is accurate for static scenes."
        ),
        url="https://reddit.com/r/sonyalpha/post3",
        author="weekend_hiker",
        title="Casual landscape impressions",
    ),
]

_YOUTUBE = [
    ReviewDoc(
        source="youtube",
        text=(
            "After 1 year shooting weddings with this camera, the autofocus is excellent "
            "for portraits and the battery is good if you carry spares for a full day as "
            "a professional."
        ),
        url="https://www.youtube.com/watch?v=fake1",
        author="WeddingFilmsCo",
        title="Long term review: one year of weddings",
    ),
]

_EXPERT = [
    ReviewDoc(
        source="expert",
        text=(
            "In our lab testing the 33MP sensor delivers excellent dynamic range and the "
            "autofocus is reliable across modes. Battery is rated 580 shots CIPA, solid "
            "for the class. Rolling shutter readout measured at 1/15s."
        ),
        url="https://expert-reviews.example/a7iv",
        title="Lab test: full review",
    ),
]

_BESTBUY = [
    ReviewDoc(
        source="bestbuy",
        text="Great camera, the screen is sharp and menus are responsive. Heavy with the kit lens.",
        author="verified_buyer_41",
        title="Great upgrade",
    ),
]

_BY_NAME = {"reddit": _REDDIT, "youtube": _YOUTUBE, "expert": _EXPERT, "bestbuy": _BESTBUY}


class FakeReviewSource:
    def __init__(self, name: str, docs: list[ReviewDoc] | None = None):
        self.name = name
        self._docs = docs if docs is not None else _BY_NAME.get(name, [])

    def fetch(self, query: str, limit: int = 25) -> list[ReviewDoc]:
        return self._docs[:limit]


def fake_sources() -> list[FakeReviewSource]:
    return [FakeReviewSource(n) for n in ("expert", "bestbuy", "reddit", "youtube")]
