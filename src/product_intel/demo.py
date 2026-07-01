"""Offline end-to-end demo across all phases.

Runs with the in-memory fakes (no API keys, no network): discover -> track ->
price-drop alert with the effective-price stack -> review synthesis with a
contradiction -> Layer 3 market scan -> purchase handoff -> post-purchase.

    PRODUCT_INTEL_FAKE=1 python -m product_intel.demo
"""

from __future__ import annotations

import os
import tempfile

from .config import Config
from .db import Store
from .effective_price import OffersBook
from .models import Availability, PriceObservation
from .service import Service


def main() -> None:
    os.environ.setdefault("PRODUCT_INTEL_FAKE", "1")
    tmp = tempfile.mkdtemp(prefix="product-intel-demo-")
    config = Config.from_env()
    object.__setattr__(config, "db_path", os.path.join(tmp, "demo.sqlite3"))
    service = Service(config=config, store=Store(config.db_path))

    # Phase B offer book (normally ~/.product-intel/offers.yaml).
    service.offers = OffersBook.from_dict(
        {
            "point_values": {"AA": 1.6, "C1": 1.8},
            "portals": [{"portal": "Rakuten", "merchant": "B&H", "rate": 4.0, "kind": "cash"}],
            "card_offers": [
                {"card": "Amex Plat", "merchant": "B&H", "discount": 64.0,
                 "min_spend": 600.0, "expires": "2026-06-30"}
            ],
            "earn_rates": [{"card": "Venture X", "merchant": "*", "rate": 2, "currency": "C1"}],
        }
    )

    print("== Phase A: discover ==")
    intent = service.clarify_intent("Sony a7 IV body only", category="cameras", budget_max=2500.0)
    pool = service.build_candidate_pool("Sony a7 IV", intent_id=intent["intent_id"])
    for c in pool["candidates"]:
        print(f"  {c['merchant']:<18} ${c['price']:>9,.2f}  {c['title']}")

    print("\n== Phase A: track (target $2,300 at B&H, category ff-camera) ==")
    t = service.start_tracking(
        label="Sony a7 IV",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2300.0,
        category="ff-camera",
    )
    tid = t["tracked_id"]

    print("\n== Phase B: price drop to $2,279 -> Layer 1 alert with effective price ==")
    service.store.add_observation(
        PriceObservation(tid, 227900, "USD", Availability.IN_STOCK, "demo-injected")
    )
    from .alerts.rules import evaluate

    tracked = service.store.get_tracked(tid)
    ctx = service.price_context.get_context(url=tracked.url, current_subunits=227900)
    alerts, _ = evaluate(tracked, service.store.observations(tid), ctx, service.rule_params)
    for a in alerts:
        from .effective_price import compute_effective_price

        ep = compute_effective_price(service.offers, tracked.merchant, a.price_subunits)
        print(f"  ALERT [{a.layer.value}] {a.message}")
        print(f"        {ep.summary()}")

    print("\n== Phase C: review synthesis (profile: parent / indoor volleyball / weekly) ==")
    out = service.synthesize_reviews(
        "Sony a7 IV",
        product_label="Sony a7 IV",
        user_profile="parent shooting my kid's indoor volleyball every weekend",
    )
    print(f"  review base: {out['review_base']}")
    m = out["matched_segment"]
    print(f"  matched segment: '{m['label']}' ({m['reviews']} reviews, sim {m['profile_similarity']})")
    for n in m["negatives"]:
        print(f"    - {n['aspect']}: {n['reports']} report(s) — \"{n['example'][:70]}...\"")
    for c in out["contradictions"]:
        print(f"  contradiction [{c['aspect']}]: ledger #{c.get('ledger_id')} "
              f"({c['supporting']['negative']} neg vs {c['supporting']['positive']} pos)")

    print("\n== Phase D: Layer 3 market scan ==")
    for a in service.market_scan(force=True)["alerts"]:
        print(f"  ALERT [{a['rule']}] {a['message'][:110]}")

    print("\n== Handoff ==")
    h = service.stage_handoff(tid)
    b = h["briefing"]
    print(f"  executor={h['executor']}  deep_link={h['deep_link']}")
    print(f"  ${b['current_price']} vs target ${b['target_price']}  meets_target={b['meets_target']}")
    print(f"  effective: {b['effective_price']['summary']}")
    print(f"  price protection: {b['price_protection']}")

    print("\n== Phase 5: record purchase ==")
    p = service.record_purchase("Sony a7 IV", "B&H", 2279.0, tracked_id=tid)
    print(f"  {p['price_protection']}; return window {p['return_window_days']} days")

    service.close()
    print(f"\nDemo DB at {config.db_path}")


if __name__ == "__main__":
    main()
