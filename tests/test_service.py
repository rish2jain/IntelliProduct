import os
import tempfile

import pytest

from product_intel.config import Config
from product_intel.db import Store
from product_intel.models import Availability, PriceObservation
from product_intel.service import Service


@pytest.fixture()
def service():
    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    tmp = tempfile.mkdtemp(prefix="pi-test-")
    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", os.path.join(tmp, "t.sqlite3"))
    svc = Service(config=cfg, store=Store(cfg.db_path))
    yield svc
    svc.close()


def test_discover_track_handoff_flow(service):
    intent = service.clarify_intent("Sony a7 IV", category="cameras", budget_max=3000.0)
    pool = service.build_candidate_pool("Sony a7 IV", intent_id=intent["intent_id"])
    assert pool["count"] >= 1

    cmp = service.compare_candidates(intent["intent_id"])
    assert cmp["count"] == pool["count"]
    # ordered by price
    prices = [c["price"] for c in cmp["by_price"]]
    assert prices == sorted(prices)

    t = service.start_tracking(
        label="Sony a7 IV (body)",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2300.0,
    )
    tid = t["tracked_id"]
    # start_tracking seeds an initial observation
    status = service.tracking_status(tid)["tracked"][0]
    assert status["observations"] >= 1
    assert status["latest_price"] is not None


def test_layer1_alert_fires_through_service(service):
    t = service.start_tracking(
        label="cam",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2400.0,  # fake B&H price is 2398 -> at/below target
    )
    tid = t["tracked_id"]
    # The seed observation in start_tracking already fires Layer 1.
    status = service.tracking_status(tid)["tracked"][0]
    layers = [a["layer"] for a in status["recent_alerts"]]
    assert "layer1_threshold" in layers

    # A second check at the same price is deduped (no improvement).
    res = service.check_once(tid)
    assert res["alerts_fired"] == []


def test_price_context_amazon_vs_non_amazon(service):
    amazon = service.get_price_context("https://www.amazon.com/dp/B09JZ9JTLB", current_price=2499.0)
    assert amazon["history_available"] is True
    assert amazon["amazon_all_time_low"] is not None

    bh = service.get_price_context("https://www.bhphotovideo.com/c/product/x", current_price=2398.0)
    assert bh["history_available"] is False
    assert "no history" in (bh["history_note"] or "").lower()


def test_handoff_includes_effective_price_and_policy(service):
    from product_intel.effective_price import OffersBook

    service.offers = OffersBook.from_dict(
        {
            "portals": [{"portal": "Rakuten", "merchant": "B&H", "rate": 4.0, "kind": "cash"}],
        }
    )
    t = service.start_tracking(
        label="cam",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2300.0,
    )
    b = service.stage_handoff(t["tracked_id"])["briefing"]
    ep = b["effective_price"]
    assert ep["effective"] < ep["sticker"]
    assert any(c["kind"] == "portal" for c in ep["components"])
    assert "case-by-case" in b["price_protection"]  # B&H policy
    assert "codes" in b["codes_to_try"]


def test_alert_payload_carries_effective_price(service):
    from product_intel.effective_price import OffersBook

    service.offers = OffersBook.from_dict(
        {"portals": [{"portal": "Rakuten", "merchant": "B&H", "rate": 4.0, "kind": "cash"}]}
    )
    t = service.start_tracking(
        label="cam",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2400.0,  # seed obs at 2398 fires Layer 1
    )
    alerts = service.store.alerts_for(t["tracked_id"])
    assert alerts and "effective_price" in alerts[0].payload
    assert "effective ~$" in alerts[0].message


def test_synthesize_reviews_through_service(service):
    out = service.synthesize_reviews(
        "Sony a7 IV", user_profile="parent shooting indoor volleyball weekly"
    )
    assert out["matched_segment"] is not None
    assert out["review_base"]["amazon"] == 0
    # contradiction landed in the shared store
    assert service.list_contradictions(open_only=True)["contradictions"]


def test_sweep_runs_all_layers(service):
    service.start_tracking(
        label="Sony a7 IV (body)",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=1000.0,
        category="ff-camera",
    )
    out = service.sweep()
    assert set(out) == {"price_checks", "layer3_alerts", "purchase_alerts"}
    # First sweep triggers the (fake) successor search pass for the Sony label.
    rules = [a["rule"] for a in out["layer3_alerts"]]
    assert "successor_signal" in rules


def test_purchase_flow_through_service(service):
    t = service.start_tracking(
        label="tv", merchant="Costco", url="https://www.costco.com/x", target_price=1.0
    )
    res = service.record_purchase("tv", "Costco", 1500.0, tracked_id=t["tracked_id"])
    assert "30 days" in res["price_protection"]
    st = service.purchase_status()["purchases"][0]
    assert st["protection_days_left"] > 0
    service.mark_warranty_registered(res["purchase_id"])
    assert service.purchase_status()["purchases"][0]["warranty_registered"] is True


def test_handoff_never_touches_session(service):
    t = service.start_tracking(
        label="cam",
        merchant="B&H",
        url="https://www.bhphotovideo.com/c/product/sony-a7-iv-body",
        target_price=2300.0,
    )
    h = service.stage_handoff(t["tracked_id"])
    assert h["executor"] == "manual_deeplink"
    assert "manually" in h["briefing"]["note"]
