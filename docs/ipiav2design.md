# Integrated Product Intelligence Agent — v2 (Refined)

**Revision basis:** v1 was architecturally sound but written as if the data and execution layers were free, legal, and waiting to be called. Between late 2025 and mid-2026 the agentic commerce landscape hardened in ways that invalidate three of v1's assumptions and make two of its "missing tools" partially obsolete. This revision also re-grounds the design in the builder's actual stack (Claude Code + Max subscription, Mac Studio M3 Ultra running Ollama, existing MCP server patterns from legal-rag-local, Supercode's router) and actual purchase profile (electronics $300–$4,000, cameras, handheld gaming PCs, travel gear; heavy points/portal optimizer with a deep US credit card portfolio).

---

## 1. What the 2026 landscape changed

### 1.1 Browser-agent purchasing on major retailers is now legally contested, not just risky

In March 2026, Amazon won a preliminary injunction blocking Perplexity's Comet browser agent from accessing Amazon's logged-in systems. The court's key finding: the agent operated *with the user's permission but without Amazon's authorization*, and that distinction was enough for a likely CFAA claim. Amazon had warned Perplexity repeatedly, deployed blocks, and watched Perplexity circumvent them within 24 hours — the circumvention was held against them.

Design consequence: **v1's Purchase Staging Agent, as specified ("navigates to the retailer, stages the cart at the final confirmation screen"), is dead for Amazon and presumptively dead for any retailer whose ToS prohibits automated access — which is most of them.** It's not a safety preference anymore; it's litigation exposure plus an arms race you will lose. The user-consent-isn't-platform-authorization principle is now the operative legal frontier.

Replacement: the staging agent becomes a **Purchase Handoff Agent**. It does everything *except* touch the retailer's logged-in session:

- Verifies current price via legitimate data APIs (not session scraping)
- Confirms variant/bundle match against the research record
- Computes the effective-price stack (Section 4)
- Produces a deep link to the product page and a one-screen briefing
- The human opens the link and completes checkout manually

Where the retailer participates in an agentic commerce protocol (Section 1.2), the handoff can be upgraded to a protocol-native checkout — but that's the retailer opting in, which is categorically different from an agent masquerading as a browser.

### 1.2 Agentic commerce protocols exist now; design for them as an optional rail, not a foundation

The 2026 stack: ACP (OpenAI/Stripe/Meta), Google's UCP and AP2, Visa TAP, and MCP for tool transport. Reality check on adoption: OpenAI retired Instant Checkout in March 2026 after thin merchant uptake (roughly a dozen Shopify merchants plus Etsy) and pivoted to dedicated retailer apps. Amazon built its own walled-garden agents (Rufus, Buy for Me) and litigates against external ones.

Design consequence: protocol-native checkout is a **capability flag per retailer**, checked at handoff time. Architect the Purchase Handoff Agent with a pluggable executor interface — `manual_deeplink` (default, always works) and `acp_checkout` (when the merchant supports it). Do not build the system assuming ACP coverage; for the electronics categories this user buys (B&H, Adorama, Best Buy, Amazon, manufacturer-direct), coverage is effectively zero today.

### 1.3 The "missing tools" from v1 partially exist — compose, don't build

v1 proposed building a Cross-Retailer Price Normalization API from scratch. In 2026:

- **Rye Product Data API**: pass any merchant URL, get back normalized product data (stable ID, brand, price in subunits, availability enum, purchasability flag) with ~91% reliability across the top 450 US stores. This is most of the normalization layer.
- **Channel3**: natural-language product search across many merchants returning normalized candidates with live offers and merchant URLs; 1,000 free searches/month. This is most of the Discovery Worker's data layer.
- **Keepa**: Amazon price history via API. Paid (entry ~€49/mo for API access, token-metered), Amazon-only, but it is the canonical source. CamelCamelCamel has no public API — v1 cited it as if it did. **Correction: the v1 Price History Worker as specified cannot be built on CamelCamelCamel.**

What genuinely remains unbuilt: **bundle decomposition** (body-only vs. kit vs. bundle-with-accessories pricing) and **use-case review segmentation**. Those are the two pieces worth building. Everything else is integration work.

---

## 2. Revised architecture

Same skeleton — orchestrator, parallel workers, evaluator, human gates, continuous monitor — but with three structural changes.

### 2.1 Collapse six workers to four

v1's Product Discovery and Specification Extraction workers merge: Channel3/Rye return normalized product records that already carry the structured attributes the Spec worker was going to scrape. The remaining spec gap (deep technical attributes like sensor readout speed or IP ratings) folds into the Review Synthesis Worker's Tier 1 pass, because the expert review sites (Rtings, DPReview-style testing) are where verified spec data lives anyway — manufacturer pages are the *least* reliable spec source, which v1 itself noted.

v1's Constraint Validator stops being a worker and becomes what it actually is: a **deterministic post-filter** — a pure function over the candidate table, run after every worker pass and again at handoff time. No LLM call. This was implicit in v1's own Constraint Guardrail concept; making it a "worker" overstated it.

Final worker pool:

1. **Discovery + Spec Worker** (Channel3 search → Rye normalization → expert-review spec enrichment)
2. **Review Synthesis Worker** (the genuinely hard one — Section 3)
3. **Competitive Landscape Worker** (unchanged in concept; now also checks the *used/refurbished* market, which for camera bodies routinely beats new-at-discount)
4. **Price Context Worker** (Keepa for Amazon history; Rye/Channel3 live offers for cross-retailer spread; explicit "no history available" flag for non-Amazon retailers rather than fabricated trends)

### 2.2 Compute placement: subscription orchestration + local high-volume inference

The v1 framework section ("raw API calls + Python") ignores the operating constraint that has shaped every prior project in this stack: no per-token API costs.

- **Orchestrator and Evaluator**: Claude Code headless (`claude -p` / Agent SDK) running under the existing Max subscription. These are low-volume, high-reasoning calls — exactly what the subscription covers. This is the same pattern as llm-council and Supercode's predecessor.
- **Review Synthesis bulk processing**: local, on the Mac Studio M3 Ultra via Ollama. Ingesting 2,000+ reviews per candidate is precisely where token costs explode and precisely what a 96GB local box eats for free. Embedding, clustering, and first-pass summarization run on a local model (Qwen-class); only the final cross-candidate synthesis goes to Claude.
- **This is the legal-rag-local architecture transplanted.** Hybrid retrieval with RRF fusion + cross-encoder reranking over review corpora instead of judgments; ChromaDB collections per product instead of per matter; the same chunk → embed → cluster → rerank pipeline. An estimated 60–70% of that codebase's retrieval layer is directly reusable. The use-case segmentation engine v1 says "doesn't exist" is, structurally, a build-out of infrastructure that already runs on this machine.

### 2.3 Packaging: MCP server + scheduled daemon, not an app

Two deployment surfaces:

- **`product-intel` MCP server** (FastMCP, same packaging as legal-rag-local): exposes the research workflow as tools — `clarify_intent`, `build_candidate_pool`, `synthesize_reviews`, `get_price_context`, `compare_candidates`, `start_tracking`, `tracking_status`, `stage_handoff`. Research becomes an interactive Claude conversation rather than a fire-and-forget pipeline, which matters because the Intent Clarifier is conversational by nature and the Research Review Gate is just... talking to Claude with the evidence on screen.
- **Monitor daemon**: a Python scheduled job via `launchd` on the always-on Mac Studio. Skip n8n — it adds a service to maintain for what is a cron loop with three alert rules. Alerts via ntfy or Pushover to phone; alert payloads include the deep link and the effective-price computation.

State: SQLite, not v1's JSON file. The monitor accumulates price observations over months across multiple tracked products; JSON-file state is how you lose six months of price history to one bad write.

---

## 3. Review Synthesis Worker — the real build

Unchanged thesis from v1: use-case-segmented review synthesis is the clearest win and the piece no existing tool does. Refinements:

**Sourcing reality.** Tier 2 (retailer verified-purchase reviews) is the hardest tier to source legitimately post-Amazon-v-Perplexity. Amazon review scraping at scale is the same unauthorized-access posture that just lost in court. Practical sources, in order of defensibility: (a) expert review sites and their published test data via standard fetching (firecrawl, already connected, respecting robots.txt); (b) Reddit via the official API — community discussion is the richest use-case-context source anyway, since Redditors state their use case ("I shoot my kid's indoor volleyball games...") far more often than Amazon reviewers do; (c) YouTube transcripts via the Data API for long-term-ownership reviews; (d) retailer reviews only where an API or permissive access exists (Best Buy has a developer API; Amazon does not for reviews). Accept the coverage loss; flag it in output ("review base: 412 Reddit/forum posts, 14 expert reviews, 0 Amazon reviews — Amazon review sentiment unverifiable").

**Segmentation mechanics.** Two-stage: local LLM extracts a structured use-case tuple from each review/post (user type, frequency, environment, primary function, ownership duration); embeddings cluster the tuples; candidate scores are computed within the cluster nearest the user's constraint profile. Ownership duration is a first-class field — v1's insight about 6-month failure modes only works if you can filter to long-term owners, and Reddit/YouTube are the only tiers where duration is reliably stated.

**Contradiction ledger.** Keep v1's evaluator-flags-contradictions design, but persist contradictions as records (claim A, source A, claim B, source B, resolution status). The Layer 3 monitor reuses this: when a firmware update ships, the monitor checks it against open contradiction records ("manufacturer claims fix for the battery drain flagged by 23 long-term owners") rather than v1's vague "recall or quality update" trigger.

---

## 4. New layer: effective price, not sticker price

v1 tracks retail price. For this user, retail price is the wrong objective function. The actual cost of a $1,500 camera varies by $150–$300 depending on routing:

- **Shopping portal**: AAdvantage eShopping / Rakuten / Chase Travel portal multipliers (1–10x miles or 1–10% cash, periodically boosted). For an AA Executive Platinum flyer who values AA miles above 1.5cpp, a 6x portal day is a 9%+ effective discount.
- **Card-linked offers**: Amex Offers / Chase Offers ("$60 back on $300+ at Dell" class). These rotate; the monitor should check the relevant merchant's offer status at alert time.
- **Category multipliers**: which card earns most at this merchant (CSR travel/dining vs. Venture X catch-all vs. Amex Plat at specific merchants).
- **Coupon codes**: v1 assumed a "coupon aggregator API" — none exists with reliable public access (Honey/PayPal has no public API). Downgrade to best-effort: a web search pass at handoff time, surfaced as "codes to try," not auto-applied.

The alert therefore reads: *"B&H: $1,398 (−$101 vs. target). Effective price ~$1,278 after Rakuten 4% ($56) and Amex offer ($64, expires 6/30). Historical context: Amazon ATL is $1,349; B&H has no tracked history. Variant verified: body-only."* The portal/offer data is partly manual to maintain (a small YAML of active offers refreshed weekly is honest and sufficient); full automation here is a rabbit hole — Amex Offers have no API and scraping a logged-in Amex session is the same legal posture as scraping Amazon.

This layer is also where the system pays for itself: Keepa's API at ~€49/mo only makes sense during active tracking windows. Subscribe when tracking ≥1 product above ~$500, cancel between. Build the Price Context Worker against an interface so the Keepa dependency is swappable.

---

## 5. Monitoring layers — corrections

**Layer 1 (threshold)** and **Layer 2 (relative value)**: keep, with the caveat that Layer 2's percentile framing only works where history exists (Amazon via Keepa). For other retailers, the monitor builds its *own* history from the day tracking starts — honest cold-start, no fabricated baselines.

**Layer 3 (market context)**: keep, it remains the differentiator, but specify the sources: successor-product announcements via a weekly web-search pass scoped to the tracked products' brands; competitor price moves fall out of the existing multi-candidate tracking for free; discontinuation signals from Rye availability enums flipping to `out_of_stock`/`backorder` across retailers; recall/firmware events from the contradiction ledger as above.

**Phase 5 (post-purchase) — factual correction.** v1 claims "most retailers offer 30-day price protection." False in 2026 for the relevant retailers: Amazon eliminated general price-drop adjustments years ago; credit card price protection (Citi Price Rewind etc.) is dead industry-wide. What survives: Best Buy (My Best Buy Plus/Total members), Target (14 days), Costco (30 days), B&H and Adorama case-by-case. Rebuild Phase 5 as a per-retailer policy table checked at purchase time — if you bought from a retailer with no policy, the monitor says so once and stops, instead of generating false-hope alerts. Return-window and warranty-registration tracking: keep unchanged, they're cheap and real.

---

## 6. Honest assessment, revised

The v1 justification logic holds with two amendments.

**The research phase is now competing with frontier-model defaults.** ChatGPT shopping, Gemini/UCP, Rufus, and plain Claude-with-web-search all do passable multi-source product research in 2026. The defensible deltas are exactly three: (1) use-case-segmented review synthesis with a persistent contradiction ledger, (2) effective-price computation across the card/portal stack, (3) months-long stateful monitoring with market-context triggers. If a feature isn't one of those three, it's rebuilding what a chat session already does — cut it.

**Build order follows value density:**

- *Phase A (weekend-scale):* MCP server skeleton + Discovery (Channel3/Rye) + Keepa price context + SQLite tracking + launchd monitor with Layer 1/2 alerts. Ships the monitoring value immediately for the next real purchase.
- *Phase B:* Effective-price layer with the manual offer YAML. Small effort, immediate dollar returns.
- *Phase C:* Review synthesis pipeline (port legal-rag-local retrieval, Reddit/YouTube ingestion, local segmentation). The hard one; also the portfolio piece — a use-case-aware review segmentation engine on a local hybrid-retrieval stack is a substantially stronger demonstration artifact for solutions-architect conversations than another orchestration wrapper.
- *Phase D:* Layer 3 context monitoring + contradiction ledger integration.
- *Cut entirely:* autonomous cart staging, coupon auto-application, currency arbitrage (US-cards-only constraint makes import warranties a net negative), and the enterprise/LangGraph fork — that's a different product for a different buyer and its presence in a personal-tool design doc is scope creep.

The one-line test from v1 still applies, sharpened: this system justifies itself the day it catches one mid-cycle price anomaly on a $1,000+ tracked product that a single-retailer threshold tracker would have missed, or the day the effective-price layer routes one purchase through a boosted portal you'd have skipped. Both are single-purchase paybacks against roughly two weekends of build time on infrastructure that's 60% already written.
