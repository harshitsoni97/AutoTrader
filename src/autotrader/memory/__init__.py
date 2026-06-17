"""Memory stores with a configurable backend.

``get_long_term_memory`` / ``get_short_term_memory`` return the backend selected
in ``memory_policy.yaml`` (``memory_policy.backend.provider``):

* ``memory``  — in-process dict (default; zero dependencies)
* ``postgres`` — Postgres + pgvector (single-ecosystem: state + vectors in one DB)

Both fall back to the in-memory store if the Postgres driver/DSN/pgvector is
unavailable, so the platform always runs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from autotrader.memory.long_term import LongTermMemory
from autotrader.memory.short_term import ShortTermMemory

logger = logging.getLogger(__name__)


def _backend_cfg(cfg: Any | None):
    if cfg is not None:
        return cfg
    from autotrader.core.config import load_config
    return load_config().memory_policy.backend


def get_long_term_memory(cfg: Any | None = None):
    """Return the configured long-term memory store."""
    bcfg = _backend_cfg(cfg)
    from autotrader.memory.embeddings import get_embedder

    if (bcfg.provider or "memory").lower() == "postgres":
        dsn = os.getenv(bcfg.dsn_env)
        if not dsn:
            logger.warning("%s not set — falling back to in-memory long-term store", bcfg.dsn_env)
        else:
            try:
                from autotrader.memory.postgres_store import PgLongTermMemory
                return PgLongTermMemory(dsn, get_embedder(bcfg))
            except Exception as exc:
                logger.warning("Postgres long-term backend unavailable (%s) — using in-memory", exc)

    ltm = LongTermMemory()
    ltm.set_embedder(get_embedder(bcfg))
    return ltm


def get_short_term_memory(cfg: Any | None = None):
    """Return the configured short-term memory store."""
    bcfg = _backend_cfg(cfg)
    from autotrader.memory.embeddings import get_embedder

    retention = 30
    if cfg is None:
        try:
            from autotrader.core.config import load_config
            retention = load_config().memory_policy.short_term_retention_days
        except Exception:
            pass

    if (bcfg.provider or "memory").lower() == "postgres":
        dsn = os.getenv(bcfg.dsn_env)
        if not dsn:
            logger.warning("%s not set — falling back to in-memory short-term store", bcfg.dsn_env)
        else:
            try:
                from autotrader.memory.postgres_store import PgShortTermMemory
                return PgShortTermMemory(dsn, get_embedder(bcfg), retention_days=retention)
            except Exception as exc:
                logger.warning("Postgres short-term backend unavailable (%s) — using in-memory", exc)

    return ShortTermMemory(retention_days=retention, embedder=get_embedder(bcfg))


__all__ = [
    "LongTermMemory",
    "ShortTermMemory",
    "get_long_term_memory",
    "get_short_term_memory",
]
