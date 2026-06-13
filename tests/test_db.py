from product_intel.db import Store
from product_intel.models import (
    Alert,
    AlertLayer,
    Availability,
    PriceObservation,
    Product,
    TrackedProduct,
)


def make_store():
    return Store(":memory:")


def test_intent_and_candidates_roundtrip():
    s = make_store()
    intent_id = s.create_intent("camera", category="cameras", budget_max_subunits=250000)
    p = Product(
        title="Sony a7 IV",
        url="https://x/y",
        merchant="B&H",
        price_subunits=239800,
        availability=Availability.IN_STOCK,
        purchasable=True,
        brand="Sony",
        rye_product_id="rye1",
        attributes={"kit": "body-only"},
    )
    ids = s.add_candidates(intent_id, [p])
    assert len(ids) == 1
    got = s.candidates_for_intent(intent_id)
    assert got[0].title == "Sony a7 IV"
    assert got[0].availability is Availability.IN_STOCK
    assert got[0].attributes["kit"] == "body-only"


def test_tracking_observations_and_alerts():
    s = make_store()
    tid = s.add_tracked(
        TrackedProduct(label="cam", merchant="B&H", url="u", target_price_subunits=230000)
    )
    s.add_observation(
        PriceObservation(tid, 239800, "USD", Availability.IN_STOCK, "rye")
    )
    s.add_observation(
        PriceObservation(tid, 227900, "USD", Availability.IN_STOCK, "rye")
    )
    obs = s.observations(tid)
    assert [o.price_subunits for o in obs] == [239800, 227900]
    assert s.latest_observation(tid).price_subunits == 227900

    s.add_alert(
        Alert(
            tracked_product_id=tid,
            layer=AlertLayer.THRESHOLD,
            rule="price_at_or_below_target",
            message="hit",
            price_subunits=227900,
            deep_link="u",
        )
    )
    alerts = s.alerts_for(tid)
    assert len(alerts) == 1 and alerts[0].layer is AlertLayer.THRESHOLD


def test_last_alert_state_persists():
    s = make_store()
    tid = s.add_tracked(TrackedProduct(label="x", merchant="m", url="u", target_price_subunits=1))
    s.update_last_alert(tid, {"layer1_threshold": 227900})
    assert s.get_tracked(tid).last_alert_price_subunits == {"layer1_threshold": 227900}


def test_set_tracking_active():
    s = make_store()
    tid = s.add_tracked(TrackedProduct(label="x", merchant="m", url="u", target_price_subunits=1))
    assert len(s.active_tracked()) == 1
    s.set_tracking_active(tid, False)
    assert len(s.active_tracked()) == 0
    assert len(s.all_tracked()) == 1
