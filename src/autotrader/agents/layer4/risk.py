"""Risk Agent — validates trade quality before execution."""

from __future__ import annotations

import structlog
from datetime import date
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.memory.long_term import LongTermMemory
from autotrader.tools.market_data import get_stock_data
from autotrader.tools.nse_tools import get_asm_gsm_list, get_corporate_actions

logger = structlog.get_logger()

AGENT_NAME = "RiskAgent"
MIN_AVG_VOLUME = 500_000
MAX_SPREAD_PCT = 0.2
KELLY_MIN_OBSERVATIONS = 20   # Don't trust Kelly until we have enough data
KELLY_MAX_FRACTION = 0.15     # Hard cap: never more than 15% of capital per trade


def _kelly_fraction(pattern_key: str, rr: float) -> tuple[float, str]:
    """Half-Kelly position fraction for a given pattern.

    f* = (b×p - q) / b  where b=RR, p=win_rate, q=1-p
    Returns (fraction 0-KELLY_MAX_FRACTION, reasoning_note).
    """
    mem = LongTermMemory()
    rec = mem.get_pattern(pattern_key)
    if rec is None or rec.get("observations", 0) < KELLY_MIN_OBSERVATIONS:
        return 0.0, f"kelly_skipped: <{KELLY_MIN_OBSERVATIONS} observations"
    p = rec["win_rate"]
    q = 1.0 - p
    b = max(rr, 0.1)
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0.0, f"kelly_negative: p={p:.2f} rr={b:.2f} — skip trade"
    half_kelly = full_kelly / 2.0
    fraction = min(half_kelly, KELLY_MAX_FRACTION)
    return round(fraction, 4), f"kelly: f*={full_kelly:.3f} half={half_kelly:.3f} cap={fraction:.3f} (p={p:.2f} obs={rec['observations']})"


def _get_liquidity(symbol: str, stock_data: dict | list | None = None) -> dict:
    """Extract liquidity metrics from stock data dict or raw OHLCV rows."""
    if isinstance(stock_data, dict):
        return {
            "avg_volume_20d": stock_data.get("avg_volume_20d", stock_data.get("volume", 0)),
            "price": stock_data.get("price", stock_data.get("ltp", 0)),
            "spread_pct": stock_data.get("spread_pct", 0.05),
            "atr": stock_data.get("atr", 0),
        }
    if isinstance(stock_data, list) and stock_data:
        vols = [r.get("volume", 0) for r in stock_data[-20:]]
        avg_vol = sum(vols) / len(vols) if vols else 0
        price = stock_data[-1].get("close", 0)
        # ATR approximation from last 14 days
        trs = []
        for i in range(1, min(15, len(stock_data))):
            hi, lo, prev_c = stock_data[-i]["high"], stock_data[-i]["low"], stock_data[-i - 1]["close"]
            trs.append(max(hi - lo, abs(hi - prev_c), abs(lo - prev_c)))
        atr = sum(trs) / len(trs) if trs else price * 0.015
        return {"avg_volume_20d": avg_vol, "price": price, "spread_pct": 0.05, "atr": atr}
    return {"avg_volume_20d": 0, "price": 0, "spread_pct": 0, "atr": 0}


def risk_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running risk validation", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    scored = state.get("scored_opportunities", [])

    def fail(reason: str) -> dict[str, Any]:
        msg = create_message(
            source=AGENT_NAME, target="TradeConstructionAgent",
            payload={"risk_pass": False, "reason": reason},
        )
        entry = audit_entry(agent=AGENT_NAME, action="risk_failed", data={"reason": reason})
        logger.warning("[%s] FAILED: %s", AGENT_NAME, reason)
        return {
            "risk_passed": False,
            "risk_reason": reason,
            "messages": [msg],
            "audit_trail": [entry],
        }

    if not scored:
        return fail("No scored opportunities to evaluate")

    top = scored[0]
    symbol = top["symbol"]
    current_price = top.get("current_price", 0)
    atr = top.get("atr", current_price * 0.015)

    # 1. Liquidity check — use get_stock_data (patchable in tests)
    raw_data = get_stock_data(symbol, period="25d")
    liquidity = _get_liquidity(symbol, raw_data)
    avg_vol_20d = liquidity["avg_volume_20d"]
    if current_price == 0:
        current_price = liquidity["price"]
    if atr == 0:
        atr = liquidity["atr"] or current_price * 0.015

    if avg_vol_20d < MIN_AVG_VOLUME:
        return fail(f"{symbol}: Insufficient volume (avg {avg_vol_20d:,.0f} < {MIN_AVG_VOLUME:,})")

    # 2. Spread check
    spread_pct = liquidity["spread_pct"]
    if spread_pct > MAX_SPREAD_PCT:
        return fail(f"{symbol}: Spread too wide ({spread_pct:.4f}% > {MAX_SPREAD_PCT}%)")

    # 3. ASM/GSM check
    asm_list = get_asm_gsm_list()
    if symbol in asm_list:
        return fail(f"{symbol}: Under ASM/GSM surveillance — not tradeable")

    # 4. Corporate actions check (no trading on ex-date)
    actions = get_corporate_actions(symbol)
    today = date.today().isoformat()
    for action in actions:
        if action.get("exDate", "") == today:
            return fail(f"{symbol}: Ex-date today for {action.get('subject', 'corporate action')}")

    # 5. ATR and price sanity
    if atr == 0 or current_price == 0:
        return fail(f"{symbol}: Cannot calculate ATR or price is zero")

    stop_distance = atr * 1.5
    entry_price = current_price
    stop = entry_price - stop_distance
    target1 = entry_price + stop_distance * policy.min_risk_reward

    # 6. Risk/reward check
    if (target1 - entry_price) <= 0 or (entry_price - stop) <= 0:
        return fail(f"{symbol}: Invalid risk/reward geometry")

    rr = (target1 - entry_price) / (entry_price - stop)
    if rr < policy.min_risk_reward:
        return fail(f"{symbol}: R/R {rr:.2f} below minimum {policy.min_risk_reward}")

    # 7. Gap risk: already up > 3% today
    if top.get("ret_1d_pct", 0) > 3.0:
        return fail(f"{symbol}: Gapped up {top.get('ret_1d_pct', 0):.1f}% — gap risk")

    # Kelly Criterion sizing — requires sufficient historical observations
    pattern = top.get("pattern", "UNKNOWN")
    kelly_fraction, kelly_note = _kelly_fraction(pattern, rr)

    reason = f"All risk checks passed. R/R={rr:.2f}, AvgVol={avg_vol_20d:,.0f}. {kelly_note}"
    msg = create_message(
        source=AGENT_NAME, target="TradeConstructionAgent",
        payload={"risk_pass": True, "symbol": symbol, "reason": reason, "rr": rr,
                 "kelly_fraction": kelly_fraction},
    )
    entry_audit = audit_entry(agent=AGENT_NAME, action="risk_passed", data={
        "symbol": symbol,
        "rr": round(rr, 2),
        "spread_pct": spread_pct,
        "avg_vol_20d": int(avg_vol_20d),
        "kelly_fraction": kelly_fraction,
        "kelly_note": kelly_note,
    })
    logger.info("[%s] PASSED for %s: %s", AGENT_NAME, symbol, reason)

    return {
        "risk_passed": True,
        "risk_reason": reason,
        "kelly_fraction": kelly_fraction,
        "messages": [msg],
        "audit_trail": [entry_audit],
    }
