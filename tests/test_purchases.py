from product_intel.db import Store
from product_intel.models import Availability, PriceObservation, TrackedProduct, now_ts
from product_intel.purchases import (
    check_purchases,
    policy_for,
    purchase_status,
    record_purchase,
)

DAY = 86400


def test_policy_table_corrections():
    assert policy_for("Amazon").price_protection_days == 0  # eliminated, no false hope
    assert policy_for("Best Buy").price_protection_days == 15
    assert policy_for("Target").price_protection_days == 14
    assert policy_for("Costco").price_protection_days == 30
    assert policy_for("B&H").price_protection_days is None  # case-by-case
    assert policy_for("Some Web Shop").price_protection_days == 0


def test_no_policy_notified_once_and_stops():
    s = Store(":memory:")
    res = record_purchase(s, "cam", "Amazon", 200000)
    assert "monitor will not generate price-protection alerts" in res["price_protection"]
    # Monitor never produces protection alerts for it.
    alerts = check_purchases(s, now=now_ts())
    assert all(a.rule != "price_protection_opportunity" for a in alerts)


def test_protection_opportunity_fires_and_dedups():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="tv", merchant="Costco", url="u", target_price_subunits=1)
    )
    bought = now_ts() - 5 * DAY  # inside Costco's 30-day window
    record_purchase(s, "tv", "Costco", 150000, tracked_product_id=tid, purchased_at=bought)
    s.add_observation(PriceObservation(tid, 140000, "USD", Availability.IN_STOCK, "rye"))

    alerts = check_purchases(s)
    assert any(a.rule == "price_protection_opportunity" for a in alerts)
    assert "claim $100.00" in alerts[0].message

    # Same price again: dedup'd via market_events.
    assert check_purchases(s) == []
    # A further drop is a new event.
    s.add_observation(PriceObservation(tid, 130000, "USD", Availability.IN_STOCK, "rye"))
    again = check_purchases(s)
    assert any(a.rule == "price_protection_opportunity" for a in again)


def test_protection_window_expired_is_silent():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="tv", merchant="Costco", url="u", target_price_subunits=1)
    )
    bought = now_ts() - 45 * DAY  # outside the 30-day window
    record_purchase(s, "tv", "Costco", 150000, tracked_product_id=tid, purchased_at=bought)
    s.add_observation(PriceObservation(tid, 100000, "USD", Availability.IN_STOCK, "rye"))
    alerts = check_purchases(s)
    assert all(a.rule != "price_protection_opportunity" for a in alerts)


def test_return_window_closing_fires_once():
    s = Store(":memory:")
    bought = now_ts() - 28 * DAY  # Amazon return_days=30 -> 2 days left
    record_purchase(s, "cam", "Amazon", 200000, purchased_at=bought)
    alerts = check_purchases(s)
    assert any(a.rule == "return_window_closing" for a in alerts)
    assert check_purchases(s) == []  # once


def test_purchase_status_reports_windows():
    s = Store(":memory:")
    record_purchase(s, "cam", "Best Buy", 99900)
    st = purchase_status(s)[0]
    assert st["merchant"] == "Best Buy"
    assert st["return_days_left"] > 0
    assert st["protection_days_left"] > 0
    assert st["warranty_registered"] is False
