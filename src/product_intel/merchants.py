"""Canonical merchant identity (design review §1.2).

Offer matching, the policy table, and the executor registry all key on
merchant names that arrive from three uncoordinated sources: Channel3/Rye
records, hand-written offer YAML, and user input. This module collapses the
aliases to one canonical key so those joins can't silently miss.

Unknown merchants pass through trimmed/lowercased — the system stays
open-world; canonicalization only has to be right for merchants that appear
in the offers YAML or policy table.
"""

from __future__ import annotations

_ALIASES: dict[str, str] = {
    # B&H
    "b&h photo": "b&h",
    "b&h photo video": "b&h",
    "bh photo": "b&h",
    "bhphotovideo": "b&h",
    "bhphotovideo.com": "b&h",
    # Best Buy
    "bestbuy": "best buy",
    "bestbuy.com": "best buy",
    # Amazon
    "amazon.com": "amazon",
    "amazon us": "amazon",
    # Adorama
    "adorama camera": "adorama",
    "adorama.com": "adorama",
    # Costco
    "costco wholesale": "costco",
    "costco.com": "costco",
    # Target
    "target.com": "target",
}


def canonical(merchant: str) -> str:
    """Canonical lowercase key for a merchant name."""
    key = merchant.strip().lower()
    # Suffixes like "(used)" or department qualifiers don't change identity
    # for offer/policy purposes: "Adorama (used)" -> "adorama".
    base = key.split("(")[0].strip()
    return _ALIASES.get(base, _ALIASES.get(key, base))


def same_merchant(a: str, b: str) -> bool:
    return canonical(a) == canonical(b)
