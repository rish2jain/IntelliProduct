# Design Review — v3 refinements

**Scope:** review of the all-phases implementation (PR #1) against the
[v2 design](ipiav2design.md), plus the refinements adopted in this pass.
Live-API validation items remain in `BACKLOG.md` P0 — they need keys, not
design changes.

---

## 1. Findings

### 1.1 Alert dedup never re-arms (design gap, Layer 1/2)

`alerts/rules.py` re-fires a layer only when the price drops *strictly below*
that layer's last alerted price. Correct for suppressing flat-price spam, but
wrong across cycles: if a product alerts at $2,279, rebounds to $2,450 for two
months, then drops to $2,299 (below the $2,300 target), no alert fires —
$2,299 is not an improvement on $2,279. For months-long tracking windows this
silently eats exactly the mid-cycle events the system exists to catch.

**Refinement:** crossing-based re-arm. When the price exits the trigger
region (Layer 1: above target; Layer 2: a configurable fraction above the
last alerted price), the layer's dedup state clears, and the next entry into
the region alerts again. Dedup state changes must persist even on sweeps that
fire no alerts.

### 1.2 Merchant identity is string-fragile (Phase B)

Offer matching (`effective_price.py`) and the policy table (`purchases.py`)
match on exact lowercased merchant strings. "B&H", "B&H Photo", and
"bhphotovideo.com" are three different merchants to the code; a Channel3
result and a hand-written YAML entry can silently fail to join, which
misreports the effective price — the layer's whole point.

**Refinement:** a canonical-merchant module (`merchants.py`) with an alias
table, applied at every merchant comparison boundary (offers, policies,
handoff). Unknown merchants pass through normalized (trim/lowercase) so the
system stays open-world.

### 1.3 Offer YAML failures are hostile (Phase B)

`OffersBook.from_dict` raises raw `KeyError`/`ValueError` mid-parse, so one
malformed entry kills the whole book with no pointer to the bad entry. And
the doc's "refreshed weekly" contract is unenforced — a three-month-old YAML
quietly reports offers that expired in spring (expiry dates catch card
offers, but portal rates have no expiry field and *do* go stale).

**Refinement:** entry-level validation that reports the section and index of
each malformed entry, and a staleness flag (`refreshed_within_days`, default
8) surfaced by `reload_offers` and in handoff briefings.

### 1.4 Contradiction ledger is write-only for humans (Phase C)

Records are created by synthesis and auto-transitioned by the Layer 3
firmware pass, but there is no way to resolve or dismiss one from the Claude
conversation — the design doc's "resolution status" implies a human closes
the loop after verifying a fix.

**Refinement:** `resolve_contradiction` MCP tool (status + note), and open
contradiction counts surfaced in `tracking_status` so they're visible where
the user already looks.

### 1.5 Protocol checkout has no seam (§1.2)

`stage_handoff` hardcodes `executor: "manual_deeplink"`. The design doc asks
for a *capability flag per retailer, checked at handoff time* — the seam
should exist even while the registry is empty (coverage is effectively zero
for this user's retailers today).

**Refinement:** an executor registry (`executors.py`): canonical merchant →
executor, defaulting to `manual_deeplink`, overridable via
`PRODUCT_INTEL_ACP_MERCHANTS` for when a retailer opts in. No checkout
implementation — just the dispatch point the doc specifies.

### 1.6 Monitor health is unobservable

A dead launchd job is indistinguishable from a quiet market. Nothing records
when the last sweep ran.

**Refinement:** each sweep writes a heartbeat (`meta` table); a
`monitor_health` MCP tool reports last-sweep age, overdue status (> 2×
interval), active tracked count, and notifier configuration. The in-process
loop also gains ±10% sleep jitter so a large tracked set doesn't hit
providers in lockstep bursts.

### 1.7 Multi-process SQLite (MCP server + monitor daemon)

The MCP server (long-lived, interactive) and the monitor (launchd) share one
SQLite file. Default journal mode holds writer locks long enough to produce
`database is locked` errors under overlap.

**Refinement:** WAL journal mode + `busy_timeout` on connect. WAL gives
concurrent readers + single writer with retries — correct for this two-process
shape without introducing a server DB.

### 1.8 Schema migrations are ad-hoc

`db._migrate` is one try/except `ALTER`. A second schema change would have to
copy the pattern with no record of what ran.

**Refinement:** versioned migrations: `schema_version` in `meta`, an ordered
migration map, applied idempotently on connect.

### 1.9 Hygiene (small, real)

- `except (ClientNotConfigured, Exception)` appears at three call sites — the
  tuple is redundant (everything is `Exception`); it reads as if
  `ClientNotConfigured` got special handling when it doesn't. Collapse to a
  deliberate `except Exception` with the intent stated.
- `service._live_price` returns an ad-hoc class defined inside the method;
  replace with a proper `LivePrice` dataclass.
- No CI: 54 offline tests exist but nothing runs them on push. Add a GitHub
  Actions workflow and an `.mcp.json` example for registering the server.

---

## 2. Explicitly not changed

- **Heuristic LLM / hash embedder / greedy clustering** stay as the offline
  tier. The upgrade path (ChromaDB + RRF + cross-encoder from legal-rag-local)
  is P1 backlog: it changes fidelity, not architecture, and shouldn't be
  faked without the real corpus infrastructure.
- **Live-API parser validation** stays P0 backlog — requires keys and recorded
  fixtures, not design work.
- **The §6 cut list stays cut**: no cart staging, no coupon auto-apply, no
  currency arbitrage, no enterprise fork.

## 3. Implementation map

| Refinement | Where |
|---|---|
| 1.1 re-arm | `alerts/rules.py` (+ `RuleParams.rearm_fraction`), `service.check_once` persists dedup-state changes always |
| 1.2 merchant aliases | new `merchants.py`; applied in `effective_price.py`, `purchases.policy_for`, `executors.py` |
| 1.3 offer validation + staleness | `effective_price.py` (`OffersBook.validate`-on-load, `stale` flag), `service.reload_offers`, handoff note |
| 1.4 contradiction resolution | `service.resolve_contradiction`, MCP tool, `tracking_status` surfaces open counts |
| 1.5 executor seam | new `executors.py`; `stage_handoff` dispatches through it |
| 1.6 heartbeat + health | `monitor.py`, `service.monitor_health`, MCP tool, loop jitter |
| 1.7 WAL + busy_timeout | `db.Store.__init__` |
| 1.8 versioned migrations | `db.py` (`SCHEMA_VERSION`, `MIGRATIONS`) |
| 1.9 hygiene | `workers/discovery.py`, `service.py`, `market.py`; `.github/workflows/tests.yml`, `deploy/mcp.example.json` |
