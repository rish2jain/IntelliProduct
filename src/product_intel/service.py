"""Application service shared by the MCP server and the monitor daemon.

Owns the Store, clients, and workers, and exposes the operations behind the MCP
tools plus the monitor's per-product poll. Returns plain dict/list structures so
both the FastMCP layer and the launchd daemon can consume them without
importing each other.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .alerts.notifier import Notifier
from .alerts.rules import RuleParams, evaluate
from .clients.base import ClientNotConfigured
from .config import Config
from .container import build_clients
from .db import Store
from .models import Alert, Availability, PriceObservation, Product, TrackedProduct
from .workers.constraints import Constraints, violations
from .workers.discovery import DiscoveryWorker
from .workers.price_context import PriceContextWorker

log = logging.getLogger("product_intel.service")


class Service:
    def __init__(self, config: Optional[Config] = None, store: Optional[Store] = None):
        self.config = config or Config.from_env()
        self.store = store or Store(self.config.db_path)
        clients = build_clients(self.config)
        self.discovery = DiscoveryWorker(clients.discovery, clients.normalizer)
        self.price_context = PriceContextWorker(clients.price_history)
        self._normalizer = clients.normalizer
        self.notifier = Notifier(self.config)
        self.rule_params = RuleParams()

    def close(self) -> None:
        self.store.close()

    # -- intent ----------------------------------------------------------
    def clarify_intent(
        self,
        raw_query: str,
        category: Optional[str] = None,
        budget_min: Optional[float] = None,
        budget_max: Optional[float] = None,
        constraints: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Persist an intent profile and echo the parsed constraints.

        Conversational refinement happens in the Claude session; this records
        the resolved profile candidates and tracking will be scored against.
        """
        intent_id = self.store.create_intent(
            raw_query=raw_query,
            category=category,
            budget_min_subunits=_to_subunits(budget_min),
            budget_max_subunits=_to_subunits(budget_max),
            constraints=constraints or {},
        )
        return {
            "intent_id": intent_id,
            "raw_query": raw_query,
            "category": category,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "constraints": constraints or {},
        }

    # -- discovery -------------------------------------------------------
    def build_candidate_pool(
        self,
        query: str,
        intent_id: Optional[int] = None,
        constraints: Optional[dict[str, Any]] = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        c = Constraints.from_dict(constraints or {})
        try:
            candidates = self.discovery.build_candidate_pool(query, constraints=c, limit=limit)
        except ClientNotConfigured as e:
            return {"error": str(e), "hint": "set CHANNEL3_API_KEY or run with PRODUCT_INTEL_FAKE=1"}
        ids = self.store.add_candidates(intent_id, candidates)
        return {
            "intent_id": intent_id,
            "count": len(candidates),
            "candidates": [_product_dict(p, cid) for cid, p in zip(ids, candidates)],
        }

    def compare_candidates(self, intent_id: int) -> dict[str, Any]:
        products = self.store.candidates_for_intent(intent_id)
        return {
            "intent_id": intent_id,
            "count": len(products),
            "by_price": [_product_dict(p) for p in products],
        }

    # -- price context ---------------------------------------------------
    def get_price_context(
        self,
        url: str,
        rye_product_id: Optional[str] = None,
        current_price: Optional[float] = None,
    ) -> dict[str, Any]:
        ctx = self.price_context.get_context(
            url=url,
            rye_product_id=rye_product_id,
            current_subunits=_to_subunits(current_price),
        )
        return {
            "url": url,
            "history_available": ctx.history_available,
            "summary": ctx.summary(),
            "amazon_all_time_low": _from_subunits(ctx.amazon_all_time_low_subunits),
            "percentile": ctx.percentile,
            "notes": ctx.notes,
            "source": ctx.history.source,
            "history_note": ctx.history.note,
        }

    # -- tracking --------------------------------------------------------
    def start_tracking(
        self,
        label: str,
        merchant: str,
        url: str,
        target_price: float,
        rye_product_id: Optional[str] = None,
    ) -> dict[str, Any]:
        tracked = TrackedProduct(
            label=label,
            merchant=merchant,
            url=url,
            target_price_subunits=_to_subunits(target_price) or 0,
            rye_product_id=rye_product_id,
        )
        tid = self.store.add_tracked(tracked)
        # Seed an initial observation so cold-start history begins immediately.
        self.check_once(tid)
        return {"tracked_id": tid, "label": label, "target_price": target_price, "active": True}

    def stop_tracking(self, tracked_id: int) -> dict[str, Any]:
        self.store.set_tracking_active(tracked_id, False)
        return {"tracked_id": tracked_id, "active": False}

    def tracking_status(self, tracked_id: Optional[int] = None) -> dict[str, Any]:
        tracked = (
            [self.store.get_tracked(tracked_id)] if tracked_id else self.store.all_tracked()
        )
        tracked = [t for t in tracked if t]
        out = []
        for t in tracked:
            latest = self.store.latest_observation(int(t.id))
            obs = self.store.observations(int(t.id))
            alerts = self.store.alerts_for(int(t.id), limit=5)
            out.append(
                {
                    "tracked_id": t.id,
                    "label": t.label,
                    "merchant": t.merchant,
                    "url": t.url,
                    "target_price": _from_subunits(t.target_price_subunits),
                    "active": t.active,
                    "latest_price": _from_subunits(latest.price_subunits) if latest else None,
                    "observations": len(obs),
                    "recent_alerts": [_alert_dict(a) for a in alerts],
                }
            )
        return {"tracked": out}

    # -- monitor poll ----------------------------------------------------
    def check_once(self, tracked_id: int) -> dict[str, Any]:
        """Record one observation for a tracked product and evaluate alerts.

        Live price comes from the normalizer (Rye) — a legitimate data API, not
        a logged-in session scrape (Section 1.1). Amazon history context comes
        from Keepa where available.
        """
        t = self.store.get_tracked(tracked_id)
        if not t:
            return {"error": f"no tracked product {tracked_id}"}

        live = self._live_price(t)
        if live is None:
            return {"tracked_id": tracked_id, "skipped": "could not resolve a live price"}

        obs = PriceObservation(
            tracked_product_id=tracked_id,
            price_subunits=live.price_subunits,
            currency=live.currency,
            availability=live.availability,
            source=live.source,
        )
        self.store.add_observation(obs)

        ctx = self.price_context.get_context(
            url=t.url,
            rye_product_id=t.rye_product_id,
            current_subunits=live.price_subunits,
        )
        observations = self.store.observations(tracked_id)
        alerts, last_alert = evaluate(t, observations, ctx, self.rule_params)

        fired = []
        for a in alerts:
            a.delivered = self.notifier.send(a)
            self.store.add_alert(a)
            fired.append(_alert_dict(a))
        if alerts:
            self.store.update_last_alert(tracked_id, last_alert)

        return {
            "tracked_id": tracked_id,
            "observed_price": _from_subunits(live.price_subunits),
            "availability": live.availability.value,
            "price_context": ctx.summary(),
            "alerts_fired": fired,
        }

    def check_all_active(self) -> list[dict[str, Any]]:
        results = []
        for t in self.store.active_tracked():
            try:
                results.append(self.check_once(int(t.id)))
            except Exception as e:  # noqa: BLE001 — one bad product must not kill the loop
                log.exception("check_once failed for %s", t.id)
                results.append({"tracked_id": t.id, "error": str(e)})
        return results

    # -- handoff ---------------------------------------------------------
    def stage_handoff(self, tracked_id: int, constraints: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Purchase Handoff Agent (Section 1.1).

        Does everything *except* touch the retailer's logged-in session:
        re-verifies the live price via a data API, re-runs the constraint
        post-filter, and produces a deep link + one-screen briefing. The human
        opens the link and completes checkout.
        """
        t = self.store.get_tracked(tracked_id)
        if not t:
            return {"error": f"no tracked product {tracked_id}"}

        live = self._live_price(t)
        product = Product(
            title=t.label,
            url=t.url,
            merchant=t.merchant,
            price_subunits=live.price_subunits if live else 0,
            availability=live.availability if live else Availability.UNKNOWN,
            purchasable=True,
            rye_product_id=t.rye_product_id,
        )
        c = Constraints.from_dict(constraints or {})
        constraint_violations = violations(product, c) if constraints else []
        ctx = self.price_context.get_context(
            url=t.url,
            rye_product_id=t.rye_product_id,
            current_subunits=product.price_subunits,
        )
        meets_target = product.price_subunits <= t.target_price_subunits and product.price_subunits > 0
        return {
            "executor": "manual_deeplink",  # acp_checkout is a future per-retailer capability flag
            "tracked_id": tracked_id,
            "deep_link": t.url,
            "briefing": {
                "label": t.label,
                "merchant": t.merchant,
                "current_price": _from_subunits(product.price_subunits) if live else None,
                "target_price": _from_subunits(t.target_price_subunits),
                "meets_target": meets_target,
                "availability": product.availability.value,
                "price_context": ctx.summary(),
                "constraint_violations": constraint_violations,
                "note": "Open the deep link and complete checkout manually. "
                "Agent does not touch the retailer's logged-in session.",
            },
        }

    # -- internals -------------------------------------------------------
    def _live_price(self, t: TrackedProduct):
        """Resolve a current price via the normalizer; None on failure."""
        try:
            p = self._normalizer.normalize(t.url)
        except ClientNotConfigured:
            return None
        except Exception:  # noqa: BLE001
            log.exception("normalize failed for %s", t.url)
            return None
        if p.price_subunits <= 0:
            return None

        class _Live:
            price_subunits = p.price_subunits
            currency = p.currency
            availability = p.availability
            source = "rye"

        return _Live()


# -- serialization helpers ----------------------------------------------
def _to_subunits(value: Optional[float]) -> Optional[int]:
    return None if value is None else int(round(value * 100))


def _from_subunits(value: Optional[int]) -> Optional[float]:
    return None if value is None else value / 100.0


def _product_dict(p: Product, candidate_id: Optional[int] = None) -> dict[str, Any]:
    d = {
        "title": p.title,
        "merchant": p.merchant,
        "price": p.price,
        "currency": p.currency,
        "availability": p.availability.value,
        "purchasable": p.purchasable,
        "brand": p.brand,
        "url": p.url,
        "rye_product_id": p.rye_product_id,
        "attributes": p.attributes,
    }
    if candidate_id is not None:
        d["candidate_id"] = candidate_id
    return d


def _alert_dict(a: Alert) -> dict[str, Any]:
    return {
        "layer": a.layer.value,
        "rule": a.rule,
        "message": a.message,
        "price": a.price_subunits / 100.0,
        "deep_link": a.deep_link,
        "delivered": a.delivered,
        "created_at": a.created_at,
    }
