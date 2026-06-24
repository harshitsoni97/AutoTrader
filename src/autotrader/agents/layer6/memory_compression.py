"""Memory Compression Agent — prevents memory bloat via dedup and expiry."""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.memory import get_long_term_memory

logger = structlog.get_logger()

AGENT_NAME = "MemoryCompressionAgent"
_ltm = get_long_term_memory()


def memory_compression_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running memory compression", AGENT_NAME)

    cfg = load_config()
    before_count = _ltm.count()

    # Expire stale memories (no update in 90+ trading days — approximated by low confidence)
    expired = _ltm.expire_stale(min_confidence=0.50, stale_threshold_days=90)

    # Merge near-duplicate patterns (same key prefix)
    merged = _ltm.merge_duplicates()

    # Boost confidence of repeatedly validated patterns
    boosted = _ltm.boost_high_performers(win_rate_threshold=0.65, boost=0.02)

    after_count = _ltm.count()

    msg = create_message(
        source=AGENT_NAME, target="END",
        payload={"before": before_count, "after": after_count, "expired": expired, "merged": merged, "boosted": boosted},
    )
    entry = audit_entry(agent=AGENT_NAME, action="compression_complete", data={
        "before": before_count, "after": after_count,
        "expired": expired, "merged": merged, "boosted": boosted,
    })

    logger.info("[%s] Memory: %d → %d (expired=%d merged=%d boosted=%d)", AGENT_NAME, before_count, after_count, expired, merged, boosted)

    return {
        "messages": [msg],
        "audit_trail": [entry],
    }
