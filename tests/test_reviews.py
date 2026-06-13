from product_intel.db import Store
from product_intel.reviews.embeddings import HashEmbedder
from product_intel.reviews.fakes import fake_sources
from product_intel.reviews.llm import HeuristicLLM
from product_intel.reviews.synthesis import ReviewSynthesizer


def make_synth(store=None):
    return ReviewSynthesizer(fake_sources(), HeuristicLLM(), HashEmbedder(), store=store)


def test_use_case_extraction():
    llm = HeuristicLLM()
    t = llm.extract_use_case(
        "I shoot my kid's indoor volleyball games every weekend. After 8 months "
        "the battery drains faster."
    )
    assert t.user_type == "parent"
    assert t.environment == "indoor"
    assert t.frequency == "weekly"
    assert t.primary_function == "sports"
    assert t.ownership_months == 8
    assert t.duration_bucket == "long"

    t2 = llm.extract_use_case("Been using it for 1 year of travel and hiking.")
    assert t2.user_type == "traveler"
    assert t2.ownership_months == 12


def test_claim_extraction_sentiment():
    from product_intel.reviews.models import ReviewDoc

    llm = HeuristicLLM()
    doc = ReviewDoc(
        source="reddit",
        text="The autofocus is fast and reliable, but the battery drains quickly.",
        url="https://r/1",
    )
    claims = {c.aspect: c.sentiment for c in llm.extract_claims(doc)}
    assert claims["autofocus"] == 1
    assert claims["battery"] == -1


def test_synthesis_segments_and_profile_match():
    out = make_synth().synthesize(
        "Sony a7 IV",
        user_profile="parent shooting my kid's indoor volleyball every weekend",
    )
    assert len(out["segments"]) >= 2  # distinct use-case clusters
    matched = out["matched_segment"]
    assert matched is not None
    assert "parent" in matched["label"]
    # The matched segment surfaces the long-term battery negative.
    negatives = {n["aspect"] for n in matched["negatives"]}
    assert "battery" in negatives
    positives = {p["aspect"] for p in matched["positives"]}
    assert "autofocus" in positives


def test_coverage_flags_amazon_unverifiable():
    out = make_synth().synthesize("Sony a7 IV")
    assert out["review_base"]["amazon"] == 0
    assert any("unverifiable" in n for n in out["coverage_notes"])
    assert out["review_base"]["reddit"] >= 1


def test_long_term_issues_filtered_by_duration():
    out = make_synth().synthesize("Sony a7 IV")
    lt = out["long_term"]
    assert lt["owners"] >= 2  # 8-month and 1-year owners in the seed corpus
    assert any(i["aspect"] == "battery" for i in lt["issues"])


def test_contradiction_persisted_to_ledger():
    store = Store(":memory:")
    out = make_synth(store).synthesize("Sony a7 IV", product_label="Sony a7 IV")
    aspects = {c["aspect"] for c in out["contradictions"]}
    assert "battery" in aspects  # long-term drain vs "excellent battery life"

    ledger = store.contradictions("Sony a7 IV", open_only=True)
    assert len(ledger) >= 1
    rec = next(r for r in ledger if r["aspect"] == "battery")
    assert rec["source_a"] != rec["source_b"]

    # Re-running does not duplicate open records.
    make_synth(store).synthesize("Sony a7 IV", product_label="Sony a7 IV")
    assert len(store.contradictions("Sony a7 IV", open_only=True)) == len(ledger)


def test_source_failure_is_coverage_note_not_crash():
    class Broken:
        name = "reddit"

        def fetch(self, query, limit=25):
            raise RuntimeError("boom")

    synth = ReviewSynthesizer([Broken()], HeuristicLLM(), HashEmbedder())
    out = synth.synthesize("anything")
    assert out["review_base"]["reddit"] == 0
    assert any("fetch failed" in n for n in out["coverage_notes"])
    assert "error" in out  # no reviews at all -> honest error
