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

---

## 4. Addendum: adversarial review findings (second pass)

An independent bug-hunting review of the implementation surfaced 16 findings;
the confirmed and high-value plausible ones were fixed (regression tests in
`tests/test_review_fixes.py`):

1. **Offers loading could still crash the daemon** — YAML syntax errors,
   top-level lists, and bad `point_values` escaped the `OffersError` guard.
   All load failures now surface as `OffersError`.
2. **Price protection saw pre-purchase prices** — a dip frozen in history
   before the purchase produced perpetual false claim alerts. Observations
   now filter to `observed_at > purchased_at`.
3. **Contradiction dedup loop** — the firmware pass flipping records to
   `update_reported` let re-synthesis insert duplicates and re-alert forever.
   Dedup now covers all *live* statuses, enforced by a partial unique index
   (race-safe across the two processes).
4. **Weak-evidence status transitions** — a search snippet echoing the query
   no longer auto-transitions a contradiction; the record stays open with the
   hit attached as a note, and a human closes it via `resolve_contradiction`.
5. **Policy substring misfire** — "Targeted Deals" no longer inherits
   Target's price-adjustment policy (prefix-with-word-boundary matching).
6. **Layer 1 re-arm lacked hysteresis** — prices oscillating pennies around
   the target could re-alert every other sweep; re-arm now requires exiting
   the region by `rearm_fraction`.
7. **Competitor-move dedup keyed on exact price** — cent-level drift created
   "new" events every sweep; keys now bucket by whole-percent drop.
8. **Discontinuation could only ever fire once** — keys now identify the
   out-of-stock *episode*, so a recovery followed by a real discontinuation
   re-alerts.
9. **`"light "` substring matching** — "slight"/"flight" triggered the weight
   aspect and clause-final "light" was missed; aspect keywords are now
   word-bounded regexes.
10. **Decimal numbers split clauses** — "lasts 3.5 hours" severed aspects
    from their sentiment; the splitter no longer breaks on digit-flanked dots.
11. **Thread affinity under FastMCP** — sync tools run on worker threads;
    the SQLite connection now uses `check_same_thread=False` (CPython's
    sqlite3 is serialized).
12. **Migration version could advance past a failed migration** — only
    "duplicate column" errors are swallowed now.
13. **Card offers over-stacked** — multiple offers (including same-card) all
    subtracted; one card pays, so only the best applicable offer subtracts,
    with the alternatives noted.
14. **Silent 1.0 cpp default** — a miles/points rate whose currency is
    missing from `point_values` is now a validation error instead of a 37%
    valuation error.
15. **`record_purchase` FK crash** — an unknown `tracked_id` now returns an
    error dict instead of an uncaught `IntegrityError`.

**Accepted risk (documented, not fixed):** cross-process read-modify-write
races on `last_alert_json` if the MCP server and the monitor run `check_once`
for the same product in the same instant — worst case one duplicate push
notification. The fix (per-row optimistic versioning) isn't worth the
complexity at this duty cycle.
