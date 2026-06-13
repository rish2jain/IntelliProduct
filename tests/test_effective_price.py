from datetime import date

from product_intel.effective_price import OffersBook, compute_effective_price

BOOK = OffersBook.from_dict(
    {
        "point_values": {"AA": 1.6, "C1": 1.8},
        "portals": [
            {"portal": "Rakuten", "merchant": "B&H", "rate": 4.0, "kind": "cash"},
            {"portal": "AAdvantage eShopping", "merchant": "Dell", "rate": 6, "kind": "miles", "currency": "AA"},
        ],
        "card_offers": [
            {"card": "Amex Plat", "merchant": "B&H", "discount": 64.00, "min_spend": 600.00, "expires": "2026-06-30"},
        ],
        "earn_rates": [
            {"card": "Venture X", "merchant": "*", "rate": 2, "kind": "points", "currency": "C1"},
        ],
    }
)

ON = date(2026, 6, 13)


def test_doc_worked_example():
    # The design doc's alert: B&H $1,398 -> ~$1,278 after Rakuten 4% (~$56)
    # and the $64 Amex offer.
    ep = compute_effective_price(BOOK, "B&H", 139800, on=ON)
    assert ep.sticker_subunits == 139800
    # 4% of 1398.00 = 55.92 -> 5592; minus 6400 -> 127808 (~$1,278)
    assert ep.effective_subunits == 139800 - 5592 - 6400
    kinds = [c["kind"] for c in ep.components]
    assert kinds == ["portal", "card_offer"]
    assert "effective ~$1,278.08" in ep.summary()


def test_miles_portal_valued_at_cpp():
    # Dell $1,000 via 6x AA at 1.6cpp: 6000 miles * 1.6c = $96.
    ep = compute_effective_price(BOOK, "Dell", 100000, on=ON)
    portal = next(c for c in ep.components if c["kind"] == "portal")
    assert portal["value_subunits"] == 9600


def test_card_offer_min_spend_and_expiry():
    # Below min spend: offer does not apply.
    ep = compute_effective_price(BOOK, "B&H", 50000, on=ON)
    assert all(c["kind"] != "card_offer" for c in ep.components)
    # After expiry: offer does not apply.
    ep = compute_effective_price(BOOK, "B&H", 139800, on=date(2026, 7, 1))
    assert all(c["kind"] != "card_offer" for c in ep.components)


def test_earn_is_informational_not_subtracted():
    ep = compute_effective_price(BOOK, "B&H", 139800, on=ON)
    assert ep.earn is not None and ep.earn["card"] == "Venture X"
    # 2x C1 at 1.8cpp on $1,398 = $50.33 earn
    assert ep.earn["value_subunits"] == round(1398.0 * 2 * 1.8)
    # effective price ignores earn
    assert ep.effective_subunits == 139800 - 5592 - 6400


def test_unknown_merchant_sticker_stands():
    ep = compute_effective_price(BOOK, "Random Store", 100000, on=ON)
    assert ep.effective_subunits == 100000
    assert ep.components == []
    assert "no portal/offer stack" in ep.summary()


def test_empty_book_load(tmp_path):
    book = OffersBook.load(tmp_path / "missing.yaml")
    ep = compute_effective_price(book, "B&H", 100000, on=ON)
    assert ep.effective_subunits == 100000
    assert ep.notes  # explains the missing file


def test_yaml_roundtrip(tmp_path):
    p = tmp_path / "offers.yaml"
    p.write_text(
        "point_values:\n  AA: 1.6\n"
        "portals:\n  - portal: Rakuten\n    merchant: 'B&H'\n    rate: 4.0\n    kind: cash\n"
        "card_offers:\n  - card: Amex\n    merchant: 'B&H'\n    discount: 64.0\n    min_spend: 600.0\n    expires: 2026-06-30\n"
    )
    book = OffersBook.load(p)
    assert len(book.portals) == 1 and len(book.card_offers) == 1
    ep = compute_effective_price(book, "b&h", 139800, on=ON)
    assert ep.effective_subunits == 139800 - 5592 - 6400
