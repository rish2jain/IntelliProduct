"""Phase B: effective price, not sticker price (Section 4).

The actual cost of a $1,500 camera varies $150–$300 by routing:

- **Shopping portals** (Rakuten / AAdvantage eShopping / ...): cash-% or
  miles-per-dollar, valued at the user's cents-per-point.
- **Card-linked offers** (Amex/Chase Offers): fixed rebates with a minimum
  spend and an expiry, rotating monthly.
- **Category multipliers**: which card earns most at this merchant — reported
  as informational earn, *not* subtracted (matching the doc's worked example:
  effective = sticker − portal − card offers).
- **Coupon codes**: best-effort "codes to try" surfaced at handoff time, never
  auto-applied (no reliable public aggregator API exists).

The offer data is deliberately *manual*: "a small YAML of active offers
refreshed weekly is honest and sufficient." Amex Offers have no API, and
scraping a logged-in Amex session is the same legal posture as scraping
Amazon. All money math stays in integer subunits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class PortalOffer:
    portal: str
    merchant: str
    rate: float  # percent (cash) or miles-per-dollar (miles)
    kind: str = "cash"  # "cash" | "miles"
    currency: Optional[str] = None  # points currency for kind="miles", e.g. "AA"
    note: Optional[str] = None

    def value_subunits(self, sticker_subunits: int, point_values_cpp: dict[str, float]) -> int:
        if self.kind == "cash":
            return round(sticker_subunits * self.rate / 100.0)
        cpp = point_values_cpp.get(self.currency or "", 1.0)  # cents per point
        # miles earned = dollars * rate; value cents = miles * cpp
        return round(sticker_subunits / 100.0 * self.rate * cpp)


@dataclass(frozen=True)
class CardOffer:
    card: str
    merchant: str
    discount_subunits: int
    min_spend_subunits: int = 0
    expires: Optional[date] = None  # None == no stated expiry
    note: Optional[str] = None

    def applies(self, sticker_subunits: int, on: date) -> bool:
        if sticker_subunits < self.min_spend_subunits:
            return False
        return self.expires is None or on <= self.expires


@dataclass(frozen=True)
class EarnRate:
    """Category multiplier: points/cash earned per dollar on a given card.

    Informational — reported alongside the effective price, not subtracted.
    ``merchant="*"`` is the catch-all card.
    """

    card: str
    merchant: str  # specific merchant or "*"
    rate: float  # points per dollar, or percent for kind="cash"
    kind: str = "points"  # "points" | "cash"
    currency: Optional[str] = None

    def value_subunits(self, sticker_subunits: int, point_values_cpp: dict[str, float]) -> int:
        if self.kind == "cash":
            return round(sticker_subunits * self.rate / 100.0)
        cpp = point_values_cpp.get(self.currency or "", 1.0)
        return round(sticker_subunits / 100.0 * self.rate * cpp)


@dataclass
class OffersBook:
    """The manual offer YAML, parsed. Empty book == sticker price stands."""

    point_values_cpp: dict[str, float] = field(default_factory=dict)
    portals: list[PortalOffer] = field(default_factory=list)
    card_offers: list[CardOffer] = field(default_factory=list)
    earn_rates: list[EarnRate] = field(default_factory=list)
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any], source: Optional[str] = None) -> "OffersBook":
        portals = [
            PortalOffer(
                portal=p["portal"],
                merchant=p["merchant"],
                rate=float(p["rate"]),
                kind=p.get("kind", "cash"),
                currency=p.get("currency"),
                note=p.get("note"),
            )
            for p in d.get("portals", [])
        ]
        cards = [
            CardOffer(
                card=c["card"],
                merchant=c["merchant"],
                discount_subunits=_subunits(c["discount"]),
                min_spend_subunits=_subunits(c.get("min_spend", 0)),
                expires=_date(c.get("expires")),
                note=c.get("note"),
            )
            for c in d.get("card_offers", [])
        ]
        earns = [
            EarnRate(
                card=e["card"],
                merchant=e.get("merchant", "*"),
                rate=float(e["rate"]),
                kind=e.get("kind", "points"),
                currency=e.get("currency"),
            )
            for e in d.get("earn_rates", [])
        ]
        return cls(
            point_values_cpp={str(k): float(v) for k, v in (d.get("point_values") or {}).items()},
            portals=portals,
            card_offers=cards,
            earn_rates=earns,
            source=source,
        )

    @classmethod
    def load(cls, path: str | Path) -> "OffersBook":
        """Load the YAML; a missing file yields an empty book (sticker stands)."""
        p = Path(path)
        if not p.exists():
            return cls(source=f"{p} (not found — effective price == sticker price)")
        import yaml

        with open(p) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data, source=str(p))


@dataclass
class EffectivePrice:
    sticker_subunits: int
    effective_subunits: int
    components: list[dict[str, Any]] = field(default_factory=list)  # subtracted
    earn: Optional[dict[str, Any]] = None  # informational best-card earn
    notes: list[str] = field(default_factory=list)

    @property
    def savings_subunits(self) -> int:
        return self.sticker_subunits - self.effective_subunits

    def summary(self) -> str:
        """One line in the design doc's alert style."""
        if not self.components:
            return f"${self.sticker_subunits / 100:,.2f} (no portal/offer stack applies)"
        parts = ", ".join(
            f"{c['label']} (${c['value_subunits'] / 100:,.2f}"
            + (f", expires {c['expires']}" if c.get("expires") else "")
            + ")"
            for c in self.components
        )
        s = (
            f"effective ~${self.effective_subunits / 100:,.2f} "
            f"after {parts} on ${self.sticker_subunits / 100:,.2f}"
        )
        if self.earn:
            s += f"; pay with {self.earn['card']} (+${self.earn['value_subunits'] / 100:,.2f} earn)"
        return s

    def to_dict(self) -> dict[str, Any]:
        return {
            "sticker": self.sticker_subunits / 100.0,
            "effective": self.effective_subunits / 100.0,
            "savings": self.savings_subunits / 100.0,
            "components": self.components,
            "earn": self.earn,
            "notes": self.notes,
            "summary": self.summary(),
        }


def compute_effective_price(
    book: OffersBook,
    merchant: str,
    sticker_subunits: int,
    on: Optional[date] = None,
) -> EffectivePrice:
    """Stack the best portal + all applicable card offers for ``merchant``."""
    on = on or date.today()
    m = merchant.strip().lower()
    components: list[dict[str, Any]] = []
    effective = sticker_subunits

    # Best portal (only one portal can route a purchase).
    portal_matches = [p for p in book.portals if p.merchant.strip().lower() == m]
    if portal_matches:
        best = max(
            portal_matches, key=lambda p: p.value_subunits(sticker_subunits, book.point_values_cpp)
        )
        value = best.value_subunits(sticker_subunits, book.point_values_cpp)
        if value > 0:
            components.append(
                {
                    "kind": "portal",
                    "label": f"{best.portal} {best.rate:g}{'%' if best.kind == 'cash' else 'x ' + (best.currency or 'mi')}",
                    "value_subunits": value,
                }
            )
            effective -= value

    # Card-linked offers: each distinct card's offer can stack with the portal.
    for c in book.card_offers:
        if c.merchant.strip().lower() == m and c.applies(sticker_subunits, on):
            components.append(
                {
                    "kind": "card_offer",
                    "label": f"{c.card} offer",
                    "value_subunits": c.discount_subunits,
                    "expires": c.expires.isoformat() if c.expires else None,
                }
            )
            effective -= c.discount_subunits

    # Best earn (merchant-specific beats catch-all only if it values higher).
    earn = None
    earn_matches = [e for e in book.earn_rates if e.merchant.strip().lower() in (m, "*")]
    if earn_matches:
        best_earn = max(
            earn_matches, key=lambda e: e.value_subunits(sticker_subunits, book.point_values_cpp)
        )
        earn = {
            "card": best_earn.card,
            "rate": best_earn.rate,
            "value_subunits": best_earn.value_subunits(sticker_subunits, book.point_values_cpp),
        }

    ep = EffectivePrice(
        sticker_subunits=sticker_subunits,
        effective_subunits=max(effective, 0),
        components=components,
        earn=earn,
    )
    if not book.portals and not book.card_offers:
        ep.notes.append(book.source or "offers book empty")
    return ep


def _subunits(v: Any) -> int:
    return int(round(float(v) * 100))


def _date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))
