"""Tests for the v3 design-review refinements (docs/design-review-v3.md)."""

import os
import time

from product_intel.db import SCHEMA_VERSION, Store
from product_intel.effective_price import OffersBook, OffersError, compute_effective_price
from product_intel.executors import executor_for
from product_intel.merchants import canonical, same_merchant
from product_intel.purchases import policy_for


# ---- §1.1 re-arm -------------------------------------------------------
def test_layer1_rearm_after_price_exits_region():
    from product_intel.alerts.rules import evaluate
    from product_intel.models import (
        Availability,
        PriceHistory,
        PriceObservation,
        TrackedProduct,
    )
    from product_intel.workers.price_context import PriceContext

    def ctx(cur):
        return PriceContext(
            current_subunits=cur,
            history=PriceHistory(source="x", available=False, note="none"),
        )

    def ob(*prices):
        return [PriceObservation(1, p, "USD", Availability.IN_STOCK, "t") for p in prices]

    t = TrackedProduct(label="cam", merchant="B&H", url="u", target_price_subunits=230000, id=1)

    # Alert at 227900.
    alerts, last = evaluate(t, ob(227900), ctx(227900))
    assert any(a.layer.value == "layer1_threshold" for a in alerts)
    t.last_alert_price_subunits = last

    # Rebound above target: no alert, but the layer re-arms (state changes).
    alerts, last = evaluate(t, ob(227900, 245000), ctx(245000))
    assert alerts == []
    assert "layer1_threshold" not in last
    t.last_alert_price_subunits = last

    # Back below target at a WORSE price than the first alert: fires again.
    alerts, last = evaluate(t, ob(227900, 245000, 229900), ctx(229900))
    assert any(a.layer.value == "layer1_threshold" for a in alerts)
    assert last["layer1_threshold"] == 229900


def test_layer2_rearm_after_rebound():
    from product_intel.alerts.rules import RuleParams, evaluate
    from product_intel.models import (
        Availability,
        PriceObservation,
        TrackedProduct,
    )
    from product_intel.models import PriceHistory
    from product_intel.workers.price_context import PriceContext

    def hist_ctx(cur, pct):
        return PriceContext(
            current_subunits=cur,
            history=PriceHistory(source="keepa", available=True, points=[(0, 200000)]),
            amazon_all_time_low_subunits=200000,
            percentile=pct,
        )

    def ob(*prices):
        return [PriceObservation(1, p, "USD", Availability.IN_STOCK, "t") for p in prices]

    t = TrackedProduct(label="cam", merchant="Amazon", url="u", target_price_subunits=1, id=1)
    params = RuleParams(rearm_fraction=0.03)

    alerts, last = evaluate(t, ob(205000), hist_ctx(205000, 10.0), params)
    assert any(a.rule == "near_all_time_low" for a in alerts)
    t.last_alert_price_subunits = last

    # Rebounds >3% above the alerted price: layer re-arms.
    alerts, last = evaluate(t, ob(205000, 215000), hist_ctx(215000, 60.0), params)
    assert "layer2_relative_value" not in last
    t.last_alert_price_subunits = last

    # Dips near the low again at a price above the first alert: fires again.
    alerts, last = evaluate(t, ob(205000, 215000, 207000), hist_ctx(207000, 12.0), params)
    assert any(a.rule == "near_all_time_low" for a in alerts)


def test_rearm_state_persists_through_service_without_alerts(tmp_path):
    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    from product_intel.config import Config
    from product_intel.service import Service

    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", tmp_path / "t.sqlite3")
    svc = Service(config=cfg, store=Store(cfg.db_path))
    try:
        url = "https://www.bhphotovideo.com/c/product/sony-a7-iv-body"
        # Fake B&H price is 2398; target 2400 -> Layer 1 fires on the seed obs.
        t = svc.start_tracking("cam", "B&H", url, 2400.0)
        tid = t["tracked_id"]
        assert svc.store.get_tracked(tid).last_alert_price_subunits  # armed

        # The monitor observes a rebound above target: no alert fires, but the
        # dedup state must be cleared AND persisted by that alert-free check.
        live = svc._normalizer._by_url[url]
        live.price_subunits = 260000
        res = svc.check_once(tid)
        assert res["alerts_fired"] == []
        assert "layer1_threshold" not in svc.store.get_tracked(tid).last_alert_price_subunits

        # Price returns below target — a re-alert despite not beating the
        # original $2,398 alert price (it equals it).
        live.price_subunits = 239800
        res = svc.check_once(tid)
        assert any(a["layer"] == "layer1_threshold" for a in res["alerts_fired"])
    finally:
        svc.close()


# ---- §1.2 canonical merchants ------------------------------------------
def test_canonical_aliases():
    assert canonical("B&H Photo") == "b&h"
    assert canonical("bhphotovideo.com") == "b&h"
    assert canonical("Adorama (used)") == "adorama"
    assert canonical("BestBuy") == "best buy"
    assert canonical("Amazon.com") == "amazon"
    assert canonical("Some Unknown Shop") == "some unknown shop"
    assert same_merchant("B&H", "B&H Photo Video")


def test_offers_join_across_merchant_spellings():
    book = OffersBook.from_dict(
        {"portals": [{"portal": "Rakuten", "merchant": "B&H Photo", "rate": 4.0}]}
    )
    ep = compute_effective_price(book, "bhphotovideo.com", 100000)
    assert any(c["kind"] == "portal" for c in ep.components)


def test_policy_for_uses_aliases():
    assert policy_for("bhphotovideo.com").price_protection_days is None  # B&H case-by-case
    assert policy_for("BestBuy.com").price_protection_days == 15


# ---- §1.3 offers validation + staleness ---------------------------------
def test_malformed_offers_reports_entry():
    import pytest

    with pytest.raises(OffersError) as e:
        OffersBook.from_dict(
            {
                "portals": [
                    {"portal": "Rakuten", "merchant": "B&H", "rate": 4.0},
                    {"portal": "Broken", "merchant": "Dell"},  # missing rate
                ],
                "card_offers": [{"card": "Amex", "merchant": "B&H"}],  # missing discount
            }
        )
    msg = str(e.value)
    assert "portals[1]" in msg and "rate" in msg
    assert "card_offers[0]" in msg and "discount" in msg


def test_bad_kind_rejected():
    import pytest

    with pytest.raises(OffersError, match="kind"):
        OffersBook.from_dict(
            {"portals": [{"portal": "X", "merchant": "Y", "rate": 1, "kind": "crypto"}]}
        )


def test_staleness_flag(tmp_path):
    p = tmp_path / "offers.yaml"
    p.write_text("portals:\n  - portal: Rakuten\n    merchant: 'B&H'\n    rate: 4.0\n")
    book = OffersBook.load(p)
    assert book.stale() is False
    old = time.time() - 10 * 86400
    os.utime(p, (old, old))
    assert OffersBook.load(p).stale() is True


def test_service_survives_malformed_offers_file(tmp_path):
    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    from product_intel.config import Config
    from product_intel.service import Service

    bad = tmp_path / "offers.yaml"
    bad.write_text("portals:\n  - portal: X\n    merchant: Y\n")  # missing rate
    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", tmp_path / "t.sqlite3")
    object.__setattr__(cfg, "offers_file", bad)
    svc = Service(config=cfg, store=Store(cfg.db_path))  # must not raise
    try:
        assert svc.offers.portals == []
        res = svc.reload_offers()
        assert "error" in res and "portals[0]" in res["error"]
    finally:
        svc.close()


# ---- §1.5 executor seam --------------------------------------------------
def test_executor_defaults_to_manual():
    assert executor_for("B&H") == "manual_deeplink"


def test_executor_env_opt_in(monkeypatch):
    monkeypatch.setenv("PRODUCT_INTEL_ACP_MERCHANTS", "Etsy, B&H Photo")
    assert executor_for("bhphotovideo.com") == "acp_checkout"
    assert executor_for("etsy") == "acp_checkout"
    assert executor_for("Amazon") == "manual_deeplink"


# ---- §1.6 heartbeat / health ---------------------------------------------
def test_monitor_health_heartbeat(tmp_path):
    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    from product_intel.config import Config
    from product_intel.service import Service

    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", tmp_path / "t.sqlite3")
    svc = Service(config=cfg, store=Store(cfg.db_path))
    try:
        h = svc.monitor_health()
        assert h["overdue"] is True and h["note"]  # never swept
        svc.sweep()
        h = svc.monitor_health()
        assert h["overdue"] is False
        assert h["last_sweep_age_seconds"] is not None
    finally:
        svc.close()


# ---- §1.7 / §1.8 store: WAL + versioned migrations -----------------------
def test_wal_and_busy_timeout_on_file_db(tmp_path):
    s = Store(tmp_path / "wal.sqlite3")
    mode = s.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    timeout = s.conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000
    s.close()


def test_schema_version_recorded(tmp_path):
    s = Store(tmp_path / "v.sqlite3")
    assert int(s.get_meta("schema_version")) == SCHEMA_VERSION
    s.close()
    # Reopening is a no-op, not a re-migration failure.
    s2 = Store(tmp_path / "v.sqlite3")
    assert int(s2.get_meta("schema_version")) == SCHEMA_VERSION
    s2.close()


# ---- §1.4 contradiction resolution ---------------------------------------
def test_resolve_contradiction_through_service(tmp_path):
    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    from product_intel.config import Config
    from product_intel.service import Service

    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", tmp_path / "t.sqlite3")
    svc = Service(config=cfg, store=Store(cfg.db_path))
    try:
        svc.synthesize_reviews("Sony a7 IV", product_label="Sony a7 IV")
        open_recs = svc.list_contradictions(open_only=True)["contradictions"]
        assert open_recs
        cid = open_recs[0]["id"]
        assert svc.resolve_contradiction(cid, "dismissed", "verified: not reproducible")["status"] == "dismissed"
        assert svc.list_contradictions(open_only=True)["contradictions"] == []
        assert "error" in svc.resolve_contradiction(cid, "bogus-status")

        # tracking_status surfaces open contradiction counts by label.
        svc.synthesize_reviews("Sony a7 IV", product_label="Sony a7 IV (body)")
        t = svc.start_tracking(
            "Sony a7 IV (body)", "B&H",
            "https://www.bhphotovideo.com/c/product/sony-a7-iv-body", 2300.0,
        )
        st = svc.tracking_status(t["tracked_id"])["tracked"][0]
        assert st["open_contradictions"] == 1
    finally:
        svc.close()
