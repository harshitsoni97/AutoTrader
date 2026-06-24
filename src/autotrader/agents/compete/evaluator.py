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
    """Fetch today's closing price for symbol via yfinance (.NS suffix for NSE)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(period="1d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("[%s] Could not fetch closing price for %s: %s", AGENT_NAME, symbol, exc)
        return None


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

    # Fetch closing prices — cache per symbol to avoid duplicate yfinance calls
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

    # Rank by PnL % (None / errored competitors go last)
    ranked = sorted(
        updated,
        key=lambda r: r.get("hypothetical_pnl_pct") if r.get("hypothetical_pnl_pct") is not None else float("-inf"),
        reverse=True,
    )

    leaderboard = []
    for rank, r in enumerate(ranked, start=1):
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
