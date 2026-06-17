"""Pluggable text embeddings for semantic memory retrieval.

The default ``local`` embedder is deterministic, dependency-free, and needs no
network — suitable for dev/CI and as a universal fallback. Cloud-native
providers (Bedrock, Vertex, Azure OpenAI, Voyage) can be selected so embeddings
stay inside whichever single cloud ecosystem you standardise on; each is added
as a small function in ``_PROVIDERS`` and falls back to ``local`` if its client
or credentials are unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder:
    """Base embedder interface."""

    dim: int = 256

    def embed(self, text: str) -> list[float]:  # pragma: no cover - interface
        raise NotImplementedError


class LocalHashEmbedder(Embedder):
    """Deterministic hashed bag-of-words embedding, L2-normalised.

    Cosine similarity between two LocalHashEmbedder vectors reflects token
    overlap — good enough for keyword-ish semantic recall without any service.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall((text or "").lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


def _make_local(cfg: Any) -> Embedder:
    return LocalHashEmbedder(dim=getattr(cfg, "embedding_dim", 256))


def _make_voyage(cfg: Any) -> Embedder:
    # Anthropic's recommended embedding partner; requires `voyageai` + VOYAGE_API_KEY.
    import os

    import voyageai  # type: ignore

    client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    model = getattr(cfg, "embedding_model", "voyage-3")

    class _Voyage(Embedder):
        dim = getattr(cfg, "embedding_dim", 1024)

        def embed(self, text: str) -> list[float]:
            return client.embed([text], model=model, input_type="document").embeddings[0]

    return _Voyage()


# provider name -> factory. Add cloud-native providers here to keep one ecosystem.
_PROVIDERS: dict[str, Callable[[Any], Embedder]] = {
    "local": _make_local,
    "voyage": _make_voyage,
    # "bedrock": _make_bedrock,   # AWS Titan embeddings
    # "vertex": _make_vertex,     # GCP text-embedding-*
    # "azure_openai": _make_azure_openai,
}


def get_embedder(cfg: Any) -> Embedder:
    """Return the configured embedder, falling back to local on any failure."""
    provider = (getattr(cfg, "embedding_provider", "local") or "local").lower()
    factory = _PROVIDERS.get(provider)
    if factory is None:
        logger.warning("Unknown embedding_provider '%s' — using local", provider)
        return _make_local(cfg)
    try:
        return factory(cfg)
    except Exception as exc:
        logger.warning("Embedding provider '%s' unavailable (%s) — using local", provider, exc)
        return _make_local(cfg)
