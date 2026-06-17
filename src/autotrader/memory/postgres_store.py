"""Postgres + pgvector memory backend.

A single Postgres instance holds BOTH the relational pattern/observation state
AND the vector embeddings (via the ``pgvector`` extension), so the whole system
stays inside one cloud ecosystem (RDS / Cloud SQL / Azure Database). Mirrors the
public API of the in-memory ``LongTermMemory`` / ``ShortTermMemory`` so it drops
in behind the same factory.

Requires: ``psycopg2-binary`` (already a dependency) and the ``vector`` extension
enabled in the target database (``CREATE EXTENSION IF NOT EXISTS vector;``).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from autotrader.memory.embeddings import Embedder
from autotrader.memory.scoring import composite_score, cosine, recency_decay

logger = logging.getLogger(__name__)


def _connect(dsn: str):
    import psycopg2  # imported lazily so the package is optional
    conn = psycopg2.connect(dsn)
    try:
        # Register the pgvector adapter so Python lists serialise to `vector`
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
    except Exception as exc:  # pragma: no cover - depends on optional extension
        logger.debug("pgvector adapter not registered: %s", exc)
    return conn


class PgLongTermMemory:
    """Long-term validated-pattern store backed by Postgres + pgvector."""

    def __init__(self, dsn: str, embedder: Embedder):
        self._dsn = dsn
        self._embedder = embedder
        self._dim = embedder.dim
        self._init_schema()

    def _init_schema(self) -> None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS ltm_patterns (
                    memory_id    UUID PRIMARY KEY,
                    pattern_key  TEXT NOT NULL,
                    description  TEXT,
                    observations INTEGER DEFAULT 0,
                    wins         INTEGER DEFAULT 0,
                    win_rate     REAL DEFAULT 0,
                    confidence   REAL DEFAULT 0,
                    embedding    vector({self._dim}),
                    created_at   TIMESTAMPTZ DEFAULT now(),
                    last_updated TIMESTAMPTZ DEFAULT now()
                );
                """
            )
            conn.commit()

    def store_pattern(
        self,
        pattern: str | None = None,
        observations: int = 0,
        win_rate: float = 0.0,
        confidence: float = 0.0,
        pattern_key: str | None = None,
        description: str = "",
    ) -> str:
        key = pattern or pattern_key or "unknown"
        desc = description or key
        memory_id = str(uuid.uuid4())
        emb = self._embedder.embed(f"{key} {desc}")
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ltm_patterns
                  (memory_id, pattern_key, description, observations, wins, win_rate, confidence, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (memory_id, key, desc, observations, int(win_rate * observations),
                 round(win_rate, 4), round(confidence, 4), emb),
            )
            conn.commit()
        return memory_id

    def get_pattern(self, pattern_key: str) -> dict | None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id, pattern_key, description, observations, wins, win_rate, confidence "
                "FROM ltm_patterns WHERE pattern_key=%s LIMIT 1",
                (pattern_key,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def update_pattern(self, memory_id: str, new_observation: bool = True, win: bool | None = None,
                       observations: int | None = None, win_rate: float | None = None,
                       confidence: float | None = None) -> bool:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT observations, wins FROM ltm_patterns WHERE memory_id=%s", (memory_id,))
            row = cur.fetchone()
            if not row:
                return False
            obs, wins = row
            if new_observation:
                obs += 1
                if win is True:
                    wins += 1
            if observations is not None:
                obs = observations
            if win_rate is not None:
                wins = int(win_rate * obs)
            wr = round(wins / obs, 4) if obs > 0 else 0.0
            sets = ["observations=%s", "wins=%s", "win_rate=%s", "last_updated=now()"]
            vals: list[Any] = [obs, wins, wr]
            if confidence is not None:
                sets.append("confidence=%s")
                vals.append(round(min(0.99, confidence), 4))
            vals.append(memory_id)
            cur.execute(f"UPDATE ltm_patterns SET {', '.join(sets)} WHERE memory_id=%s", vals)
            conn.commit()
        return True

    def retrieve_patterns(self, min_confidence: float = 0.70) -> list[dict]:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id, pattern_key, description, observations, wins, win_rate, confidence "
                "FROM ltm_patterns WHERE confidence >= %s",
                (min_confidence,),
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def search_scored(self, query: str, top_k: int = 5, w_recency: float = 0.34,
                      w_relevance: float = 0.33, w_importance: float = 0.33,
                      half_life_days: float = 30.0) -> list[dict]:
        """Hybrid scoring: pgvector ANN for relevance, then re-rank with recency/importance."""
        q = self._embedder.embed(query)
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            # pgvector cosine distance operator <=> ; pull a wider candidate set then re-rank
            cur.execute(
                "SELECT memory_id, pattern_key, description, observations, wins, win_rate, confidence, "
                "last_updated, 1 - (embedding <=> %s::vector) AS relevance "
                "FROM ltm_patterns ORDER BY embedding <=> %s::vector LIMIT %s",
                (q, q, max(top_k * 4, 20)),
            )
            rows = cur.fetchall()
        scored: list[dict] = []
        for r in rows:
            d = self._row_to_dict(r[:7])
            relevance = max(0.0, min(1.0, float(r[8])))
            recency = recency_decay(r[7].isoformat() if r[7] else "", half_life_days)
            score = composite_score(recency, relevance, d["confidence"], w_recency, w_relevance, w_importance)
            scored.append({**d, "retrieval_score": round(score, 4)})
        scored.sort(key=lambda x: x["retrieval_score"], reverse=True)
        return scored[:top_k]

    def get_stats(self) -> dict[str, Any]:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*), coalesce(avg(win_rate),0), coalesce(avg(confidence),0) FROM ltm_patterns")
            n, awr, ac = cur.fetchone()
        return {"total_patterns": n, "avg_win_rate": round(float(awr), 4), "avg_confidence": round(float(ac), 4)}

    def count(self) -> int:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ltm_patterns")
            return cur.fetchone()[0]

    def all_patterns(self) -> list[dict]:
        return self.retrieve_patterns(min_confidence=-1.0)

    def expire_stale(self, min_confidence: float = 0.50, stale_threshold_days: int = 90) -> int:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ltm_patterns WHERE last_updated < now() - (%s || ' days')::interval "
                "AND confidence < %s",
                (stale_threshold_days, min_confidence),
            )
            n = cur.rowcount
            conn.commit()
        return n

    def boost_high_performers(self, win_rate_threshold: float = 0.65, boost: float = 0.02) -> int:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ltm_patterns SET confidence = LEAST(0.99, confidence + %s), last_updated=now() "
                "WHERE win_rate >= %s AND confidence < 0.97",
                (boost, win_rate_threshold),
            )
            n = cur.rowcount
            conn.commit()
        return n

    def merge_duplicates(self) -> int:
        """Collapse rows sharing a pattern_key into the highest-confidence one."""
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pattern_key FROM ltm_patterns GROUP BY pattern_key HAVING count(*) > 1"
            )
            dup_keys = [r[0] for r in cur.fetchall()]
            removed = 0
            for key in dup_keys:
                cur.execute(
                    "SELECT memory_id, observations, wins, confidence FROM ltm_patterns "
                    "WHERE pattern_key=%s ORDER BY confidence DESC",
                    (key,),
                )
                rows = cur.fetchall()
                keep = rows[0]
                tot_obs = sum(r[1] for r in rows)
                tot_wins = sum(r[2] for r in rows)
                wr = round(tot_wins / tot_obs, 4) if tot_obs > 0 else 0.0
                cur.execute(
                    "UPDATE ltm_patterns SET observations=%s, wins=%s, win_rate=%s, last_updated=now() "
                    "WHERE memory_id=%s",
                    (tot_obs, tot_wins, wr, keep[0]),
                )
                cur.execute(
                    "DELETE FROM ltm_patterns WHERE pattern_key=%s AND memory_id<>%s", (key, keep[0])
                )
                removed += len(rows) - 1
            conn.commit()
        return removed

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "memory_id": str(row[0]), "pattern_key": row[1], "description": row[2],
            "observations": row[3], "wins": row[4], "win_rate": float(row[5]),
            "confidence": float(row[6]),
        }


class PgShortTermMemory:
    """Short-term TTL store backed by Postgres + pgvector."""

    def __init__(self, dsn: str, embedder: Embedder, retention_days: int = 30):
        self._dsn = dsn
        self._embedder = embedder
        self._dim = embedder.dim
        self._retention_days = retention_days
        self._init_schema()

    def _init_schema(self) -> None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS stm_entries (
                    key        TEXT PRIMARY KEY,
                    value      JSONB,
                    embedding  vector({self._dim}),
                    stored_at  TIMESTAMPTZ DEFAULT now(),
                    expires_at TIMESTAMPTZ NOT NULL
                );
                """
            )
            conn.commit()

    def store(self, key: str, value: Any, ttl_days: int | None = None) -> None:
        from datetime import timedelta
        ttl = ttl_days or self._retention_days
        expiry = datetime.now(timezone.utc) + timedelta(days=ttl)
        emb = self._embedder.embed(f"{key} {json.dumps(value, default=str)}")
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stm_entries (key, value, embedding, stored_at, expires_at) "
                "VALUES (%s,%s,%s, now(), %s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, embedding=EXCLUDED.embedding, "
                "stored_at=now(), expires_at=EXCLUDED.expires_at",
                (key, json.dumps(value, default=str), emb, expiry),
            )
            conn.commit()

    def retrieve(self, key: str) -> Any | None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT value, expires_at FROM stm_entries WHERE key=%s", (key,))
            row = cur.fetchone()
            if not row:
                return None
            if row[1] < datetime.now(timezone.utc):
                cur.execute("DELETE FROM stm_entries WHERE key=%s", (key,))
                conn.commit()
                return None
            return row[0]

    def search_scored(self, query: str, top_k: int = 5, w_recency: float = 0.34,
                      w_relevance: float = 0.33, w_importance: float = 0.33,
                      half_life_days: float = 30.0) -> list[dict]:
        q = self._embedder.embed(query)
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT key, value, stored_at, 1 - (embedding <=> %s::vector) AS relevance "
                "FROM stm_entries WHERE expires_at >= now() "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (q, q, max(top_k * 4, 20)),
            )
            rows = cur.fetchall()
        out: list[dict] = []
        for key, value, stored_at, relevance in rows:
            rel = max(0.0, min(1.0, float(relevance)))
            rec = recency_decay(stored_at.isoformat() if stored_at else "", half_life_days)
            score = composite_score(rec, rel, rec, w_recency, w_relevance, w_importance)
            out.append({"key": key, "value": value, "stored_at": stored_at.isoformat() if stored_at else "",
                        "retrieval_score": round(score, 4)})
        out.sort(key=lambda x: x["retrieval_score"], reverse=True)
        return out[:top_k]

    def expire(self) -> int:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM stm_entries WHERE expires_at < now()")
            n = cur.rowcount
            conn.commit()
        return n

    def keys(self) -> list[str]:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT key FROM stm_entries")
            return [r[0] for r in cur.fetchall()]

    def list_keys(self) -> list[str]:
        return self.keys()

    def count(self) -> int:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM stm_entries")
            return cur.fetchone()[0]

    def to_dict(self) -> dict[str, Any]:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT key, value FROM stm_entries WHERE expires_at >= now()")
            return {k: v for k, v in cur.fetchall()}
