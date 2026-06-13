"""Offline end-to-end demo.

Runs the full Phase-A flow with the in-memory fake clients (no API keys, no
network): discover -> track -> simulate a price drop -> fire Layer 1/2 alerts
-> stage a handoff. Use it to see the system work and to sanity-check a fresh
checkout.

    PRODUCT_INTEL_FAKE=1 python -m product_intel.demo
"""

from __future__ import annotations

import os
import tempfile

from .config import Config
from .db import Store
from .models import Availability, PriceObservation
from .service import Service


def main() -> None:
    os.environ.setdefault("PRODUCT_INTEL_FAKE", "1")
    tmp = tempfile.mkdtemp(prefix="product-intel-demo-")
    config = Config.from_env()
    object.__setattr__(config, "db_path", os.path.join(tmp, "demo.sqlite3"))
    service = Service(config=config, store=Store(config.db_path))

    print("== discover ==")
    intent = service.clarify_intent("Sony a7 IV body only", category="cameras", budget_max=2500.0)
    pool = service.build_candidate_pool("Sony a7 IV", intent_id=intent["intent_id"])
    for c in pool["candidates"]:
        print(f"  {c['merchant']:<18} ${c['price']:>9,.2f}  {c['title']}")

    print("\n== track (target $2,300 at B&H) ==")
    t = service.start_tracking(
        label="Sony a7 IV (body)",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2300.0,
    )
    tid = t["tracked_id"]
    print(f"  tracking_id={tid}; seeded observation at ${pool['candidates'][0]['price']:,.2f}")

    print("\n== simulate a Layer-1 price drop to $2,279 ==")
    # In production the monitor pulls the live price via Rye; here we inject one.
    service.store.add_observation(
        PriceObservation(
            tracked_product_id=tid,
            price_subunits=227900,
            currency="USD",
            availability=Availability.IN_STOCK,
            source="demo-injected",
        )
    )
    tracked = service.store.get_tracked(tid)
    ctx = service.price_context.get_context(url=tracked.url, current_subunits=227900)
    from .alerts.rules import evaluate

    alerts, last = evaluate(tracked, service.store.observations(tid), ctx, service.rule_params)
    for a in alerts:
        service.store.add_alert(a)
        print(f"  ALERT [{a.layer.value}] {a.message}")
    if not alerts:
        print("  (no alerts)")

    print("\n== handoff ==")
    h = service.stage_handoff(tid)
    b = h["briefing"]
    print(f"  executor={h['executor']}  deep_link={h['deep_link']}")
    print(f"  ${b['current_price']} vs target ${b['target_price']}  meets_target={b['meets_target']}")
    print(f"  {b['price_context']}")

    service.close()
    print(f"\nDemo DB at {config.db_path}")


if __name__ == "__main__":
    main()
