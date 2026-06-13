"""Constraint validation as a deterministic post-filter.

Section 2.1: the Constraint Validator is not a worker — it's "a pure function
over the candidate table, run after every worker pass and again at handoff
time. No LLM call." This module is exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..models import Availability, Product


@dataclass(frozen=True)
class Constraints:
    budget_min_subunits: Optional[int] = None
    budget_max_subunits: Optional[int] = None
    require_purchasable: bool = False
    require_in_stock: bool = False
    allowed_merchants: Optional[frozenset[str]] = None
    blocked_merchants: Optional[frozenset[str]] = None
    required_attributes: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Constraints":
        am = d.get("allowed_merchants")
        bm = d.get("blocked_merchants")
        return cls(
            budget_min_subunits=d.get("budget_min_subunits"),
            budget_max_subunits=d.get("budget_max_subunits"),
            require_purchasable=bool(d.get("require_purchasable", False)),
            require_in_stock=bool(d.get("require_in_stock", False)),
            allowed_merchants=frozenset(am) if am else None,
            blocked_merchants=frozenset(bm) if bm else None,
            required_attributes=d.get("required_attributes"),
        )


def violations(product: Product, c: Constraints) -> list[str]:
    """Return the list of reasons ``product`` fails ``c`` (empty == passes)."""
    out: list[str] = []
    if c.budget_min_subunits is not None and product.price_subunits < c.budget_min_subunits:
        out.append(f"below budget min ({product.price:.2f} < {c.budget_min_subunits / 100:.2f})")
    if c.budget_max_subunits is not None and product.price_subunits > c.budget_max_subunits:
        out.append(f"over budget max ({product.price:.2f} > {c.budget_max_subunits / 100:.2f})")
    if c.require_purchasable and not product.purchasable:
        out.append("not purchasable")
    if c.require_in_stock and product.availability != Availability.IN_STOCK:
        out.append(f"not in stock ({product.availability.value})")
    if c.allowed_merchants and product.merchant not in c.allowed_merchants:
        out.append(f"merchant '{product.merchant}' not in allowed set")
    if c.blocked_merchants and product.merchant in c.blocked_merchants:
        out.append(f"merchant '{product.merchant}' is blocked")
    for key, want in (c.required_attributes or {}).items():
        if str(product.attributes.get(key)) != str(want):
            out.append(f"attribute {key}={product.attributes.get(key)!r} != required {want!r}")
    return out


def filter_candidates(products: list[Product], c: Constraints) -> list[Product]:
    return [p for p in products if not violations(p, c)]
