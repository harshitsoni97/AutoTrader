"""Short-term memory — 30-day rolling store backed by in-memory dict.

In production, replace the _store dict with a Qdrant/Weaviate/Pinecone client.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any


class ShortTermMemory:
    """Thread-safe in-memory short-term store with TTL expiry."""

    def __init__(self, retention_days: int = 30, ttl_days: int | None = None, embedder=None):
        # Accept both parameter names for compatibility
        self._retention_days = ttl_days if ttl_days is not None else retention_days
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._embedder = embedder

    def _get_embedder(self):
        if self._embedder is None:
            from autotrader.memory.embeddings import LocalHashEmbedder
            self._embedder = LocalHashEmbedder()
        return self._embedder

    def set_embedder(self, embedder) -> None:
        self._embedder = embedder

    def store(self, key: str, value: Any, ttl_days: int | None = None) -> None:
        ttl = ttl_days or self._retention_days
        expiry = datetime.now(timezone.utc) + timedelta(days=ttl)
        with self._lock:
            self._store[key] = {
                "value": value,
                "stored_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expiry.isoformat(),
                "embedding": self._get_embedder().embed(f"{key} {json.dumps(value, default=str)}"),
            }

    def retrieve(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            if datetime.fromisoformat(entry["expires_at"]) < datetime.now(timezone.utc):
                del self._store[key]
                return None
            return entry["value"]

    def search(self, query: str) -> list[dict]:
        """Simple keyword search over stored values."""
        query_lower = query.lower()
        results = []
        with self._lock:
            for key, entry in self._store.items():
                value_str = json.dumps(entry["value"]).lower()
                if query_lower in key.lower() or query_lower in value_str:
                    results.append({"key": key, "value": entry["value"], "stored_at": entry["stored_at"]})
        return results

    def search_scored(
        self,
        query: str,
        top_k: int = 5,
        w_recency: float = 0.34,
        w_relevance: float = 0.33,
        w_importance: float = 0.33,
        half_life_days: float = 30.0,
    ) -> list[dict]:
        """Recency × relevance retrieval over non-expired entries.

        Short-term entries have no intrinsic importance, so importance defaults
        to recency (newer working memories are treated as more salient).
        """
        from autotrader.memory.scoring import composite_score, cosine, recency_decay

        q_vec = self._get_embedder().embed(query)
        now = datetime.now(timezone.utc)
        results: list[dict] = []
        with self._lock:
            for key, entry in self._store.items():
                if datetime.fromisoformat(entry["expires_at"]) < now:
                    continue
                relevance = cosine(q_vec, entry.get("embedding", []))
                recency = recency_decay(entry.get("stored_at", ""), half_life_days, now)
                score = composite_score(recency, relevance, recency, w_recency, w_relevance, w_importance)
                results.append({
                    "key": key,
                    "value": entry["value"],
                    "stored_at": entry["stored_at"],
                    "retrieval_score": round(score, 4),
                })
        results.sort(key=lambda x: x["retrieval_score"], reverse=True)
        return results[:top_k]

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict of key → value (without TTL metadata)."""
        with self._lock:
            return {k: v["value"] for k, v in self._store.items()}

    def expire(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = datetime.now(timezone.utc)
        with self._lock:
            expired_keys = [
                k for k, v in self._store.items()
                if datetime.fromisoformat(v["expires_at"]) < now
            ]
            for k in expired_keys:
                del self._store[k]
        return len(expired_keys)

    def count(self) -> int:
        with self._lock:
            return len(self._store)

    def list_keys(self) -> list[str]:
        return self.keys()
