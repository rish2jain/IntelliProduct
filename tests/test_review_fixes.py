"""Regression tests for the adversarial-review findings (second v3 pass)."""

import threading

import pytest

from product_intel.db import Store
from product_intel.effective_price import OffersBook, OffersError, compute_effective_price
from product_intel.models import Availability, PriceObservation, TrackedProduct, now_ts
from product_intel.purchases import check_purchases, policy_for, record_purchase

DAY = 86400


# ---- finding 1: offers loading must never crash the daemon ---------------
def test_yaml_syntax_error_is_offers_error(tmp_path):
    p = tmp_path / "offers.yaml"
    p.write_text("portals:\n\t- broken: [unclosed\n")
    with pytest.raises(OffersError, match="unreadable"):
        OffersBook.load(p)


def test_top_level_list_is_offers_error():
    with pytest.raises(OffersError, match="mapping"):
        OffersBook.from_dict([{"portal": "X"}])  # type: ignore[arg-type]


def test_bad_point_value_is_offers_error():
    with pytest.raises(OffersError, match="point_values"):
        OffersBook.from_dict({"point_values": {"AA": "one point six"}})


def test_service_survives_yaml_syntax_error(tmp_path):
    import os

    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    from product_intel.config import Config
    from product_intel.service import Service

    bad = tmp_path / "offers.yaml"
    bad.write_text(":\n\t- [broken yaml\n")
    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", tmp_path / "t.sqlite3")
    object.__setattr__(cfg, "offers_file", bad)
    svc = Service(config=cfg, store=Store(cfg.db_path))  # must not raise
    try:
        assert "error" in svc.reload_offers()
    finally:
        svc.close()


# ---- finding 15: missing point-value cross-reference ----------------------
def test_miles_portal_requires_point_value():
    with pytest.raises(OffersError, match="point_values"):
        OffersBook.from_dict(
            {"portals": [{"portal": "AA eShopping", "merchant": "Dell", "rate": 6,
                          "kind": "miles", "currency": "AA"}]}
        )


# ---- finding 14: one card pays — only the best card offer subtracts -------
def test_only_best_card_offer_subtracts():
    book = OffersBook.from_dict(
        {
            "card_offers": [
                {"card": "Amex Plat", "merchant": "B&H", "discount": 64.0},
                {"card": "CSR", "merchant": "B&H", "discount": 40.0},
                {"card": "Amex Plat", "merchant": "B&H", "discount": 50.0},
            ]
        }
    )
    ep = compute_effective_price(book, "B&H", 100000)
    offers = [c for c in ep.components if c["kind"] == "card_offer"]
    assert len(offers) == 1
    assert offers[0]["value_subunits"] == 6400
    assert ep.effective_subunits == 100000 - 6400
    assert any("not stacked" in n for n in ep.notes)


# ---- finding 5: policy fallback must not misfire on prefixes --------------
def test_policy_prefix_word_boundary():
    assert policy_for("Targeted Deals").protection_note == "no known price-protection policy"
    assert policy_for("amazon uk").price_protection_days == 0  # legit prefix
    assert policy_for("best buy outlet").price_protection_days == 15


# ---- finding 2: price protection ignores pre-purchase observations --------
def test_protection_ignores_pre_purchase_dip():
    s = Store(":memory:")
    tid = s.add_tracked(
        TrackedProduct(label="tv", merchant="Costco", url="u", target_price_subunits=1)
    )
    # Pre-purchase dip to $1,400, frozen in history.
    s.conn.execute(
        "INSERT INTO price_observations (tracked_product_id, observed_at, price_subunits,"
        " currency, availability, source) VALUES (?,?,?,?,?,?)",
        (tid, now_ts() - 10 * DAY, 140000, "USD", "in_stock", "test"),
    )
    s.conn.commit()
    record_purchase(s, "tv", "Costco", 150000, tracked_product_id=tid,
                    purchased_at=now_ts() - 5 * DAY)
    # No post-purchase observation below paid -> no claim alert.
    assert all(a.rule != "price_protection_opportunity" for a in check_purchases(s))
    # A genuine post-purchase drop still fires.
    s.add_observation(PriceObservation(tid, 139000, "USD", Availability.IN_STOCK, "test"))
    assert any(a.rule == "price_protection_opportunity" for a in check_purchases(s))


# ---- finding 3: contradiction dedup survives the firmware cycle -----------
def test_contradiction_dedup_covers_update_reported_and_races():
    s = Store(":memory:")
    cid = s.add_contradiction("cam", "battery", "a", "s1", "b", "s2")
    assert cid is not None
    assert s.add_contradiction("cam", "battery", "a2", "s3", "b2", "s4") is None
    s.set_contradiction_status(cid, "update_reported")
    # Still live -> still deduped (no open/update_reported duplicate loop).
    assert s.add_contradiction("cam", "battery", "a3", "s5", "b3", "s6") is None
    # Resolved records don't block re-detection on new evidence.
    s.set_contradiction_status(cid, "resolved")
    assert s.add_contradiction("cam", "battery", "a4", "s7", "b4", "s8") is not None


# ---- finding 6: layer-1 re-arm hysteresis ----------------------------------
def test_layer1_no_rearm_within_hysteresis_band():
    from product_intel.alerts.rules import evaluate
    from product_intel.models import PriceHistory
    from product_intel.workers.price_context import PriceContext

    def ctx(cur):
        return PriceContext(
            current_subunits=cur,
            history=PriceHistory(source="x", available=False, note="none"),
        )

    def ob(*prices):
        return [PriceObservation(1, p, "USD", Availability.IN_STOCK, "t") for p in prices]

    t = TrackedProduct(label="x", merchant="m", url="u", target_price_subunits=100000, id=1)
    _, last = evaluate(t, ob(99999), ctx(99999))
    t.last_alert_price_subunits = last
    # Oscillates a penny above target: within the 3% band -> stays armed-off.
    _, last = evaluate(t, ob(99999, 100001), ctx(100001))
    assert "layer1_threshold" in last
    t.last_alert_price_subunits = last
    _, alerts_last = evaluate(t, ob(99999, 100001, 99999), ctx(99999))
    # Back to the same price: deduped, no spam.
    alerts, _ = evaluate(t, ob(99999, 100001, 99999), ctx(99999))
    assert alerts == []


# ---- findings 7/8: market dedup episodes ------------------------------------
def test_competitor_drift_same_percent_no_repeat():
    from product_intel.market import MarketScanner, ScanParams

    s = Store(":memory:")
    a = s.add_tracked(TrackedProduct(label="A", merchant="m", url="u1",
                                     target_price_subunits=1, category="c"))
    b = s.add_tracked(TrackedProduct(label="B", merchant="m", url="u2",
                                     target_price_subunits=1, category="c"))
    for p in (100000, 100000, 100000):
        s.add_observation(PriceObservation(a, p, "USD", Availability.IN_STOCK, "t"))
    for p in (100000, 100000, 91000):
        s.add_observation(PriceObservation(b, p, "USD", Availability.IN_STOCK, "t"))
    scanner = MarketScanner(s, None, ScanParams())
    first = scanner.scan()
    assert any(x.rule == "competitor_price_move" for x in first)
    # Drifts a few cents within the same whole-percent drop: no new alert.
    s.add_observation(PriceObservation(b, 90990, "USD", Availability.IN_STOCK, "t"))
    assert all(x.rule != "competitor_price_move" for x in scanner.scan())
    # A materially deeper drop is a new event.
    s.add_observation(PriceObservation(b, 85000, "USD", Availability.IN_STOCK, "t"))
    assert any(x.rule == "competitor_price_move" for x in scanner.scan())


def test_discontinuation_new_episode_realerts():
    from product_intel.market import MarketScanner, ScanParams

    s = Store(":memory:")
    tid = s.add_tracked(TrackedProduct(label="X", merchant="m", url="u",
                                       target_price_subunits=1))

    def add(price, avail, at):
        s.conn.execute(
            "INSERT INTO price_observations (tracked_product_id, observed_at,"
            " price_subunits, currency, availability, source) VALUES (?,?,?,?,?,?)",
            (tid, at, price, "USD", avail, "t"),
        )
        s.conn.commit()

    t0 = now_ts() - 30 * DAY
    scanner = MarketScanner(s, None, ScanParams())
    add(100, "in_stock", t0)
    add(100, "out_of_stock", t0 + 1 * DAY)
    assert any(a.rule == "discontinuation_signal" for a in scanner.scan())
    assert all(a.rule != "discontinuation_signal" for a in scanner.scan())  # same episode
    # Recovers, then goes out of stock again months later: a NEW episode.
    add(100, "in_stock", t0 + 10 * DAY)
    add(100, "out_of_stock", t0 + 25 * DAY)
    assert any(a.rule == "discontinuation_signal" for a in scanner.scan())


# ---- findings 9/10: claim extraction regressions ----------------------------
def test_light_keyword_word_boundaries():
    from product_intel.reviews.llm import HeuristicLLM
    from product_intel.reviews.models import ReviewDoc

    llm = HeuristicLLM()
    # "slight" must not trigger the weight aspect.
    none = llm.extract_claims(
        ReviewDoc(source="reddit", text="There is a slight problem with the lens cap.")
    )
    assert all(c.aspect != "weight" for c in none)
    # Clause-final "light" must be found.
    found = llm.extract_claims(
        ReviewDoc(source="reddit", text="For travel the camera is great because it is light")
    )
    assert any(c.aspect == "weight" and c.sentiment == 1 for c in found)


def test_decimal_numbers_do_not_split_clauses():
    from product_intel.reviews.llm import HeuristicLLM
    from product_intel.reviews.models import ReviewDoc

    llm = HeuristicLLM()
    claims = llm.extract_claims(
        ReviewDoc(source="reddit", text="The battery lasts 3.5 hours and drains quickly in video.")
    )
    # Before the fix, "3.5" split the clause, separating "battery" from
    # "drains" and losing the claim entirely.
    assert any(c.aspect == "battery" and c.sentiment == -1 for c in claims)


# ---- finding 11: store usable across threads --------------------------------
def test_store_usable_from_worker_thread(tmp_path):
    s = Store(tmp_path / "threads.sqlite3")
    s.add_tracked(TrackedProduct(label="x", merchant="m", url="u", target_price_subunits=1))
    errors: list[Exception] = []

    def use():
        try:
            assert len(s.all_tracked()) == 1
            s.set_meta("thread_check", "ok")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    th = threading.Thread(target=use)
    th.start()
    th.join()
    assert errors == []
    assert s.get_meta("thread_check") == "ok"
    s.close()


# ---- finding 16: record_purchase validates tracked_id -----------------------
def test_record_purchase_rejects_unknown_tracked_id(tmp_path):
    import os

    os.environ["PRODUCT_INTEL_FAKE"] = "1"
    from product_intel.config import Config
    from product_intel.service import Service

    cfg = Config.from_env()
    object.__setattr__(cfg, "db_path", tmp_path / "t.sqlite3")
    svc = Service(config=cfg, store=Store(cfg.db_path))
    try:
        assert "error" in svc.record_purchase("x", "Costco", 100.0, tracked_id=999)
    finally:
        svc.close()
