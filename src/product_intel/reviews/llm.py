"""Local-LLM interface for review processing (Section 2.2 compute placement).

Bulk per-review work (tuple extraction, claim extraction, first-pass
summarization) runs locally — this is precisely where token costs explode and
precisely what the Mac Studio's Ollama box eats for free. Only the final
cross-candidate synthesis belongs in the Claude conversation, which happens
naturally because the MCP tool returns structured output for Claude to reason
over.

Two implementations:
- :class:`OllamaLLM` — real local inference (Qwen-class model via Ollama).
- :class:`HeuristicLLM` — deterministic keyword/regex extractor used in tests,
  offline mode, and as a fallback when Ollama output fails to parse. Crude but
  honest: it extracts only what the text literally states.
"""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

from .models import Claim, ReviewDoc, UseCaseTuple


@runtime_checkable
class LocalLLM(Protocol):
    def extract_use_case(self, text: str) -> UseCaseTuple: ...

    def extract_claims(self, doc: ReviewDoc) -> list[Claim]: ...


# ---------------------------------------------------------------------------
# Heuristic implementation (deterministic, offline)
# ---------------------------------------------------------------------------

_USER_TYPES = {
    "parent": ["my kid", "my son", "my daughter", "my kids", "my child"],
    "professional": ["client", "wedding", "paid gig", "professional", "for work"],
    "traveler": ["travel", "trip", "backpacking", "vacation"],
    "hobbyist": ["hobby", "for fun", "enthusiast"],
}
_ENVIRONMENTS = {
    "indoor": ["indoor", "gym", "volleyball", "basketball", "concert", "low light"],
    "outdoor": ["outdoor", "hiking", "landscape", "wildlife", "beach", "mountain"],
    "studio": ["studio"],
}
_FREQUENCIES = {
    "daily": ["every day", "daily"],
    "weekly": ["every week", "weekly", "weekends", "every weekend"],
    "occasional": ["occasionally", "sometimes", "once in a while", "now and then"],
}
_FUNCTIONS = {
    "sports": ["sports", "volleyball", "basketball", "soccer", "action", "games"],
    "video": ["video", "vlog", "filming", "footage"],
    "portrait": ["portrait", "wedding", "headshot"],
    "landscape": ["landscape", "astro", "hiking", "wildlife"],
}

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(year|month|week)s?", re.IGNORECASE)
_DURATION_FACTOR = {"year": 12.0, "month": 1.0, "week": 0.25}

ASPECTS = {
    "battery": ["battery"],
    "autofocus": ["autofocus", "af tracking", "focus tracking"],
    "overheating": ["overheat"],
    "build": ["build quality", "weather seal"],
    "screen": ["screen", "evf", "viewfinder"],
    "weight": ["weight", "heavy", "light "],
}
_POS = [
    "great", "excellent", "amazing", "love", "fast", "reliable", "sharp",
    "solid", "impressive", "fantastic", "good", "accurate",
]
# Stems — matched with an open right boundary ("drain" covers drains/drained).
_NEG = [
    "drain", "die", "poor", "terrible", "bad", "awful", "slow",
    "misses", "overheat", "problem", "issue", "disappoint", "weak", "worse",
]


def _match(text: str, table: dict[str, list[str]], default: str = "general") -> str:
    low = text.lower()
    for label, keys in table.items():
        if any(k in low for k in keys):
            return label
    return default


class HeuristicLLM(LocalLLM):
    def extract_use_case(self, text: str) -> UseCaseTuple:
        months = None
        m = _DURATION_RE.search(text)
        if m:
            months = float(m.group(1)) * _DURATION_FACTOR[m.group(2).lower()]
        return UseCaseTuple(
            user_type=_match(text, _USER_TYPES),
            environment=_match(text, _ENVIRONMENTS),
            frequency=_match(text, _FREQUENCIES),
            primary_function=_match(text, _FUNCTIONS),
            ownership_months=months,
        )

    def extract_claims(self, doc: ReviewDoc) -> list[Claim]:
        # Clause-level scoring: split on sentence ends and contrastive "but"
        # so "autofocus is great, but the battery drains" yields one claim per
        # aspect instead of the sentiments cancelling across the sentence.
        clauses = re.split(r"[.;!?]|\bbut\b", doc.text, flags=re.IGNORECASE)
        claims: list[Claim] = []
        for aspect, keys in ASPECTS.items():
            for clause in clauses:
                low = clause.lower()
                if not any(k in low for k in keys):
                    continue
                # Positive words match exactly ("fast", not "faster"); negative
                # stems match prefixes ("drain" covers "drains", "drained").
                pos = sum(1 for w in _POS if re.search(rf"\b{w}\b", low))
                neg = sum(1 for w in _NEG if re.search(rf"\b{w}", low))
                if pos == neg:
                    continue
                claims.append(
                    Claim(
                        aspect=aspect,
                        sentiment=1 if pos > neg else -1,
                        quote=clause.strip()[:200],
                        source_ref=doc.ref,
                    )
                )
                break  # one claim per aspect per doc
        return claims


# ---------------------------------------------------------------------------
# Ollama implementation (real local inference)
# ---------------------------------------------------------------------------

_TUPLE_PROMPT = """Extract the reviewer's use case from this product review.
Respond with ONLY a JSON object: {{"user_type": "parent|professional|traveler|hobbyist|general",
"environment": "indoor|outdoor|studio|general", "frequency": "daily|weekly|occasional|general",
"primary_function": "sports|video|portrait|landscape|general",
"ownership_months": <number or null>}}

Review:
{text}"""

_CLAIMS_PROMPT = """List aspect-level claims this review makes about the product.
Aspects: battery, autofocus, overheating, build, screen, weight.
Respond with ONLY a JSON array: [{{"aspect": "...", "sentiment": 1 or -1, "quote": "..."}}]

Review:
{text}"""


class OllamaLLM(LocalLLM):
    """Local inference via Ollama; falls back to the heuristic on any failure
    so one mangled generation never sinks a 2,000-review ingest."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._fallback = HeuristicLLM()

    def _generate(self, prompt: str) -> str:
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base_url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False, "format": "json"},
            )
            resp.raise_for_status()
            return resp.json().get("response", "")

    def extract_use_case(self, text: str) -> UseCaseTuple:
        try:
            raw = json.loads(self._generate(_TUPLE_PROMPT.format(text=text[:4000])))
            months = raw.get("ownership_months")
            return UseCaseTuple(
                user_type=raw.get("user_type") or "general",
                environment=raw.get("environment") or "general",
                frequency=raw.get("frequency") or "general",
                primary_function=raw.get("primary_function") or "general",
                ownership_months=float(months) if months is not None else None,
            )
        except Exception:  # noqa: BLE001 — degrade to deterministic extraction
            return self._fallback.extract_use_case(text)

    def extract_claims(self, doc: ReviewDoc) -> list[Claim]:
        try:
            raw = json.loads(self._generate(_CLAIMS_PROMPT.format(text=doc.text[:4000])))
            return [
                Claim(
                    aspect=str(c["aspect"]),
                    sentiment=1 if int(c["sentiment"]) > 0 else -1,
                    quote=str(c.get("quote", ""))[:200],
                    source_ref=doc.ref,
                )
                for c in raw
                if c.get("aspect")
            ]
        except Exception:  # noqa: BLE001
            return self._fallback.extract_claims(doc)
