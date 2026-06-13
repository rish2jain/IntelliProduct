from product_intel.alerts.rules import RuleParams, evaluate
from product_intel.models import (
    Availability,
    PriceHistory,
    PriceObservation,
    TrackedProduct,
)
from product_intel.workers.price_context import PriceContext


def obs(tid, *prices):
    return [
        PriceObservation(tid, pr, "USD", Availability.IN_STOCK, "test") for pr in prices
    ]


def tracked(target, last_alert=None):
    t = TrackedProduct(
        label="cam", merchant="B&H", url="u", target_price_subunits=target, id=1
    )
    if last_alert:
        t.last_alert_price_subunits = last_alert
    return t


def no_history_ctx(current):
    return PriceContext(
        current_subunits=current,
        history=PriceHistory(source="x", available=False, note="no history"),
    )


def history_ctx(current, percentile, atl=200000):
    return PriceContext(
        current_subunits=current,
        history=PriceHistory(source="keepa", available=True, points=[(0, atl), (1, 260000)]),
        amazon_all_time_low_subunits=atl,
        percentile=percentile,
    )


# ---- Layer 1 ----------------------------------------------------------
def test_layer1_fires_at_target():
    t = tracked(target=230000)
    alerts, last = evaluate(t, obs(1, 227900), no_history_ctx(227900))
    layers = [a.layer.value for a in alerts]
    assert "layer1_threshold" in layers
    assert last["layer1_threshold"] == 227900


def test_layer1_silent_above_target():
    t = tracked(target=230000)
    alerts, _ = evaluate(t, obs(1, 239900), no_history_ctx(239900))
    assert all(a.layer.value != "layer1_threshold" for a in alerts)


def test_layer1_dedup_no_improvement():
    t = tracked(target=230000, last_alert={"layer1_threshold": 227900})
    alerts, _ = evaluate(t, obs(1, 227900), no_history_ctx(227900))
    assert all(a.layer.value != "layer1_threshold" for a in alerts)


def test_layer1_refires_on_improvement():
    t = tracked(target=230000, last_alert={"layer1_threshold": 227900})
    alerts, last = evaluate(t, obs(1, 224900), no_history_ctx(224900))
    assert any(a.layer.value == "layer1_threshold" for a in alerts)
    assert last["layer1_threshold"] == 224900


# ---- Layer 2 (history available) -------------------------------------
def test_layer2_near_all_time_low():
    t = tracked(target=100000)  # well below price, so layer1 silent
    alerts, _ = evaluate(t, obs(1, 205000), history_ctx(205000, percentile=10.0))
    rules = [a.rule for a in alerts]
    assert "near_all_time_low" in rules


def test_layer2_history_high_percentile_silent():
    t = tracked(target=100000)
    alerts, _ = evaluate(t, obs(1, 255000), history_ctx(255000, percentile=80.0))
    assert all(a.layer.value != "layer2_relative_value" for a in alerts)


# ---- Layer 2 (cold start) --------------------------------------------
def test_layer2_cold_start_needs_min_observations():
    t = tracked(target=100000)
    params = RuleParams(cold_start_min_observations=5)
    # only 3 observations -> not enough history yet
    alerts, _ = evaluate(t, obs(1, 250000, 248000, 240000), no_history_ctx(240000), params)
    assert all(a.layer.value != "layer2_relative_value" for a in alerts)


def test_layer2_cold_start_new_low():
    t = tracked(target=100000)
    series = obs(1, 250000, 252000, 249000, 251000, 248000, 244000)  # last is new low
    alerts, _ = evaluate(t, series, no_history_ctx(244000))
    assert any(a.rule == "cold_start_new_low" for a in alerts)


def test_layer2_cold_start_below_median():
    t = tracked(target=100000)
    # prior = [250000,252000,249000,251000,230000]; median 250000, prior low 230000.
    # current 235000 is NOT a new low but is 6% under median -> below_median fires.
    series = obs(1, 250000, 252000, 249000, 251000, 230000, 235000)
    params = RuleParams(cold_start_drop_fraction=0.05)
    alerts, _ = evaluate(t, series, no_history_ctx(235000), params)
    rules = [a.rule for a in alerts]
    assert "cold_start_below_median" in rules
    assert "cold_start_new_low" not in rules


def test_no_alerts_on_empty_observations():
    t = tracked(target=100000)
    alerts, last = evaluate(t, [], no_history_ctx(0))
    assert alerts == []
