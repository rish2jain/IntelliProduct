"""Review-synthesis domain models (Section 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Source tiers, in order of defensibility (Section 3 sourcing reality):
# 1 = expert review sites (published test data, standard fetching)
# 2 = retailer reviews where an API exists (Best Buy yes, Amazon no)
# 3 = community (Reddit official API, YouTube Data API) — richest use-case
#     context and the only tier where ownership duration is reliably stated.
TIER_BY_SOURCE = {"expert": 1, "bestbuy": 2, "reddit": 3, "youtube": 3}


@dataclass(slots=True)
class ReviewDoc:
    source: str  # "expert" | "bestbuy" | "reddit" | "youtube"
    text: str
    url: str = ""
    author: str = ""
    title: str = ""

    @property
    def tier(self) -> int:
        return TIER_BY_SOURCE.get(self.source, 3)

    @property
    def ref(self) -> str:
        return self.url or f"{self.source}:{self.author or self.title or 'unknown'}"


@dataclass(slots=True)
class UseCaseTuple:
    """The structured use-case extracted from each review/post (Section 3):
    user type, frequency, environment, primary function, ownership duration.

    Ownership duration is a first-class field — the 6-month-failure-mode
    insight only works if long-term owners can be filtered.
    """

    user_type: str = "general"
    frequency: str = "general"
    environment: str = "general"
    primary_function: str = "general"
    ownership_months: Optional[float] = None

    @property
    def duration_bucket(self) -> str:
        if self.ownership_months is None:
            return "unknown"
        return "long" if self.ownership_months >= 6 else "short"

    def as_text(self) -> str:
        """Stable token form for embedding/clustering."""
        return (
            f"user_type={self.user_type} environment={self.environment} "
            f"frequency={self.frequency} function={self.primary_function} "
            f"duration={self.duration_bucket}"
        )


@dataclass(slots=True)
class Claim:
    """An aspect-level claim extracted from one review."""

    aspect: str  # "battery", "autofocus", ...
    sentiment: int  # +1 / -1
    quote: str
    source_ref: str = ""


@dataclass(slots=True)
class SegmentedReview:
    doc: ReviewDoc
    use_case: UseCaseTuple
    claims: list[Claim] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    cluster: int = -1
