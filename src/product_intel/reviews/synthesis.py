"""Review Synthesis Worker — the real build (Section 3).

Pipeline: gather (tiered sources) -> extract (local LLM: use-case tuple +
aspect claims per doc) -> cluster (embeddings over tuples) -> score within
the segment nearest the user's profile -> persist contradictions.

Output is structured and *honest about coverage*: every source that yielded
nothing (no API key, fetch failure, or genuinely zero docs) is reported, and
Amazon is always flagged as unverifiable since there is no legitimate review
API ("review base: 412 Reddit/forum posts, 14 expert reviews, 0 Amazon
reviews — Amazon review sentiment unverifiable").
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from ..clients.base import ClientNotConfigured
from ..db import Store
from .embeddings import Embedder
from .llm import LocalLLM
from .models import Claim, SegmentedReview, UseCaseTuple
from .segmentation import Segment, cluster_reviews, match_profile
from .sources import ReviewSource


class ReviewSynthesizer:
    def __init__(
        self,
        sources: list[ReviewSource],
        llm: LocalLLM,
        embedder: Embedder,
        store: Optional[Store] = None,
    ):
        self._sources = sources
        self._llm = llm
        self._embedder = embedder
        self._store = store

    def synthesize(
        self,
        query: str,
        product_label: Optional[str] = None,
        user_profile: Optional[str] = None,
        limit_per_source: int = 25,
    ) -> dict[str, Any]:
        label = product_label or query

        # 1. Gather, tracking per-source coverage honestly.
        reviews: list[SegmentedReview] = []
        coverage: dict[str, int] = {}
        coverage_notes: list[str] = []
        for src in self._sources:
            try:
                docs = src.fetch(query, limit=limit_per_source)
            except ClientNotConfigured as e:
                coverage[src.name] = 0
                coverage_notes.append(f"{src.name}: skipped ({e})")
                continue
            except Exception as e:  # noqa: BLE001 — a dead source is a note, not a crash
                coverage[src.name] = 0
                coverage_notes.append(f"{src.name}: fetch failed ({e})")
                continue
            coverage[src.name] = len(docs)
            for doc in docs:
                reviews.append(
                    SegmentedReview(
                        doc=doc,
                        use_case=self._llm.extract_use_case(doc.text),
                        claims=self._llm.extract_claims(doc),
                    )
                )
        coverage["amazon"] = 0
        coverage_notes.append("amazon: 0 reviews — Amazon review sentiment unverifiable (no review API)")

        if not reviews:
            return {
                "product": label,
                "review_base": coverage,
                "coverage_notes": coverage_notes,
                "segments": [],
                "matched_segment": None,
                "error": "no reviews gathered from any source",
            }

        # 2-3. Cluster use-case tuples; match the user's profile.
        segments = cluster_reviews(reviews, self._embedder)
        profile_tuple = (
            self._llm.extract_use_case(user_profile) if user_profile else UseCaseTuple()
        )
        matched, similarity = match_profile(segments, profile_tuple, self._embedder)

        # 4. Aspect scores within the matched segment; long-term findings global.
        long_term = [r for r in reviews if r.use_case.duration_bucket == "long"]
        contradictions = self._persist_contradictions(label, reviews)

        return {
            "product": label,
            "review_base": coverage,
            "coverage_notes": coverage_notes,
            "segments": [_segment_dict(s) for s in segments],
            "user_profile": profile_tuple.as_text(),
            "matched_segment": _segment_detail(matched, similarity) if matched else None,
            "long_term": {
                "owners": len(long_term),
                "issues": _aspect_rollup([c for r in long_term for c in r.claims if c.sentiment < 0]),
            },
            "contradictions": contradictions,
        }

    def _persist_contradictions(
        self, label: str, reviews: list[SegmentedReview]
    ) -> list[dict[str, Any]]:
        """Opposing claims on the same aspect from different sources become
        ledger records (claim A, source A, claim B, source B, status)."""
        by_aspect: dict[str, list[Claim]] = defaultdict(list)
        for r in reviews:
            for c in r.claims:
                by_aspect[c.aspect].append(c)

        found: list[dict[str, Any]] = []
        for aspect, claims in by_aspect.items():
            pos = [c for c in claims if c.sentiment > 0]
            neg = [c for c in claims if c.sentiment < 0]
            if not (pos and neg):
                continue
            a, b = neg[0], pos[0]
            if a.source_ref == b.source_ref:
                continue
            record = {
                "aspect": aspect,
                "claim_a": a.quote,
                "source_a": a.source_ref,
                "claim_b": b.quote,
                "source_b": b.source_ref,
                "supporting": {"negative": len(neg), "positive": len(pos)},
                "status": "open",
            }
            if self._store is not None:
                cid = self._store.add_contradiction(
                    label, aspect, a.quote, a.source_ref, b.quote, b.source_ref
                )
                record["ledger_id"] = cid  # None == already open in the ledger
            found.append(record)
        return found


def _aspect_rollup(claims: list[Claim]) -> list[dict[str, Any]]:
    counts: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        counts[c.aspect].append(c)
    return [
        {"aspect": aspect, "reports": len(cs), "example": cs[0].quote, "sources": sorted({c.source_ref for c in cs})}
        for aspect, cs in sorted(counts.items(), key=lambda kv: -len(kv[1]))
    ]


def _segment_dict(s: Segment) -> dict[str, Any]:
    return {"segment": s.index, "label": s.label(), "reviews": s.size}


def _segment_detail(s: Segment, similarity: float) -> dict[str, Any]:
    claims = [c for m in s.members for c in m.claims]
    positives = _aspect_rollup([c for c in claims if c.sentiment > 0])
    negatives = _aspect_rollup([c for c in claims if c.sentiment < 0])
    return {
        "segment": s.index,
        "label": s.label(),
        "reviews": s.size,
        "profile_similarity": round(similarity, 3),
        "positives": positives,
        "negatives": negatives,
        "sources": sorted({m.doc.ref for m in s.members}),
    }
