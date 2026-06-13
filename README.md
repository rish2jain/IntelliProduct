# IntelliProduct — Integrated Product Intelligence Agent

Phase A of the [v2 design](docs/ipiav2design.md): a `product-intel` MCP server
plus an always-on monitor that tracks product prices in SQLite and pushes
**Layer 1 / Layer 2** alerts to your phone.

This phase ships the monitoring value for the next real purchase. Effective-price
routing (Phase B), use-case review synthesis (Phase C), and Layer 3 market-context
triggers (Phase D) are scoped but not built — see the design doc's build order.

## What's here

| Piece | Module | Notes |
|---|---|---|
| MCP server (FastMCP) | `product_intel.server` | 9 tools; `synthesize_reviews` is a Phase-C stub |
| Discovery + Spec Worker | `product_intel.workers.discovery` | Channel3 search → Rye normalize |
| Price Context Worker | `product_intel.workers.price_context` | Keepa for Amazon; honest "no history" otherwise |
| Constraint post-filter | `product_intel.workers.constraints` | pure function, not an LLM "worker" (§2.1) |
| Layer 1/2 alert rules | `product_intel.alerts.rules` | threshold + relative-value, with cold-start baselines |
| SQLite state | `product_intel.db` | observations accrue over months (§2.3) |
| Monitor daemon | `product_intel.monitor` | `launchd`-friendly `--once` sweep |
| Notifier | `product_intel.alerts.notifier` | ntfy / Pushover |

Every external dependency (Channel3, Rye, Keepa) sits behind a `Protocol`, so the
Keepa price-history source is swappable and the whole stack runs offline with
in-memory fakes.

## Quick start (offline, no API keys)

```bash
pip install -e .
PRODUCT_INTEL_FAKE=1 python -m product_intel.demo
```

The demo discovers candidates, starts tracking, simulates a price drop, fires a
Layer 1 alert, and stages a purchase handoff.

## Run the tests

```bash
pip install -e ".[dev]"
pytest                 # 23 tests, fully offline
```

## Run the MCP server

```bash
product-intel          # stdio; or: python -m product_intel.server
```

Register it with Claude Code (`.mcp.json` / `claude mcp add`). Tools:
`clarify_intent`, `build_candidate_pool`, `compare_candidates`,
`get_price_context`, `start_tracking`, `stop_tracking`, `tracking_status`,
`stage_handoff`, `synthesize_reviews`.

## Run the monitor

```bash
# single sweep of all active tracked products (launchd owns the schedule)
python -m product_intel.monitor --once

# or an in-process loop
python -m product_intel.monitor
```

On the always-on Mac Studio, install the launchd agent:

```bash
cp deploy/com.productintel.monitor.plist ~/Library/LaunchAgents/   # edit paths first
launchctl load ~/Library/LaunchAgents/com.productintel.monitor.plist
```

## Configuration

All via environment variables (no secrets in code):

| Var | Purpose |
|---|---|
| `PRODUCT_INTEL_FAKE=1` | use in-memory fakes (offline) |
| `PRODUCT_INTEL_DB` | SQLite path (default `~/.product-intel/product-intel.sqlite3`) |
| `CHANNEL3_API_KEY` | discovery search |
| `RYE_API_KEY` | URL → normalized product (also the live-price source for the monitor) |
| `KEEPA_API_KEY` | Amazon price history (subscribe only during active tracking windows) |
| `NTFY_TOPIC` / `PUSHOVER_TOKEN`+`PUSHOVER_USER` | phone alerts |
| `MONITOR_INTERVAL_SECONDS` | loop interval (default 21600 = 6h) |

## Design boundaries honored in this phase

- **No logged-in retailer session access.** The monitor and `stage_handoff` read
  live prices via the Rye data API, never by scraping a logged-in cart — the
  posture that lost in *Amazon v. Perplexity* (§1.1). Handoff produces a deep
  link + briefing; the human checks out manually.
- **No fabricated price history.** Non-Amazon retailers get an explicit
  "no history available" flag; Layer 2 cold-starts its own baseline (§5).
- **Swappable Keepa.** Behind `PriceHistoryClient`, so it can be subscribed and
  cancelled per the design doc's cost note (§4).
