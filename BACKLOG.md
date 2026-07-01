# Backlog

> **Update:** the v3 design-review pass ([docs/design-review-v3.md](docs/design-review-v3.md))
> implemented several items from this list; they are marked `[x]` below with a
> pointer. Live-API validation (P0) remains the top open lane.

Remaining work after the all-phases implementation (PR #1). Every external
dependency runs behind a `Protocol` with an in-memory fake, so the stack is
complete and offline-testable — but most of the **real** integrations have
never been exercised against live APIs, and a few pieces are deliberately
simplified relative to the design doc. This is the honest list.

Priority: **P0** blocks real-world use · **P1** materially improves quality ·
**P2** nice-to-have / portfolio polish.

---

## P0 — Validate the real integrations against live APIs

The response parsers (`_parse` / `normalize` / `_decode` etc.) are written to
the documented shapes but have **never seen a real response**. Each needs a
recorded-fixture (VCR-style) test and a live smoke test before it can be
trusted in the monitor.

- [ ] **Channel3** (`clients/channel3.py`) — confirm endpoint, auth header,
      and the `products`/`results` + `price.price_subunits` shape; wire the
      1,000/mo free tier.
- [ ] **Rye** (`clients/rye.py`) — confirm `/v1/product` request/response and
      the `price.value` subunit field; this is also the monitor's live-price
      source, so getting it wrong silently breaks tracking.
- [ ] **Keepa** (`clients/keepa.py`) — verify the `csv[0]` Amazon series
      decode and Keepa-minute epoch against a real product; confirm token
      metering behavior.
- [ ] **Reddit / YouTube / Best Buy** (`reviews/sources.py`) — validate OAuth
      flow, search params, and JSON shapes; handle rate limits and pagination.
- [ ] **Brave Search** (`clients/websearch.py`) — confirm response shape and
      quota.
- [ ] Add a `recordings/` fixture set and a `--live` pytest marker so the
      parsers are covered without burning quota on every run.

## P0 — Notifier hardening

- [ ] Retry/backoff on transient delivery failures (`alerts/notifier.py`);
      right now a failed send is logged and dropped (alert is still persisted).
- [ ] Verify ntfy `Click`/`Tags` headers and Pushover `url`/`url_title` render
      correctly on a real phone.
- [ ] Optional: digest mode (one notification summarizing a sweep) to avoid
      alert spam when several products move at once.

---

## P1 — Review synthesis depth (Phase C is the portfolio piece)

The pipeline is end-to-end but uses simplified components vs. the design doc's
"port the legal-rag-local retrieval layer" intent.

- [ ] **Hybrid retrieval + reranking.** Replace the greedy centroid clustering
      (`reviews/segmentation.py`) and `HashEmbedder` with the real
      legal-rag-local stack: ChromaDB collection per product, RRF fusion,
      cross-encoder reranking. This is the "60–70% reusable" claim made real.
- [ ] **Validate Ollama path.** `OllamaLLM` / `OllamaEmbedder` have only ever
      run through their heuristic fallback in tests. Run against a real Qwen +
      `nomic-embed-text` on the Mac Studio; tune the extraction prompts.
- [ ] **YouTube transcripts.** `YouTubeSource` currently uses title +
      description only (caption download needs channel-owner OAuth). Add a
      transcript path (e.g. timed-text where available) — long-term-ownership
      reviews are the whole point of this tier.
- [ ] **Reddit depth.** Fetch top comments (richer use-case context than the
      post body) and support subreddit-scoped queries.
- [ ] **Expert source + robots.txt.** `ExpertSource` requires URLs supplied
      per product and does **not** yet enforce robots.txt despite the design
      doc's commitment. Add robots.txt checking and a small per-category URL
      registry (Rtings, DPReview-class).
- [x] **Contradiction resolution workflow.** ~~`set_contradiction_status` exists
      but no MCP tool exposes manual resolve/dismiss.~~ Done in v3 review pass:
      `resolve_contradiction` tool + open counts in `tracking_status`.

## P1 — Effective price (Phase B) ergonomics

- [x] **Stale-offer warning.** Done in v3 review pass: `OffersBook.stale()`
      (8-day contract) surfaced in `reload_offers`, `monitor_health`, and
      handoff briefings.
- [x] **YAML schema validation.** Done in v3 review pass: entry-level
      `OffersError` naming each bad section/index; a bad file never zeroes the
      active book or blocks daemon startup.
- [x] **Per-merchant alias map.** Done in v3 review pass: `merchants.py`
      canonicalization applied to offers, policy table, and executor registry.

## P1 — Monitor robustness

- [ ] **Per-product intervals** so different price ranges can poll at different
      cadences. (Loop-level ±10% jitter done in v3 review pass.)
- [ ] **API rate-limit / quota handling** with cooldown (Keepa tokens, Channel3
      free tier, Brave quota).
- [ ] **Keepa subscribe/cancel reminder.** The design doc subscribes Keepa only
      during active tracking windows of >$500 products. Emit a reminder to
      subscribe when such tracking starts and to cancel when none remain.
- [x] **Health/heartbeat.** Done in v3 review pass: sweep heartbeat in `meta` +
      `monitor_health` MCP tool with an overdue flag. (Push-based ntfy
      heartbeat still open if wanted.)

---

## P2 — Protocol-native checkout (deferred by design, §1.2)

- [x] **Dispatch seam** done in v3 review pass: `executors.py` capability
      registry (`PRODUCT_INTEL_ACP_MERCHANTS` opt-in), checked at handoff time.
- [ ] An actual `acp_checkout` implementation stays deferred until a relevant
      merchant opts into a protocol — do not build assuming coverage.

## P2 — Packaging & ops

- [x] **`.mcp.json` example** done in v3 review pass (`deploy/mcp.example.json`).
- [x] **CI workflow** done in v3 review pass (`.github/workflows/tests.yml`:
      pytest + demo smoke test, fully offline).
- [ ] **Secrets via keychain.** The launchd plist references a keychain wrapper
      that isn't provided — ship a small `security find-generic-password` shim so
      keys aren't plaintext in the plist `EnvironmentVariables`.
- [x] **Migrations.** Done in v3 review pass: versioned `MIGRATIONS` map +
      `schema_version` in `meta`; WAL + busy_timeout for the two-process
      (MCP server + launchd monitor) shape.

## P2 — Intent clarifier

- [ ] `clarify_intent` only persists the profile; the conversational refinement
      lives in the Claude session by design. Consider a `refine_intent` tool that
      proposes missing constraints (budget, kit vs body, condition) based on the
      category, so the gate has structured prompts.

---

## Known simplifications (intentional, documented here so they're not mistaken for bugs)

- **Heuristic LLM fallback** is keyword/regex — crude but deterministic; it
  only extracts what the text literally states. Production quality depends on
  the Ollama path (P1).
- **HashEmbedder** clusters short structured tuple strings well enough for the
  segmentation demo; it is *not* a semantic embedder for free-text retrieval
  (that's the ChromaDB/RRF work in P1).
- **Greedy clustering** is order-dependent at the threshold boundary; fine for
  small review sets, replace with the real retrieval stack for scale.
- **Cut list stays cut** (§6): autonomous cart staging, coupon auto-application,
  currency arbitrage, and the enterprise/LangGraph fork are out of scope by
  design — not backlog items.
