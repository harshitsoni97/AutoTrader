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
    _class_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._store: dict[str, dict] = {}  # memory_id → entry
                    instance._write_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def store_pattern(
        self,
        pattern: str | None = None,
        observations: int = 0,
        win_rate: float = 0.0,
        confidence: float = 0.0,
        # Legacy keyword args kept for internal callers
        pattern_key: str | None = None,
        description: str = "",
    ) -> str:
        """Store a validated pattern. Returns memory_id (UUID4 string)."""
        key = pattern or pattern_key or "unknown"
        memory_id = str(uuid.uuid4())
        entry = {
            "memory_id": memory_id,
            "pattern_key": key,
            "description": description or key,
            "observations": observations,
            "wins": int(win_rate * observations),
            "win_rate": round(win_rate, 4),
            "confidence": round(confidence, 4),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with self._write_lock:
            self._store[memory_id] = entry
        return memory_id

    def get_pattern(self, pattern_key: str) -> dict | None:
        """Look up by pattern_key (not memory_id)."""
        with self._write_lock:
            for entry in self._store.values():
                if entry["pattern_key"] == pattern_key:
                    return entry
        return None

    def update_pattern(
        self,
        memory_id: str,
        new_observation: bool = True,
        win: bool | None = None,
        observations: int | None = None,
        win_rate: float | None = None,
        confidence: float | None = None,
    ) -> bool:
        """Update an existing pattern by memory_id.

        Simple form: update_pattern(mid, new_observation=True) increments observations by 1.
        Full form: pass observations, win_rate, confidence directly.
        """
        with self._write_lock:
            entry = self._store.get(memory_id)
            if not entry:
                return False
            if new_observation:
                entry["observations"] += 1
                if win is True:
                    entry["wins"] += 1
                if entry["observations"] > 0:
                    entry["win_rate"] = round(entry["wins"] / entry["observations"], 4)
            if observations is not None:
                entry["observations"] = observations
            if win_rate is not None:
                entry["win_rate"] = round(win_rate, 4)
                entry["wins"] = int(win_rate * entry["observations"])
            if confidence is not None:
                entry["confidence"] = round(min(0.99, confidence), 4)
            entry["last_updated"] = datetime.now(timezone.utc).isoformat()
        return True

    def retrieve_patterns(self, min_confidence: float = 0.70) -> list[dict]:
        with self._write_lock:
            return [p for p in self._store.values() if p["confidence"] >= min_confidence]

    def get_stats(self) -> dict[str, Any]:
        with self._write_lock:
            patterns = list(self._store.values())
        if not patterns:
            return {"total_patterns": 0, "avg_win_rate": 0.0, "avg_confidence": 0.0}
        return {
            "total_patterns": len(patterns),
            "avg_win_rate": round(sum(p["win_rate"] for p in patterns) / len(patterns), 4),
            "avg_confidence": round(sum(p["confidence"] for p in patterns) / len(patterns), 4),
        }

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
        """Merge entries with the same pattern_key, keeping the one with higher confidence."""
        with self._write_lock:
            by_key: dict[str, str] = {}  # pattern_key → memory_id to keep
            to_delete: list[str] = []
            for mid, entry in list(self._store.items()):
                key = entry["pattern_key"]
                if key in by_key:
                    existing_mid = by_key[key]
                    existing = self._store[existing_mid]
                    if entry["confidence"] > existing["confidence"]:
                        # Keep current, discard existing
                        existing["observations"] += entry["observations"]
                        existing["wins"] += entry.get("wins", 0)
                        if existing["observations"] > 0:
                            existing["win_rate"] = round(existing["wins"] / existing["observations"], 4)
                        to_delete.append(existing_mid)
                        by_key[key] = mid
                    else:
                        existing["observations"] += entry["observations"]
                        existing["wins"] += entry.get("wins", 0)
                        if existing["observations"] > 0:
                            existing["win_rate"] = round(existing["wins"] / existing["observations"], 4)
                        to_delete.append(mid)
                else:
                    by_key[key] = mid
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
