"""Risk Agent — validates trade quality before execution."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.broker_tools import MockBroker
from autotrader.tools.nse_tools import get_asm_gsm_list, get_corporate_actions

logger = logging.getLogger(__name__)

AGENT_NAME = "RiskAgent"
_broker = MockBroker()


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

    # 1. Liquidity check
    quote = _broker.get_quote(symbol)
    avg_vol_20d = quote.get("avg_volume_20d", 0)
    if avg_vol_20d < 500_000:
        return fail(f"{symbol}: Insufficient liquidity (avg vol {avg_vol_20d:,} < 500,000)")

    # 2. Spread check
    spread_pct = quote.get("spread_pct", 0)
    if spread_pct > 0.2:
        return fail(f"{symbol}: Spread too wide ({spread_pct:.4f}% > 0.2%)")

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

    # 5. ATR-based position sizing feasibility
    if atr == 0 or current_price == 0:
        return fail(f"{symbol}: Cannot calculate ATR or price is zero")

    stop_distance = atr * 1.5
    entry = current_price
    stop = entry - stop_distance
    target1 = entry + stop_distance * policy.min_risk_reward

    # 6. Risk/reward check
    if (target1 - entry) <= 0 or (entry - stop) <= 0:
        return fail(f"{symbol}: Invalid risk/reward geometry")

    rr = (target1 - entry) / (entry - stop)
    if rr < policy.min_risk_reward:
        return fail(f"{symbol}: R/R {rr:.2f} below minimum {policy.min_risk_reward}")

    # 7. Gap risk: if stock gapped up > 3% today, skip
    if top.get("ret_1d_pct", 0) > 3.0:
        return fail(f"{symbol}: Already gapped up {top.get('ret_1d_pct', 0):.1f}% — gap risk too high")

    reason = f"All risk checks passed. R/R={rr:.2f}, Spread={spread_pct:.4f}%"
    msg = create_message(
        source=AGENT_NAME, target="TradeConstructionAgent",
        payload={"risk_pass": True, "symbol": symbol, "reason": reason, "rr": rr},
    )
    entry_audit = audit_entry(agent=AGENT_NAME, action="risk_passed", data={
        "symbol": symbol,
        "rr": round(rr, 2),
        "spread_pct": spread_pct,
        "avg_vol_20d": avg_vol_20d,
    })
    logger.info("[%s] PASSED for %s: %s", AGENT_NAME, symbol, reason)

    return {
        "risk_passed": True,
        "risk_reason": reason,
        "messages": [msg],
        "audit_trail": [entry_audit],
    }
