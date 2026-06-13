"""Use-case segmentation mechanics (Section 3).

Two-stage: the local LLM extracts a structured use-case tuple from each
review (llm.py); here the tuples are embedded and clustered, and candidate
scores are computed *within the cluster nearest the user's constraint
profile* — not across the whole review base.

Clustering is greedy centroid-threshold over cosine similarity: adequate for
short structured tuple strings, dependency-free, and deterministic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .embeddings import Embedder, cosine
from .models import SegmentedReview, UseCaseTuple


@dataclass
class Segment:
    index: int
    members: list[SegmentedReview] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)

    def label(self) -> str:
        """Majority value per tuple field, e.g. 'parent / indoor / weekly / sports'."""

        def majority(values: list[str]) -> str:
            return Counter(values).most_common(1)[0][0] if values else "general"

        ts = [m.use_case for m in self.members]
        return " / ".join(
            [
                majority([t.user_type for t in ts]),
                majority([t.environment for t in ts]),
                majority([t.frequency for t in ts]),
                majority([t.primary_function for t in ts]),
            ]
        )

    def _recompute(self) -> None:
        dims = len(self.members[0].embedding)
        sums = [0.0] * dims
        for m in self.members:
            for i, v in enumerate(m.embedding):
                sums[i] += v
        self.centroid = [s / len(self.members) for s in sums]

    def add(self, review: SegmentedReview) -> None:
        self.members.append(review)
        review.cluster = self.index
        self._recompute()


def cluster_reviews(
    reviews: list[SegmentedReview],
    embedder: Embedder,
    threshold: float = 0.6,
) -> list[Segment]:
    """Embed each review's use-case tuple and greedily cluster."""
    if not reviews:
        return []
    vectors = embedder.embed([r.use_case.as_text() for r in reviews])
    for r, v in zip(reviews, vectors):
        r.embedding = v

    segments: list[Segment] = []
    for r in reviews:
        best: Segment | None = None
        best_sim = threshold
        for seg in segments:
            sim = cosine(r.embedding, seg.centroid)
            if sim >= best_sim:
                best, best_sim = seg, sim
        if best is None:
            best = Segment(index=len(segments))
            segments.append(best)
        best.add(r)
    return segments


def match_profile(
    segments: list[Segment],
    profile: UseCaseTuple,
    embedder: Embedder,
) -> tuple[Segment | None, float]:
    """Find the segment nearest the user's constraint profile."""
    if not segments:
        return None, 0.0
    profile_vec = embedder.embed([profile.as_text()])[0]
    best, best_sim = None, -1.0
    for seg in segments:
        sim = cosine(profile_vec, seg.centroid)
        if sim > best_sim:
            best, best_sim = seg, sim
    return best, best_sim
