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

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .merchants import canonical


class OffersError(ValueError):
    """Raised on a malformed offers YAML, naming every bad entry."""


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
    """The manual offer YAML, parsed and validated.

    Empty book == sticker price stands. Merchant names are canonicalized at
    load so YAML entries join against merchant strings from Channel3/Rye or
    user input regardless of spelling ("B&H Photo" vs "B&H").
    """

    point_values_cpp: dict[str, float] = field(default_factory=dict)
    portals: list[PortalOffer] = field(default_factory=list)
    card_offers: list[CardOffer] = field(default_factory=list)
    earn_rates: list[EarnRate] = field(default_factory=list)
    source: Optional[str] = None
    file_mtime: Optional[float] = None

    def stale(self, max_age_days: float = 8.0) -> bool:
        """True when the YAML hasn't been refreshed within the weekly contract."""
        if self.file_mtime is None:
            return False
        return (time.time() - self.file_mtime) > max_age_days * 86400

    @classmethod
    def from_dict(cls, d: dict[str, Any], source: Optional[str] = None) -> "OffersBook":
        if not isinstance(d, dict):
            raise OffersError(
                f"offers YAML invalid: top level must be a mapping, got {type(d).__name__}"
            )
        errors: list[str] = []
        point_values: dict[str, float] = {}
        for k, v in (d.get("point_values") or {}).items():
            try:
                point_values[str(k)] = float(v)
            except (ValueError, TypeError):
                errors.append(f"point_values[{k!r}]: not a number ({v!r})")
        portals: list[PortalOffer] = []
        for i, p in enumerate(d.get("portals") or []):
            try:
                portals.append(
                    PortalOffer(
                        portal=_require(p, "portal"),
                        merchant=canonical(_require(p, "merchant")),
                        rate=float(_require(p, "rate")),
                        kind=_one_of(p.get("kind", "cash"), {"cash", "miles"}, "kind"),
                        currency=p.get("currency"),
                        note=p.get("note"),
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                errors.append(f"portals[{i}]: {e}")
        cards: list[CardOffer] = []
        for i, c in enumerate(d.get("card_offers") or []):
            try:
                cards.append(
                    CardOffer(
                        card=_require(c, "card"),
                        merchant=canonical(_require(c, "merchant")),
                        discount_subunits=_subunits(_require(c, "discount")),
                        min_spend_subunits=_subunits(c.get("min_spend", 0)),
                        expires=_date(c.get("expires")),
                        note=c.get("note"),
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                errors.append(f"card_offers[{i}]: {e}")
        earns: list[EarnRate] = []
        for i, e in enumerate(d.get("earn_rates") or []):
            try:
                merchant = e.get("merchant", "*")
                earns.append(
                    EarnRate(
                        card=_require(e, "card"),
                        merchant=merchant if merchant == "*" else canonical(merchant),
                        rate=float(_require(e, "rate")),
                        kind=_one_of(e.get("kind", "points"), {"points", "cash"}, "kind"),
                        currency=e.get("currency"),
                    )
                )
            except (KeyError, ValueError, TypeError) as err:
                errors.append(f"earn_rates[{i}]: {err}")
        # Cross-reference: every miles/points rate must have a valuation, or
        # its value silently defaults to 1.0 cpp and can misrank portals.
        for i, p in enumerate(portals):
            if p.kind == "miles" and (p.currency or "") not in point_values:
                errors.append(
                    f"portals[{i}]: currency {p.currency!r} has no entry in point_values"
                )
        for i, e in enumerate(earns):
            if e.kind == "points" and (e.currency or "") not in point_values:
                errors.append(
                    f"earn_rates[{i}]: currency {e.currency!r} has no entry in point_values"
                )
        if errors:
            raise OffersError(
                "offers YAML invalid:\n  " + "\n  ".join(errors)
            )
        return cls(
            point_values_cpp=point_values,
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

        try:
            with open(p) as f:
                data = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError) as e:
            # Hand-edited weekly: a stray tab must surface as the same
            # recoverable error class as a bad entry, not crash the daemon.
            raise OffersError(f"offers YAML unreadable: {e}") from e
        book = cls.from_dict(data, source=str(p))
        book.file_mtime = p.stat().st_mtime
        return book


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
    m = canonical(merchant)
    components: list[dict[str, Any]] = []
    effective = sticker_subunits

    # Best portal (only one portal can route a purchase).
    portal_matches = [p for p in book.portals if p.merchant == m]
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

    # Card-linked offers stack with the portal, but a purchase is paid with
    # ONE card — only the single best applicable offer subtracts. Others are
    # noted as alternatives so a different-card routing stays visible.
    applicable = [c for c in book.card_offers if c.merchant == m and c.applies(sticker_subunits, on)]
    if applicable:
        best_offer = max(applicable, key=lambda c: c.discount_subunits)
        components.append(
            {
                "kind": "card_offer",
                "label": f"{best_offer.card} offer",
                "value_subunits": best_offer.discount_subunits,
                "expires": best_offer.expires.isoformat() if best_offer.expires else None,
            }
        )
        effective -= best_offer.discount_subunits
        alternatives = [c for c in applicable if c is not best_offer]
        if alternatives:
            notes_alt = ", ".join(
                f"{c.card} ${c.discount_subunits / 100:,.2f}" for c in alternatives
            )
            # Collected into ep.notes after construction below.
            alt_note = f"other card offers not stacked (one card pays): {notes_alt}"
        else:
            alt_note = None
    else:
        alt_note = None

    # Best earn (merchant-specific beats catch-all only if it values higher).
    earn = None
    earn_matches = [e for e in book.earn_rates if e.merchant in (m, "*")]
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
    if alt_note:
        ep.notes.append(alt_note)
    if not book.portals and not book.card_offers:
        ep.notes.append(book.source or "offers book empty")
    return ep


def _require(d: dict[str, Any], key: str) -> Any:
    if key not in d or d[key] is None:
        raise KeyError(f"missing required key '{key}'")
    return d[key]


def _one_of(value: Any, allowed: set[str], key: str) -> str:
    if value not in allowed:
        raise ValueError(f"'{key}' must be one of {sorted(allowed)}, got {value!r}")
    return str(value)


def _subunits(v: Any) -> int:
    return int(round(float(v) * 100))


def _date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))
