"""Long-term memory — permanent store for validated trading patterns.

Schema: {memory_id, pattern_key, description, observations, win_rate, confidence, last_updated}

In production: replace _store with Postgres table + pgvector or Qdrant.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


class LongTermMemory:
    """Singleton-like in-memory long-term knowledge store."""

    _instance: "LongTermMemory | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._store: dict[str, dict] = {}
                    instance._write_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def store_pattern(
        self,
        pattern_key: str,
        description: str,
        observations: int,
        win_rate: float,
        confidence: float,
    ) -> str:
        memory_id = f"LTM_{str(uuid.uuid4())[:8].upper()}"
        with self._write_lock:
            self._store[pattern_key] = {
                "memory_id": memory_id,
                "pattern_key": pattern_key,
                "description": description,
                "observations": observations,
                "wins": int(win_rate * observations),
                "win_rate": round(win_rate, 4),
                "confidence": round(confidence, 4),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
        return memory_id

    def get_pattern(self, pattern_key: str) -> dict | None:
        with self._write_lock:
            return self._store.get(pattern_key)

    def update_pattern(
        self,
        pattern_key: str,
        observations: int,
        win_rate: float,
        confidence: float,
    ) -> bool:
        with self._write_lock:
            if pattern_key not in self._store:
                return False
            entry = self._store[pattern_key]
            entry["observations"] = observations
            entry["wins"] = int(win_rate * observations)
            entry["win_rate"] = round(win_rate, 4)
            entry["confidence"] = round(min(0.99, confidence), 4)
            entry["last_updated"] = datetime.now(timezone.utc).isoformat()
            return True

    def retrieve_patterns(self, min_confidence: float = 0.70) -> list[dict]:
        with self._write_lock:
            return [
                p for p in self._store.values()
                if p["confidence"] >= min_confidence
            ]

    def expire_stale(self, min_confidence: float = 0.50, stale_threshold_days: int = 90) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_threshold_days)
        with self._write_lock:
            stale = [
                k for k, v in self._store.items()
                if (
                    datetime.fromisoformat(v["last_updated"]) < cutoff
                    and v["confidence"] < min_confidence
                )
            ]
            for k in stale:
                del self._store[k]
        return len(stale)

    def merge_duplicates(self) -> int:
        """Merge patterns with the same prefix (simplified dedup)."""
        with self._write_lock:
            seen_prefixes: dict[str, str] = {}
            to_delete: list[str] = []
            for key in list(self._store.keys()):
                prefix = key.split("_")[0]
                if prefix in seen_prefixes:
                    # Merge: keep higher confidence
                    existing_key = seen_prefixes[prefix]
                    existing = self._store[existing_key]
                    current = self._store[key]
                    if current["confidence"] > existing["confidence"]:
                        # Merge into current
                        current["observations"] += existing["observations"]
                        current["wins"] += existing.get("wins", 0)
                        current["win_rate"] = round(current["wins"] / current["observations"], 4) if current["observations"] > 0 else 0
                        to_delete.append(existing_key)
                        seen_prefixes[prefix] = key
                    else:
                        existing["observations"] += current["observations"]
                        existing["wins"] += current.get("wins", 0)
                        existing["win_rate"] = round(existing["wins"] / existing["observations"], 4) if existing["observations"] > 0 else 0
                        to_delete.append(key)
                else:
                    seen_prefixes[prefix] = key
            for k in to_delete:
                self._store.pop(k, None)
        return len(to_delete)

    def boost_high_performers(self, win_rate_threshold: float = 0.65, boost: float = 0.02) -> int:
        boosted = 0
        with self._write_lock:
            for entry in self._store.values():
                if entry["win_rate"] >= win_rate_threshold and entry["confidence"] < 0.97:
                    entry["confidence"] = round(min(0.99, entry["confidence"] + boost), 4)
                    boosted += 1
        return boosted

    def count(self) -> int:
        with self._write_lock:
            return len(self._store)

    def all_patterns(self) -> list[dict]:
        with self._write_lock:
            return list(self._store.values())
