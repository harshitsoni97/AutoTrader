"""Compete Evaluator — end-of-day leaderboard.

Called in the post-market session. For each competitor in competitor_results,
fetches the day's closing price of their pick, computes hypothetical PnL
(entry was current_price at time of pick, exit is closing price), and
produces a ranked leaderboard written to the audit trail.
"""

from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "CompeteEvaluator"


def _fetch_closing_price(symbol: str) -> float | None:
    """Fetch TODAY's closing price for symbol via Upstox (shared price source).

    Uses the same Upstox-backed helper as dry_run_pnl so the leaderboard and the
    assumed P&L always reconcile. On a holiday/weekend the helper returns None
    (no phantom P&L from a stale prior-day candle).
    """
    from autotrader.tools.price_utils import closing_price
    return closing_price(symbol, require_today=True)


def _pnl_for_pick(entry_price: float | None, closing_price: float | None) -> float | None:
    """Return % PnL between entry and close, or None if prices unavailable."""
    if entry_price and closing_price and entry_price > 0:
        return round((closing_price - entry_price) / entry_price * 100, 3)
    return None


def compete_evaluator_agent(state: TradingState) -> dict[str, Any]:
    """Compute end-of-day hypothetical PnL for each competitor and produce a leaderboard."""
    cfg = load_config()
    if not cfg.compete.enabled:
        entry = audit_entry(agent=AGENT_NAME, action="compete_disabled", data={})
        return {"audit_trail": [entry]}

    competitor_results = list(state.get("competitor_results", []))
    if not competitor_results:
        entry = audit_entry(agent=AGENT_NAME, action="no_results", data={})
        return {"audit_trail": [entry]}

    # Fetch closing prices — cache per symbol to avoid duplicate Upstox calls
    price_cache: dict[str, float | None] = {}
    updated: list[dict] = []

    for result in competitor_results:
        pick = result.get("pick")
        if not pick:
            updated.append(result)
            continue

        if pick not in price_cache:
            price_cache[pick] = _fetch_closing_price(pick)

        closing = price_cache[pick]
        pnl_pct = _pnl_for_pick(result.get("entry_price"), closing)

        updated.append({
            **result,
            "closing_price": closing,
            "hypothetical_pnl_pct": pnl_pct,
        })

    # Rank by PnL % (None / errored competitors go last).
    # Joint ranking: stacks with identical P&L share the same rank (standard
    # competition ranking, "1224"). This avoids implying a winner when picks
    # tie — e.g. three stacks all flat on a quiet day are all rank #1, not
    # #1/#2/#3 decided by config order.
    def _pnl_key(r: dict) -> float:
        v = r.get("hypothetical_pnl_pct")
        return v if v is not None else float("-inf")

    ranked = sorted(updated, key=_pnl_key, reverse=True)
    pnl_values = [_pnl_key(r) for r in ranked]

    leaderboard = []
    for i, r in enumerate(ranked):
        # Competition rank = 1 + number of stacks strictly better than this one.
        rank = 1 + sum(1 for v in pnl_values if v > pnl_values[i])
        leaderboard.append({
            "rank": rank,
            "name": r["name"],
            "model": r.get("report_model") or r.get("analysis_model") or r.get("fast_model", ""),
            "pick": r["pick"],
            "entry_price": r.get("entry_price"),
            "closing_price": r.get("closing_price"),
            "pnl_pct": r.get("hypothetical_pnl_pct"),
            "pass_review": r.get("pass_review"),
            "rationale": r.get("rationale", "")[:120],
        })

    # Build a human-readable summary for the audit log
    lines = [f"=== Compete Mode Leaderboard — {datetime.now(timezone.utc).date()} ==="]
    for row in leaderboard:
        pnl_str = f"{row['pnl_pct']:+.3f}%" if row["pnl_pct"] is not None else "N/A"
        lines.append(
            f"  #{row['rank']} {row['name']:30s} | pick={row['pick'] or 'N/A':10s} | PnL={pnl_str}"
        )
    logger.info("\n".join(lines))

    # Pick attribution — record the deterministic top pick vs the LLM picks and
    # their day returns, so we can measure whether the LLM override adds value.
    try:
        from autotrader.core.pick_attribution import append as _attr_append
        scored = state.get("scored_opportunities", [])
        det = None
        if scored:
            d0 = scored[0]
            d_entry = d0.get("current_price")
            d_close = _fetch_closing_price(d0["symbol"]) if d0.get("symbol") else None
            d_ret = round((d_close - d_entry) / d_entry * 100, 3) if (d_entry and d_close) else None
            det = {"symbol": d0.get("symbol"), "return_pct": d_ret}
        llm_picks = [
            {"stack": r.get("name"), "symbol": r.get("pick"),
             "return_pct": r.get("hypothetical_pnl_pct")}
            for r in ranked if r.get("pick")
        ]
        _attr_append(
            run_date=state.get("run_date", ""),
            regime=state.get("market_regime", "unknown"),
            confidence=state.get("market_confidence", 0.0),
            deterministic=det,
            llm_picks=llm_picks,
        )
    except Exception as exc:
        logger.warning("pick_attribution_call_failed", error=str(exc))

    msg = create_message(
        source=AGENT_NAME,
        target="DailyLearningAgent",
        payload={"leaderboard": leaderboard},
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="compete_leaderboard",
        data={
            "date": datetime.now(timezone.utc).date().isoformat(),
            "leaderboard": leaderboard,
        },
    )

    return {
        "competitor_results": ranked,
        "messages": [msg],
        "audit_trail": [entry],
    }
