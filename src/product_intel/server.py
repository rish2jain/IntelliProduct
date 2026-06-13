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
) -> dict[str, Any]:
    """Begin continuous monitoring of a product at a target price."""
    return service().start_tracking(label, merchant, url, target_price, rye_product_id)


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
def synthesize_reviews(query: str, candidate_url: str | None = None) -> dict[str, Any]:
    """Use-case-segmented review synthesis. (Phase C — not yet implemented.)

    Stubbed in Phase A. Phase C ports the legal-rag-local hybrid-retrieval stack
    (Reddit/YouTube/expert-review ingestion, local segmentation) per Section 3.
    """
    return {
        "status": "not_implemented",
        "phase": "C",
        "message": "Review synthesis ships in Phase C (local hybrid retrieval + use-case segmentation).",
        "query": query,
        "candidate_url": candidate_url,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
