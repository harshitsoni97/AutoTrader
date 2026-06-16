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

    def __init__(self, retention_days: int = 30, ttl_days: int | None = None):
        # Accept both parameter names for compatibility
        self._retention_days = ttl_days if ttl_days is not None else retention_days
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()

    def store(self, key: str, value: Any, ttl_days: int | None = None) -> None:
        ttl = ttl_days or self._retention_days
        expiry = datetime.now(timezone.utc) + timedelta(days=ttl)
        with self._lock:
            self._store[key] = {
                "value": value,
                "stored_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expiry.isoformat(),
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
