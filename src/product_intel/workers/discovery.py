"""Discovery + Spec Worker.

Section 2.1 collapses v1's Discovery and Specification-Extraction workers:
Channel3 search returns candidates carrying structured attributes, Rye
normalization firms each into a stable record. Expert-review spec enrichment
(the remaining deep-attribute gap) is Phase C and folds into the Review
Synthesis Worker; this Phase-A worker stops at normalized candidates.
"""

from __future__ import annotations

from ..clients.base import DiscoveryClient, NormalizationClient
from ..models import Product
from .constraints import Constraints, filter_candidates


class DiscoveryWorker:
    def __init__(self, discovery: DiscoveryClient, normalizer: NormalizationClient):
        self._discovery = discovery
        self._normalizer = normalizer

    def build_candidate_pool(
        self,
        query: str,
        constraints: Constraints | None = None,
        limit: int = 10,
        normalize: bool = True,
    ) -> list[Product]:
        candidates = self._discovery.search(query, limit=limit)
        if normalize:
            candidates = [self._maybe_normalize(p) for p in candidates]
        if constraints is not None:
            candidates = filter_candidates(candidates, constraints)
        candidates.sort(key=lambda p: p.price_subunits)
        return candidates

    def _maybe_normalize(self, p: Product) -> Product:
        """Re-resolve via Rye when the candidate lacks a stable id or price.

        Normalization failures are non-fatal: keep the Channel3 candidate.
        """
        if p.rye_product_id and p.price_subunits > 0:
            return p
        if not p.url:
            return p
        try:
            normalized = self._normalizer.normalize(p.url)
        except Exception:  # noqa: BLE001 — normalization is best-effort enrichment
            return p
        # Preserve the Channel3 id; prefer Rye's normalized fields.
        if not normalized.channel3_id:
            normalized.channel3_id = p.channel3_id
        if normalized.price_subunits == 0:
            normalized.price_subunits = p.price_subunits
        return normalized
