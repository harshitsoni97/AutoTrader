"""ORB Scan Agent — the validated opening-range-breakout edge, live.

Runs each intraday cycle (gated by config `orb.enabled`, default off). Uses the
FULL MARKET QUOTE (day OHLC + cumulative volume + last price) — a source that works
reliably during the session, unlike the candle endpoints which are empty outside it.

Mechanics (strategies.orb, snapshot-based, mirrors the validated backtest):
  - At ~OR-close (09:15 + or_min) it captures each name's opening range = the day
    high/low so far, plus its volume. Stored to disk (survives loop cycles/restarts).
  - On later cycles: breakout = last_price > OR-high AND this bar's volume (cumulative
    delta) > vol_mult × OR per-bar volume. Fires once per name per day.
  - Emits a trade_plan (entry=live, wide 2×OR-range stop, hold-to-close) that the
    EntryAgent books. Sized to risk `orb.risk_pct` of capital to the stop.
"""

from __future__ import annotations

import json
import structlog
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState
from autotrader.strategies.orb import capture_opening_range, snapshot_breakout

logger = structlog.get_logger()
AGENT_NAME = "ORBScanAgent"
_IST = timedelta(hours=5, minutes=30)
_STATE_DIR = Path(__file__).resolve().parents[4] / "reports"

_DEFAULT_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK",
    "LT", "ITC", "BHARTIARTL", "HINDUNILVR", "MARUTI", "TATASTEEL", "SUNPHARMA",
    "BAJFINANCE", "HCLTECH", "WIPRO", "ADANIPORTS", "TITAN", "ONGC", "POWERGRID",
    "NTPC", "COALINDIA", "JSWSTEEL", "DLF", "GODREJPROP", "DIVISLAB", "BEL",
]


def _state_path(run_date: str) -> Path:
    return _STATE_DIR / f"orb_state_{run_date}.json"


def _load_state(run_date: str) -> dict:
    p = _state_path(run_date)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _save_state(run_date: str, st: dict) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _state_path(run_date).write_text(json.dumps(st))
    except Exception as exc:
        logger.warning("[%s] orb_state save failed: %s", AGENT_NAME, exc)


def orb_scan_agent(state: TradingState) -> dict[str, Any]:
    cfg = load_config()
    orb = getattr(cfg, "orb", None)
    if not orb or not orb.enabled:
        return {}

    policy = cfg.trading_policy
    positions = state.get("positions", []) or []
    open_positions = [p for p in positions if (p.get("status") or "OPEN").upper() == "OPEN"]
    traded = {p.get("symbol") for p in positions if p.get("symbol")}
    for pl in state.get("trade_plans", []) or []:
        if pl.get("symbol"):
            traded.add(pl["symbol"])

    if len(open_positions) >= orb.max_positions:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="orb_at_position_cap",
                                            data={"open": len(open_positions)})]}

    now_ist = datetime.now(timezone.utc) + _IST
    open_ist = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    or_close = open_ist + timedelta(minutes=orb.or_min)
    if now_ist < or_close:
        logger.info("[%s] before OR window (now %s IST, OR closes %s)", AGENT_NAME,
                    now_ist.strftime("%H:%M"), or_close.strftime("%H:%M"))
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="before_or_window", data={})]}

    from autotrader.tools import upstox_data
    from autotrader.tools.price_utils import _instrument_key

    universe = orb.universe or _DEFAULT_UNIVERSE
    run_date = state.get("run_date") or now_ist.date().isoformat()

    # One batched full-quote call for the whole universe.
    keys = {sym: _instrument_key(sym) for sym in universe}
    quotes = upstox_data.get_full_quote([k for k in keys.values() if k]) or {}
    # Response is keyed by symbol (NSE_EQ|RELIANCE); map by the trailing symbol.
    by_sym = {k.split("|")[-1]: v for k, v in quotes.items()}

    orb_state = _load_state(run_date)
    total_capital = policy.total_capital
    risk_amt = total_capital * (orb.risk_pct / 100.0)
    max_notional = total_capital * getattr(policy, "max_capital_per_trade_pct", 50) / 100.0

    plans = []
    slots = orb.max_positions - len(open_positions)
    scanned = with_quote = captured = breakouts = 0
    for sym in universe:
        scanned += 1
        q = by_sym.get(sym)
        if not q or q.get("high", 0) <= 0:
            continue
        with_quote += 1

        if sym not in orb_state:
            orb_state[sym] = capture_opening_range(q, orb.or_min, orb.interval)
            captured += 1
            continue

        sig, orb_state[sym] = snapshot_breakout(orb_state[sym], q, orb.vol_mult, orb.stop_range_mult)
        if not sig:
            continue
        breakouts += 1
        if sym in traded or slots <= 0:
            continue

        live = q.get("last_price") or sig["breakout_price"]
        stop_dist = live - sig["stop"]
        if stop_dist <= 0:
            continue
        qty = max(1, int(risk_amt / stop_dist))
        qty = min(qty, max(1, int(max_notional / live)))
        plans.append({
            "symbol": sym, "qty": qty, "entry": round(live, 2), "stop": sig["stop"],
            "target1": round(live * 1.5, 2), "target2": round(live * 2.0, 2),
            "sector": "ORB", "pattern": "ORB_BREAKOUT", "score": 0,
            "strategy": "ORB", "orb_hold_to_close": True,
            "or_high": sig["or_high"], "or_low": sig["or_low"], "or_rng": sig["or_rng"],
        })
        traded.add(sym)
        slots -= 1
        logger.info("[%s] ORB breakout %s — entry %.2f stop %.2f qty %d (OR %.2f-%.2f)",
                    AGENT_NAME, sym, live, sig["stop"], qty, sig["or_low"], sig["or_high"])

    _save_state(run_date, orb_state)
    logger.info("[%s] scan: %d symbols, %d quoted, %d OR-captured, %d breakout(s)",
                AGENT_NAME, scanned, with_quote, captured, breakouts)

    if not plans:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="orb_scan",
                                            data={"quoted": with_quote, "captured": captured,
                                                  "breakouts": breakouts})]}
    return {
        "trade_plan": plans[0],
        "trade_plans": (state.get("trade_plans", []) or []) + plans,
        "audit_trail": [audit_entry(agent=AGENT_NAME, action="orb_plans_built",
                                    data={"symbols": [p["symbol"] for p in plans]})],
    }
