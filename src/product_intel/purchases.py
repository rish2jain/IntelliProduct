"""Phase 5 (Section 5): post-purchase tracking, corrected for 2026 reality.

No blanket "most retailers offer 30-day price protection" — that's false.
Amazon eliminated general price-drop adjustments; card price protection is
dead industry-wide. What survives is a small per-retailer policy table,
checked once at purchase time. If the retailer has no policy, the monitor
says so once and stops — no false-hope alerts.

Return-window and warranty-registration tracking are kept: cheap and real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .db import Store
from .merchants import canonical
from .models import Alert, AlertLayer, now_ts

DAY = 86400


@dataclass(frozen=True)
class RetailerPolicy:
    price_protection_days: Optional[int]  # None == case-by-case; 0 == none
    protection_note: str
    return_days: int


# 2026 policy table (Section 5 factual corrections).
POLICIES: dict[str, RetailerPolicy] = {
    "best buy": RetailerPolicy(15, "My Best Buy Plus/Total members only", 15),
    "target": RetailerPolicy(14, "14-day price match/adjustment", 90),
    "costco": RetailerPolicy(30, "30-day price adjustment", 90),
    "b&h": RetailerPolicy(None, "case-by-case — contact support with order number", 30),
    "adorama": RetailerPolicy(None, "case-by-case — contact support with order number", 30),
    "amazon": RetailerPolicy(0, "no general price-drop adjustments (eliminated years ago)", 30),
}
DEFAULT_POLICY = RetailerPolicy(0, "no known price-protection policy", 30)


def policy_for(merchant: str) -> RetailerPolicy:
    m = canonical(merchant)
    if m in POLICIES:
        return POLICIES[m]
    # Substring fallback for qualified names the alias table doesn't know
    # ("amazon uk", "best buy outlet").
    for key, pol in POLICIES.items():
        if key in m:
            return pol
    return DEFAULT_POLICY


def record_purchase(
    store: Store,
    label: str,
    merchant: str,
    price_subunits: int,
    url: Optional[str] = None,
    tracked_product_id: Optional[int] = None,
    purchased_at: Optional[float] = None,
) -> dict[str, Any]:
    pol = policy_for(merchant)
    no_protection = pol.price_protection_days == 0
    pid = store.add_purchase(
        {
            "tracked_product_id": tracked_product_id,
            "label": label,
            "merchant": merchant,
            "url": url,
            "price_subunits": price_subunits,
            "purchased_at": purchased_at or now_ts(),
            "return_days": pol.return_days,
            "protection_days": pol.price_protection_days,
            "protection_note": pol.protection_note,
            # If there is no policy, the one-time notice is this response;
            # the monitor never raises protection hopes for this purchase.
            "protection_notified": no_protection,
        }
    )
    return {
        "purchase_id": pid,
        "label": label,
        "merchant": merchant,
        "price": price_subunits / 100.0,
        "price_protection": (
            "none — monitor will not generate price-protection alerts for this purchase"
            if no_protection
            else pol.protection_note
            if pol.price_protection_days is None
            else f"{pol.price_protection_days} days ({pol.protection_note})"
        ),
        "return_window_days": pol.return_days,
        "reminder": "register the warranty, then call mark_warranty_registered",
    }


def purchase_status(store: Store, now: Optional[float] = None) -> list[dict[str, Any]]:
    now = now or now_ts()
    out = []
    for p in store.purchases(open_only=False):
        age_days = (now - p["purchased_at"]) / DAY
        ret_left = (p["return_days"] or 0) - age_days
        prot_left = (p["protection_days"] or 0) - age_days if p["protection_days"] else None
        out.append(
            {
                "purchase_id": p["id"],
                "label": p["label"],
                "merchant": p["merchant"],
                "paid": p["price_subunits"] / 100.0,
                "return_days_left": round(max(ret_left, 0), 1),
                "protection_days_left": round(max(prot_left, 0), 1) if prot_left is not None else None,
                "protection_note": p["protection_note"],
                "warranty_registered": bool(p["warranty_registered"]),
                "closed": bool(p["closed"]),
            }
        )
    return out


def check_purchases(store: Store, now: Optional[float] = None) -> list[Alert]:
    """Monitor pass over open purchases. Returns alerts to deliver.

    - Price-protection opportunity: within the protection window and the
      tracked product's latest observed price is below what was paid.
    - Return window closing: fires once when <=3 days remain.
    Both dedup through the market_events ledger.
    """
    now = now or now_ts()
    alerts: list[Alert] = []
    for p in store.purchases(open_only=True):
        age_days = (now - p["purchased_at"]) / DAY

        # Return window closing (once).
        ret_left = (p["return_days"] or 0) - age_days
        if 0 <= ret_left <= 3:
            msg = (
                f"{p['label']}: return window at {p['merchant']} closes in "
                f"{ret_left:.1f} days"
            )
            if store.try_add_event(f"ret:{p['id']}", "return_window_closing", msg, p["tracked_product_id"]):
                alerts.append(_purchase_alert(p, "return_window_closing", msg, p["price_subunits"]))

        # Price protection (only where a real, automatic policy exists and we
        # can see a live price; None == case-by-case, 0 == none — both skipped).
        prot_days = p["protection_days"]
        if not prot_days:
            continue
        if age_days > prot_days:
            continue
        tid = p["tracked_product_id"]
        if tid is None:
            continue
        latest = store.latest_observation(tid)
        if latest and latest.price_subunits < p["price_subunits"]:
            diff = p["price_subunits"] - latest.price_subunits
            msg = (
                f"{p['label']}: price-protection opportunity at {p['merchant']} — "
                f"paid ${p['price_subunits'] / 100:,.2f}, now ${latest.price_subunits / 100:,.2f} "
                f"(claim ${diff / 100:,.2f}; window closes in {prot_days - age_days:.1f} days; "
                f"{p['protection_note']})"
            )
            key = f"pp:{p['id']}:{latest.price_subunits}"
            if store.try_add_event(key, "price_protection", msg, tid):
                alerts.append(_purchase_alert(p, "price_protection_opportunity", msg, latest.price_subunits))
    return alerts


def _purchase_alert(p: dict[str, Any], rule: str, message: str, price_subunits: int) -> Alert:
    return Alert(
        tracked_product_id=p["tracked_product_id"] or -1,
        layer=AlertLayer.MARKET_CONTEXT,
        rule=rule,
        message=message,
        price_subunits=price_subunits,
        deep_link=p["url"] or "",
        payload={"purchase_id": p["id"], "merchant": p["merchant"]},
    )
