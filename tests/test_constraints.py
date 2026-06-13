from product_intel.models import Availability, Product
from product_intel.workers.constraints import Constraints, filter_candidates, violations


def p(**kw):
    base = dict(
        title="t",
        url="u",
        merchant="B&H",
        price_subunits=200000,
        availability=Availability.IN_STOCK,
        purchasable=True,
    )
    base.update(kw)
    return Product(**base)


def test_budget_bounds():
    c = Constraints(budget_min_subunits=100000, budget_max_subunits=250000)
    assert violations(p(price_subunits=200000), c) == []
    assert "over budget max" in violations(p(price_subunits=300000), c)[0]
    assert "below budget min" in violations(p(price_subunits=50000), c)[0]


def test_purchasable_and_stock():
    c = Constraints(require_purchasable=True, require_in_stock=True)
    assert violations(p(), c) == []
    assert violations(p(purchasable=False), c)
    assert violations(p(availability=Availability.OUT_OF_STOCK), c)


def test_merchant_allow_block():
    allow = Constraints(allowed_merchants=frozenset({"B&H"}))
    assert violations(p(merchant="B&H"), allow) == []
    assert violations(p(merchant="Amazon"), allow)

    block = Constraints(blocked_merchants=frozenset({"Amazon"}))
    assert violations(p(merchant="Amazon"), block)


def test_required_attributes():
    c = Constraints(required_attributes={"kit": "body-only"})
    assert violations(p(attributes={"kit": "body-only"}), c) == []
    assert violations(p(attributes={"kit": "with-lens"}), c)


def test_filter_candidates():
    c = Constraints(budget_max_subunits=250000)
    items = [p(price_subunits=200000), p(price_subunits=300000)]
    assert len(filter_candidates(items, c)) == 1
