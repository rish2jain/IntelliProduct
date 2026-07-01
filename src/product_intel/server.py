"""`product-intel` MCP server (FastMCP).

Section 2.3: exposes the research workflow as tools so research is an
interactive Claude conversation rather than a fire-and-forget pipeline. The
Intent Clarifier is conversational by nature, and the Research Review Gate is
"just talking to Claude with the evidence on screen."

Run: ``product-intel`` (stdio) or ``python -m product_intel.server``.
Tools delegate to the shared :class:`Service`.
"""

from __future__ import annotations

from typing import Any, Optional

from fastmcp import FastMCP

from .service import Service

mcp: FastMCP = FastMCP("product-intel")
_service: Optional[Service] = None


def service() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service


@mcp.tool
def clarify_intent(
    raw_query: str,
    category: str | None = None,
    budget_min: float | None = None,
    budget_max: float | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a purchase intent (query, category, budget, constraints).

    Returns the intent_id used by build_candidate_pool and compare_candidates.
    """
    return service().clarify_intent(raw_query, category, budget_min, budget_max, constraints)


@mcp.tool
def build_candidate_pool(
    query: str,
    intent_id: int | None = None,
    constraints: dict[str, Any] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Discover + normalize candidates (Channel3 -> Rye), constraint-filtered."""
    return service().build_candidate_pool(query, intent_id, constraints, limit)


@mcp.tool
def compare_candidates(intent_id: int) -> dict[str, Any]:
    """Return the stored candidate pool for an intent, ordered by price."""
    return service().compare_candidates(intent_id)


@mcp.tool
def get_price_context(
    url: str,
    rye_product_id: str | None = None,
    current_price: float | None = None,
) -> dict[str, Any]:
    """Price history context (Keepa for Amazon; honest 'no history' otherwise)."""
    return service().get_price_context(url, rye_product_id, current_price)


@mcp.tool
def start_tracking(
    label: str,
    merchant: str,
    url: str,
    target_price: float,
    rye_product_id: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Begin continuous monitoring of a product at a target price.

    Give competing candidates the same `category` so Layer 3 reports
    competitor price moves across them.
    """
    return service().start_tracking(label, merchant, url, target_price, rye_product_id, category)


@mcp.tool
def stop_tracking(tracked_id: int) -> dict[str, Any]:
    """Stop monitoring a tracked product."""
    return service().stop_tracking(tracked_id)


@mcp.tool
def tracking_status(tracked_id: int | None = None) -> dict[str, Any]:
    """Status of one or all tracked products: latest price, alerts, history size."""
    return service().tracking_status(tracked_id)


@mcp.tool
def stage_handoff(tracked_id: int, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    """Purchase Handoff: verify live price, re-check constraints, emit deep link + briefing.

    Never touches the retailer's logged-in session (Section 1.1).
    """
    return service().stage_handoff(tracked_id, constraints)


@mcp.tool
def synthesize_reviews(
    query: str,
    product_label: str | None = None,
    user_profile: str | None = None,
) -> dict[str, Any]:
    """Use-case-segmented review synthesis (Phase C).

    Gathers reviews from defensible sources (expert sites, Best Buy API,
    Reddit, YouTube — never Amazon scraping), extracts a use-case tuple per
    review, clusters into segments, and scores within the segment matching
    `user_profile` (e.g. "parent shooting indoor volleyball weekly").
    Contradictions persist to the ledger; coverage gaps are reported honestly.
    """
    return service().synthesize_reviews(query, product_label, user_profile)


@mcp.tool
def effective_price(merchant: str, price: float) -> dict[str, Any]:
    """Phase B: compute the effective price after the portal/card-offer stack
    from the manual offers YAML, plus best-card earn (informational)."""
    return service().effective_price(merchant, price)


@mcp.tool
def reload_offers() -> dict[str, Any]:
    """Re-read the manual offers YAML after a weekly refresh."""
    return service().reload_offers()


@mcp.tool
def record_purchase(
    label: str,
    merchant: str,
    price: float,
    url: str | None = None,
    tracked_id: int | None = None,
) -> dict[str, Any]:
    """Record a completed purchase to start post-purchase tracking
    (per-retailer price-protection policy, return window, warranty reminder)."""
    return service().record_purchase(label, merchant, price, url, tracked_id)


@mcp.tool
def purchase_status() -> dict[str, Any]:
    """Days left on return windows and price-protection windows for recorded purchases."""
    return service().purchase_status()


@mcp.tool
def mark_warranty_registered(purchase_id: int) -> dict[str, Any]:
    """Mark a purchase's warranty as registered (stops the reminder)."""
    return service().mark_warranty_registered(purchase_id)


@mcp.tool
def list_contradictions(product_label: str | None = None, open_only: bool = False) -> dict[str, Any]:
    """Read the contradiction ledger (claim A/source A vs claim B/source B, status)."""
    return service().list_contradictions(product_label, open_only)


@mcp.tool
def resolve_contradiction(
    contradiction_id: int, status: str = "resolved", note: str | None = None
) -> dict[str, Any]:
    """Close a contradiction-ledger record after verifying or dismissing it
    (status: resolved | dismissed | update_reported | open)."""
    return service().resolve_contradiction(contradiction_id, status, note)


@mcp.tool
def monitor_health() -> dict[str, Any]:
    """Monitor heartbeat: last sweep age, overdue flag, tracked count,
    notifier/offers status. Use to tell a dead launchd job from a quiet market."""
    return service().monitor_health()


@mcp.tool
def run_market_scan(force: bool = False) -> dict[str, Any]:
    """Phase D: run the Layer 3 market-context scan now (discontinuation,
    competitor moves, successor announcements, firmware vs open contradictions).
    `force=True` ignores the weekly web-search cadence."""
    return service().market_scan(force)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
