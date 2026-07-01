from product_intel.clients.fakes import FakeWebSearchClient
from product_intel.db import Store
from product_intel.market import MarketScanner, ScanParams
from product_intel.models import Availability, PriceObservation, TrackedProduct


def obs(store, tid, *prices, availability=Availability.IN_STOCK):
    for p in prices:
        store.add_observation(PriceObservation(tid, p, "USD", availability, "test"))


def make(store, search=None):
    return MarketScanner(store, search, ScanParams())


def test_discontinuation_signal_and_dedup():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="Sony a7 IV", merchant="B&H", url="u", target_price_subunits=1)
    )
    obs(s, tid, 239800)
    s.add_observation(
        PriceObservation(tid, 239800, "USD", Availability.OUT_OF_STOCK, "test")
    )
    scanner = make(s)
    alerts = scanner.scan(force=False)
    assert any(a.rule == "discontinuation_signal" for a in alerts)
    assert all(a.rule != "discontinuation_signal" for a in scanner.scan())  # dedup'd


def test_competitor_move_within_category():
    s = Store(":memory:")
    a = s.add_tracked(
        TrackedProduct(label="Sony a7 IV", merchant="B&H", url="u1",
                       target_price_subunits=1, category="ff-camera")
    )
    b = s.add_tracked(
        TrackedProduct(label="Canon R6 II", merchant="Adorama", url="u2",
                       target_price_subunits=1, category="ff-camera")
    )
    obs(s, a, 239800, 239800, 239800)
    obs(s, b, 219900, 219900, 199000)  # ~9.5% under trailing median

    alerts = make(s).scan()
    comp = [x for x in alerts if x.rule == "competitor_price_move"]
    assert len(comp) == 1
    assert comp[0].tracked_product_id == a
    assert "Canon R6 II" in comp[0].message


def test_no_competitor_alert_across_categories():
    s = Store(":memory:")
    a = s.add_tracked(
        TrackedProduct(label="cam", merchant="m", url="u1",
                       target_price_subunits=1, category="cameras")
    )
    b = s.add_tracked(
        TrackedProduct(label="handheld", merchant="m", url="u2",
                       target_price_subunits=1, category="gaming")
    )
    obs(s, a, 100000, 100000, 100000)
    obs(s, b, 80000, 80000, 60000)
    alerts = make(s).scan()
    assert all(x.rule != "competitor_price_move" for x in alerts)


def test_successor_pass_weekly_cadence():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="Sony a7 IV", merchant="B&H", url="u", target_price_subunits=1)
    )
    obs(s, tid, 239800, 239800)
    scanner = make(s, FakeWebSearchClient())

    alerts = scanner.scan()  # first ever scan -> search pass due
    assert any(a.rule == "successor_signal" for a in alerts)
    # Immediately after: cadence not due, and the event is dedup'd anyway.
    assert scanner.scan() == []
    # Forced re-scan still dedups the same successor URL.
    assert all(a.rule != "successor_signal" for a in scanner.scan(force=True))


def test_firmware_pass_checks_open_contradictions():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="Sony a7 IV", merchant="B&H", url="u", target_price_subunits=1)
    )
    obs(s, tid, 239800)
    cid = s.add_contradiction(
        "Sony a7 IV", "battery",
        "battery drains after 8 months", "reddit:gym_dad",
        "battery life is excellent", "reddit:trail_shooter",
    )
    scanner = make(s, FakeWebSearchClient())
    alerts = scanner.scan(force=True)
    fw = [a for a in alerts if a.rule == "firmware_or_recall_signal"]
    assert len(fw) == 1
    assert f"ledger #{cid}" in fw[0].message
    # A snippet hit is weak evidence: the record stays OPEN (a human verifies
    # via resolve_contradiction); the hit is attached as a note.
    rec = s.contradictions("Sony a7 IV", open_only=True)[0]
    assert rec["status"] == "open"
    assert "possible fix reported" in rec["resolution_note"]
    # Same URL never re-alerts, even across forced re-scans.
    again = scanner.scan(force=True)
    assert all(a.rule != "firmware_or_recall_signal" for a in again)


def test_no_search_backend_no_crash():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="x", merchant="m", url="u", target_price_subunits=1)
    )
    obs(s, tid, 100)
    assert make(s, None).scan(force=True) == []
