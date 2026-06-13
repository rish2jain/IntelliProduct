"""Phase D: Layer 3 market-context monitoring (Section 5).

The differentiator layer, with sources specified rather than vague:

- **Successor announcements**: a weekly web-search pass scoped to tracked
  products' brands/labels.
- **Competitor price moves**: fall out of multi-candidate tracking for free —
  tracked products sharing a ``category`` watch each other's drops.
- **Discontinuation signals**: availability enums flipping to
  out_of_stock/backorder on a product that was previously in stock.
- **Recall/firmware events**: checked against *open contradiction records*
  from the review-synthesis ledger ("manufacturer claims fix for the battery
  drain flagged by 23 long-term owners"), not a generic news trawl.

Every event dedups through the market_events ledger: one alert per distinct
event, ever.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Optional

from .clients.base import ClientNotConfigured
from .clients.websearch import WebSearchClient
from .db import Store
from .models import Alert, AlertLayer, Availability, TrackedProduct, now_ts

LAST_SCAN_KEY = "last_market_scan"


@dataclass(frozen=True)
class ScanParams:
    competitor_drop_fraction: float = 0.05  # 5% under trailing median
    competitor_min_observations: int = 3
    search_interval_seconds: int = 604800  # weekly


class MarketScanner:
    def __init__(self, store: Store, search: Optional[WebSearchClient], params: ScanParams = ScanParams()):
        self._store = store
        self._search = search
        self._params = params

    # ------------------------------------------------------------------
    def scan(self, force: bool = False, now: Optional[float] = None) -> list[Alert]:
        """Run all Layer 3 checks. Cheap checks run every sweep; the web-search
        pass (successors, firmware) honors its weekly cadence unless forced."""
        now = now or now_ts()
        tracked = self._store.active_tracked()
        alerts: list[Alert] = []

        alerts += self._discontinuation(tracked)
        alerts += self._competitor_moves(tracked)

        if force or self._search_due(now):
            alerts += self._successor_pass(tracked)
            alerts += self._firmware_pass(tracked)
            self._store.set_meta(LAST_SCAN_KEY, str(now))
        return alerts

    def _search_due(self, now: float) -> bool:
        last = self._store.get_meta(LAST_SCAN_KEY)
        return last is None or now - float(last) >= self._params.search_interval_seconds

    # ------------------------------------------------------------------
    def _discontinuation(self, tracked: list[TrackedProduct]) -> list[Alert]:
        alerts = []
        gone = {Availability.OUT_OF_STOCK, Availability.BACKORDER}
        for t in tracked:
            obs = self._store.observations(int(t.id))
            if len(obs) < 2:
                continue
            if obs[-1].availability in gone and any(o.availability == Availability.IN_STOCK for o in obs[:-1]):
                msg = (
                    f"{t.label}: availability flipped to {obs[-1].availability.value} at "
                    f"{t.merchant} after being in stock — possible discontinuation or "
                    f"supply gap; check other retailers"
                )
                if self._store.try_add_event(f"disc:{t.id}", "discontinuation_signal", msg, t.id):
                    alerts.append(_l3(t, "discontinuation_signal", msg, obs[-1].price_subunits))
        return alerts

    # ------------------------------------------------------------------
    def _competitor_moves(self, tracked: list[TrackedProduct]) -> list[Alert]:
        alerts = []
        by_category: dict[str, list[TrackedProduct]] = {}
        for t in tracked:
            if t.category:
                by_category.setdefault(t.category, []).append(t)

        for category, group in by_category.items():
            if len(group) < 2:
                continue
            for t in group:
                for other in group:
                    if other.id == t.id:
                        continue
                    obs = self._store.observations(int(other.id))
                    if len(obs) < self._params.competitor_min_observations:
                        continue
                    latest = obs[-1].price_subunits
                    median = statistics.median(o.price_subunits for o in obs[:-1])
                    if latest <= median * (1 - self._params.competitor_drop_fraction):
                        msg = (
                            f"{t.label}: competitor move in '{category}' — {other.label} "
                            f"dropped to ${latest / 100:,.2f} "
                            f"({(1 - latest / median) * 100:.0f}% under its trailing median) "
                            f"at {other.merchant}"
                        )
                        key = f"comp:{t.id}:{other.id}:{latest}"
                        if self._store.try_add_event(key, "competitor_price_move", msg, t.id):
                            alerts.append(_l3(t, "competitor_price_move", msg, latest))
        return alerts

    # ------------------------------------------------------------------
    def _successor_pass(self, tracked: list[TrackedProduct]) -> list[Alert]:
        if self._search is None:
            return []
        alerts = []
        for t in tracked:
            hits = self._safe_search(f"{t.label} successor announcement", limit=3)
            for h in hits:
                blob = (h["title"] + " " + h["snippet"]).lower()
                if "successor" in blob or "announce" in blob or "rumor" in blob:
                    msg = (
                        f"{t.label}: possible successor signal — \"{h['title']}\" ({h['url']}). "
                        f"Current-gen prices typically soften after announcements."
                    )
                    if self._store.try_add_event(f"succ:{t.id}:{h['url']}", "successor_signal", msg, t.id):
                        alerts.append(_l3(t, "successor_signal", msg, 0, deep_link=h["url"]))
                    break
        return alerts

    def _firmware_pass(self, tracked: list[TrackedProduct]) -> list[Alert]:
        """Check firmware/recall news against open contradiction records."""
        if self._search is None:
            return []
        alerts = []
        by_label = {t.label: t for t in tracked}
        for c in self._store.contradictions(open_only=True):
            t = by_label.get(c["product_label"])
            if t is None:
                continue
            hits = self._safe_search(f"{c['product_label']} firmware update {c['aspect']} fix", limit=3)
            for h in hits:
                blob = (h["title"] + " " + h["snippet"]).lower()
                if "firmware" in blob or "recall" in blob or "fix" in blob:
                    msg = (
                        f"{t.label}: manufacturer may have addressed the open "
                        f"'{c['aspect']}' contradiction (ledger #{c['id']}) — "
                        f"\"{h['title']}\" ({h['url']})"
                    )
                    if self._store.try_add_event(f"fw:{c['id']}:{h['url']}", "firmware_signal", msg, t.id):
                        self._store.set_contradiction_status(
                            int(c["id"]), "update_reported", note=h["url"]
                        )
                        alerts.append(_l3(t, "firmware_or_recall_signal", msg, 0, deep_link=h["url"]))
                    break
        return alerts

    def _safe_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            return self._search.search(query, limit=limit)  # type: ignore[union-attr]
        except (ClientNotConfigured, Exception):  # noqa: BLE001 — search is best-effort
            return []


def _l3(t: TrackedProduct, rule: str, message: str, price_subunits: int, deep_link: str = "") -> Alert:
    return Alert(
        tracked_product_id=int(t.id),
        layer=AlertLayer.MARKET_CONTEXT,
        rule=rule,
        message=message,
        price_subunits=price_subunits,
        deep_link=deep_link or t.url,
        payload={"merchant": t.merchant, "label": t.label},
    )
