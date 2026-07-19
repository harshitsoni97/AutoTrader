"""ORB Scan Agent — turns the validated opening-range-breakout signal into plans.

Runs in the intraday loop (gated by config `orb.enabled`, default off). For a liquid
universe it pulls today's 5-min candles, detects a FRESH volume-confirmed breakout
(strategies.orb — the same logic the backtest validated), and emits trade_plans that
the existing EntryAgent books at the live price. Hold-to-close: no fixed target; the
MonitoringAgent squares off at the ORB square-off time. Wide stop = entry − 2×OR range.

Sizing: risk `orb.risk_pct` of capital across the stop distance, so each trade risks a
fixed fraction regardless of the stock's price/volatility.
"""

from __future__ import annotations

import structlog
from datetime import datetime, timezone, timedelta
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState
from autotrader.strategies.orb import orb_breakout_signal

logger = structlog.get_logger()
AGENT_NAME = "ORBScanAgent"
_IST = timedelta(hours=5, minutes=30)

# Liquid default universe (tight spreads) if config.orb.universe is empty. Mirrors the
# backtest's ORB set.
_DEFAULT_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK",
    "LT", "ITC", "BHARTIARTL", "HINDUNILVR", "MARUTI", "TATASTEEL", "SUNPHARMA",
    "BAJFINANCE", "HCLTECH", "WIPRO", "ADANIPORTS", "TITAN", "ONGC", "POWERGRID",
    "NTPC", "COALINDIA", "JSWSTEEL", "DLF", "GODREJPROP", "DIVISLAB", "BEL",
]


def orb_scan_agent(state: TradingState) -> dict[str, Any]:
    cfg = load_config()
    orb = getattr(cfg, "orb", None)
    if not orb or not orb.enabled:
        return {}

    policy = cfg.trading_policy
    positions = state.get("positions", []) or []
    open_positions = [p for p in positions if (p.get("status") or "OPEN").upper() == "OPEN"]
    traded = {p.get("symbol") for p in positions if p.get("symbol")}
    # Also skip names already planned this cycle / by other agents.
    for pl in state.get("trade_plans", []) or []:
        if pl.get("symbol"):
            traded.add(pl["symbol"])

    if len(open_positions) >= orb.max_positions:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="orb_at_position_cap",
                                            data={"open": len(open_positions)})]}

    # Before the OR window closes there's nothing to break out of yet.
    now_ist = datetime.now(timezone.utc) + _IST
    open_ist = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    if now_ist < open_ist + timedelta(minutes=orb.or_min):
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="before_or_window", data={})]}

    from autotrader.tools import upstox_data
    from autotrader.tools.price_utils import live_ltp, _instrument_key

    universe = orb.universe or _DEFAULT_UNIVERSE
    day = state.get("run_date") or now_ist.date().isoformat()
    total_capital = policy.total_capital
    risk_amt = total_capital * (orb.risk_pct / 100.0)

    plans = []
    slots = orb.max_positions - len(open_positions)
    for sym in universe:
        if slots <= 0:
            break
        if sym in traded:
            continue
        ikey = _instrument_key(sym)
        if not ikey:
            continue
        candles = upstox_data.get_historical_candles(ikey, "minutes", orb.interval, day, day)
        candles = [c for c in (candles or []) if str(c.get("timestamp", "")).startswith(day)]
        sig = orb_breakout_signal(candles, orb.or_min, orb.interval, orb.vol_mult, orb.stop_range_mult)
        if not sig:
            continue

        live = live_ltp(sym) or sig["breakout_close"]
        stop_dist = live - sig["stop"]
        if stop_dist <= 0:
            continue
        qty = max(1, int(risk_amt / stop_dist))
        # Cap notional to the per-trade capital limit.
        max_notional = total_capital * getattr(policy, "max_capital_per_trade_pct", 50) / 100.0
        qty = min(qty, max(1, int(max_notional / live)))

        plans.append({
            "symbol": sym, "qty": qty, "entry": round(live, 2), "stop": sig["stop"],
            # Hold to close: targets set far away so monitoring never books them; the
            # square-off handles the exit. Kept as fields entry/monitoring expect.
            "target1": round(live * 1.5, 2), "target2": round(live * 2.0, 2),
            "sector": "ORB", "pattern": "ORB_BREAKOUT", "score": 0,
            "strategy": "ORB", "orb_hold_to_close": True,
            "or_high": sig["or_high"], "or_low": sig["or_low"], "or_rng": sig["or_rng"],
        })
        traded.add(sym)
        slots -= 1
        logger.info("[%s] ORB breakout %s — entry %.2f stop %.2f qty %d (OR %.2f-%.2f)",
                    AGENT_NAME, sym, live, sig["stop"], qty, sig["or_low"], sig["or_high"])

    if not plans:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="no_orb_breakouts",
                                            data={"scanned": len(universe)})]}
    return {
        "trade_plan": plans[0],
        "trade_plans": (state.get("trade_plans", []) or []) + plans,
        "audit_trail": [audit_entry(agent=AGENT_NAME, action="orb_plans_built",
                                    data={"symbols": [p["symbol"] for p in plans]})],
    }
