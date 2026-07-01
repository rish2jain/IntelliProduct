"""Purchase-executor registry (design review §1.5, doc §1.2).

The design doc: protocol-native checkout is "a capability flag per retailer,
checked at handoff time", with `manual_deeplink` as the default that always
works and `acp_checkout` only where the merchant has opted into an agentic
commerce protocol. This module is that dispatch seam — deliberately without a
checkout implementation, because coverage for this user's retailers is
effectively zero today.

Opt a merchant in via the env var (comma-separated merchant names):

    PRODUCT_INTEL_ACP_MERCHANTS="etsy,some shopify store"
"""

from __future__ import annotations

import os

from .merchants import canonical

MANUAL_DEEPLINK = "manual_deeplink"
ACP_CHECKOUT = "acp_checkout"

# Merchants with a protocol-native checkout rail, by canonical key.
# Empty by default; extend in code as real coverage appears.
PROTOCOL_CAPABLE: set[str] = set()


def _env_capable() -> set[str]:
    raw = os.environ.get("PRODUCT_INTEL_ACP_MERCHANTS", "")
    return {canonical(m) for m in raw.split(",") if m.strip()}


def executor_for(merchant: str) -> str:
    """The executor to use at handoff time for this merchant."""
    if canonical(merchant) in (PROTOCOL_CAPABLE | _env_capable()):
        return ACP_CHECKOUT
    return MANUAL_DEEPLINK
