"""Long-Term Memory Agent — stores validated institutional trading knowledge."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.memory.long_term import LongTermMemory

logger = logging.getLogger(__name__)

AGENT_NAME = "LongTermMemoryAgent"
_ltm = LongTermMemory()


def _extract_pattern_from_outcomes(state: TradingState) -> list[dict]:
    """Extract repeatable patterns from today's trade outcomes."""
    outcomes = state.get("trade_outcomes", [])
    candidates = state.get("candidates", [])

    # Pattern: Volume > 3x + top sector → outcome
    high_vol_trades = [
        o for o in outcomes
        if any(
            c["symbol"] == o.get("symbol") and c.get("volume_ratio", 0) >= 3
            for c in candidates
        )
    ]
    patterns = []
    if high_vol_trades:
        wins = sum(1 for t in high_vol_trades if t.get("pnl", 0) > 0)
        patterns.append({
            "pattern_key": "high_volume_sector_leader",
            "description": "Volume > 3x average + Sector Leadership",
            "observations_today": len(high_vol_trades),
            "wins_today": wins,
        })

    # Pattern: ORB + EMA alignment → outcome
    orb_trades = [o for o in outcomes if o.get("pattern") == "ORB"]
    if orb_trades:
        wins = sum(1 for t in orb_trades if t.get("pnl", 0) > 0)
        patterns.append({
            "pattern_key": "orb_ema_aligned",
            "description": "Opening Range Breakout + EMA Alignment",
            "observations_today": len(orb_trades),
            "wins_today": wins,
        })

    return patterns


def long_term_memory_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Updating long-term memory", AGENT_NAME)

    cfg = load_config()
    mem_policy = cfg.memory_policy

    if mem_policy.auto_modify_strategy:
        logger.warning("[%s] auto_modify_strategy is True — this should remain False", AGENT_NAME)

    patterns = _extract_pattern_from_outcomes(state)
    stored: list[str] = []
    rejected: list[str] = []

    for pat in patterns:
        pattern_key = pat["pattern_key"]
        existing = _ltm.get_pattern(pattern_key)

        if existing:
            # Update existing
            new_obs = existing["observations"] + pat["observations_today"]
            new_wins = existing.get("wins", 0) + pat["wins_today"]
            win_rate = new_wins / new_obs if new_obs > 0 else 0
            confidence = min(0.99, existing["confidence"] + 0.01)
            _ltm.update_pattern(pattern_key, new_obs, win_rate, confidence)
            stored.append(pattern_key)
        else:
            # Check admission rules before storing new pattern
            obs = pat["observations_today"]
            if obs < mem_policy.minimum_observations:
                rejected.append(f"{pattern_key}: only {obs} obs (need {mem_policy.minimum_observations})")
                continue
            win_rate = pat["wins_today"] / obs if obs > 0 else 0
            initial_confidence = 0.60
            if initial_confidence < mem_policy.minimum_confidence:
                rejected.append(f"{pattern_key}: initial confidence {initial_confidence} too low")
                continue
            if win_rate <= 0.5:
                rejected.append(f"{pattern_key}: win rate {win_rate:.0%} — no positive expectancy")
                continue
            _ltm.store_pattern(
                pattern_key=pattern_key,
                description=pat["description"],
                observations=obs,
                win_rate=win_rate,
                confidence=initial_confidence,
            )
            stored.append(pattern_key)

    msg = create_message(
        source=AGENT_NAME, target="MemoryCompressionAgent",
        payload={"stored": stored, "rejected": rejected},
    )
    entry = audit_entry(agent=AGENT_NAME, action="memory_updated", data={
        "patterns_stored": stored,
        "patterns_rejected": rejected,
        "total_memories": _ltm.count(),
    })

    logger.info("[%s] Stored: %s | Rejected: %s", AGENT_NAME, stored, rejected)

    return {
        "messages": [msg],
        "audit_trail": [entry],
    }
