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
