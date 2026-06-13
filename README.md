# IntelliProduct — Integrated Product Intelligence Agent

Full implementation of the [v2 design](docs/ipiav2design.md): a `product-intel`
MCP server plus an always-on monitor, covering all four build phases —

- **Phase A — monitoring core:** discovery (Channel3 → Rye), Keepa price
  context, SQLite tracking, launchd monitor with Layer 1/2 alerts.
- **Phase B — effective price:** the portal / card-offer / earn stack from a
  manual weekly YAML; alerts and handoffs report effective price, not sticker.
- **Phase C — review synthesis:** use-case-segmented review synthesis over
  defensible sources (expert sites, Best Buy API, Reddit, YouTube — never
  Amazon scraping), with a persistent contradiction ledger.
- **Phase D — Layer 3 market context:** discontinuation signals, competitor
  price moves, weekly successor-announcement search, and firmware/recall
  checks against open contradiction records.
- **Phase 5 — post-purchase:** per-retailer price-protection policy table
  (2026 reality: Amazon none, Best Buy members, Target 14d, Costco 30d,
  B&H/Adorama case-by-case), return windows, warranty reminders.

## Layout

| Piece | Module |
|---|---|
| MCP server (FastMCP, 16 tools) | `product_intel.server` |
| Discovery + Spec Worker | `product_intel.workers.discovery` |
| Price Context Worker (Keepa, swappable) | `product_intel.workers.price_context` |
| Constraint post-filter (pure function) | `product_intel.workers.constraints` |
| Layer 1/2 alert rules | `product_intel.alerts.rules` |
| Effective-price stack (Phase B) | `product_intel.effective_price` |
| Post-purchase policies (Phase 5) | `product_intel.purchases` |
| Review synthesis pipeline (Phase C) | `product_intel.reviews.*` |
| Layer 3 market scanner (Phase D) | `product_intel.market` |
| SQLite state | `product_intel.db` |
| Monitor daemon | `product_intel.monitor` |

Every external dependency — Channel3, Rye, Keepa, Brave Search, Ollama,
Reddit, YouTube, Best Buy — sits behind a `Protocol` with an in-memory fake,
so the entire stack runs offline.

## Quick start (offline, no API keys)

```bash
pip install -e .
PRODUCT_INTEL_FAKE=1 python -m product_intel.demo
```

The demo walks all phases: discovery → tracking → a Layer 1 alert carrying the
effective-price stack → review synthesis that opens a battery contradiction in
the ledger → a Layer 3 scan that finds a successor signal *and* a firmware fix
for that open contradiction → handoff briefing → purchase recording.

## Tests

```bash
pip install -e ".[dev]"
pytest                 # 54 tests, fully offline
```

## MCP server

```bash
product-intel          # stdio; or: python -m product_intel.server
```

Tools: `clarify_intent`, `build_candidate_pool`, `compare_candidates`,
`get_price_context`, `synthesize_reviews`, `effective_price`, `reload_offers`,
`start_tracking`, `stop_tracking`, `tracking_status`, `stage_handoff`,
`record_purchase`, `purchase_status`, `mark_warranty_registered`,
`list_contradictions`, `run_market_scan`.

## Monitor

```bash
python -m product_intel.monitor --once   # one sweep (launchd-friendly)
python -m product_intel.monitor          # in-process loop
```

Each sweep runs Layer 1/2 price checks, the Layer 3 market scan (the
web-search pass honors a weekly cadence), and post-purchase checks. Install on
the always-on Mac Studio:

```bash
cp deploy/com.productintel.monitor.plist ~/Library/LaunchAgents/   # edit paths first
launchctl load ~/Library/LaunchAgents/com.productintel.monitor.plist
```

## The offer YAML (Phase B)

Copy `deploy/offers.example.yaml` to `~/.product-intel/offers.yaml` and refresh
weekly by hand — Amex/Chase Offers have no API, and scraping a logged-in card
portal is the same legal posture as scraping Amazon. The alert then reads like
the design doc's example:

> `Sony a7 IV: $2,279.00 (target $2,300.00) at B&H — effective ~$2,123.84
> after Rakuten 4% ($91.16), Amex Plat offer ($64.00, expires 2026-06-30)
> on $2,279.00; pay with Venture X (+$45.58 earn)`

## Configuration

All via environment variables (no secrets in code):

| Var | Purpose |
|---|---|
| `PRODUCT_INTEL_FAKE=1` | in-memory fakes everywhere (offline) |
| `PRODUCT_INTEL_DB` | SQLite path (default `~/.product-intel/product-intel.sqlite3`) |
| `OFFERS_FILE` | offer YAML path (default `~/.product-intel/offers.yaml`) |
| `CHANNEL3_API_KEY` / `RYE_API_KEY` / `KEEPA_API_KEY` | discovery / normalization / Amazon history |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_EMBED_MODEL` | local review processing (Mac Studio) |
| `REDDIT_CLIENT_ID`+`REDDIT_CLIENT_SECRET`, `YOUTUBE_API_KEY`, `BESTBUY_API_KEY` | review sources (each optional) |
| `BRAVE_API_KEY` | Layer 3 web-search pass + handoff coupon search |
| `NTFY_TOPIC` or `PUSHOVER_TOKEN`+`PUSHOVER_USER` | phone alerts |
| `MONITOR_INTERVAL_SECONDS` / `MARKET_SCAN_INTERVAL_SECONDS` | sweep (6h) / web pass (weekly) cadence |

## Design boundaries honored

- **No logged-in retailer session access.** Live prices come from the Rye data
  API; reviews come from official APIs and standard fetching; handoff emits a
  deep link + briefing and the human checks out (§1.1, post-*Amazon v.
  Perplexity*). Amazon review sentiment is reported as *unverifiable*, never
  scraped.
- **No fabricated data.** Non-Amazon price history cold-starts honestly;
  review coverage gaps are listed per source; the no-policy retailer gets one
  notice and zero false-hope protection alerts (§5).
- **Compute placement (§2.2).** Bulk per-review extraction targets local
  Ollama (with a deterministic heuristic fallback); the MCP tools return
  structured output so the cross-candidate reasoning happens in the Claude
  conversation under the existing subscription.
- **Manual where automation is a rabbit hole (§4).** Offers live in a small
  YAML refreshed weekly; coupon codes are best-effort "codes to try," never
  auto-applied.
