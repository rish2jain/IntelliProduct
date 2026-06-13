"""Embedding interface for use-case clustering.

Real path: Ollama embeddings (nomic-embed-text class) on the local box.
Offline path: a deterministic hashed bag-of-words embedder — sufficient for
clustering the short, structured tuple strings (``user_type=parent ...``)
where token overlap *is* the similarity signal.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


class HashEmbedder(Embedder):
    def __init__(self, dims: int = 64):
        self._dims = dims

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dims
        for token in text.lower().split():
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            vec[h % self._dims] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class OllamaEmbedder(Embedder):
    def __init__(self, base_url: str, model: str, timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        out: list[list[float]] = []
        with httpx.Client(timeout=self._timeout) as client:
            for t in texts:
                resp = client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self._model, "prompt": t},
                )
                resp.raise_for_status()
                out.append(resp.json()["embedding"])
        return out
