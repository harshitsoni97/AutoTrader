"""Open Reconciliation Agent — validate pre-market picks against the actual open.

A pre-market plan is a prediction made before the market opens; the entry price
is a guess off the last pre-market quote. At the open we check each pick:

  - gapped past its entry (a BUY-LIMIT couldn't fill at a good price), or
  - already overextended (price >= target1 at the open)

→ cancel it and free the capital. Freed capital is redeployed to the next-best
candidate from the pre-market shortlist, but ONLY if that candidate is still
"good enough" (score, regime confidence, fillable, not overbought). Otherwise we
wait / don't trade — no forcing capital into a weak setup.

Runs as the first intraday step, before monitoring, so the book reflects what
actually filled.
"""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState
from autotrader.tools.price_utils import live_ltp

logger = structlog.get_logger()

AGENT_NAME = "OpenReconcileAgent"

# An RSI this hot means the redeploy candidate is itself an overbought chase.
_RSI_OVERBOUGHT = 80.0


def _open_price(symbol: str) -> float | None:
    """Actual session open (first 30-min candle) for a symbol; LTP as fallback."""
    try:
        from autotrader.agents.layer6.dry_run_pnl import _day_ohlc
        ohlc = _day_ohlc(symbol)
        if ohlc and ohlc.get("open"):
            return float(ohlc["open"])
    except Exception:
        pass
    return live_ltp(symbol)


def _good_enough(cand: dict, policy: Any, confidence: float) -> tuple[bool, str]:
    """Validate a redeploy candidate. Returns (ok, reason_if_not)."""
    min_trade = getattr(policy, "confidence_min_trade", policy.minimum_confidence)
    if confidence < min_trade:
        return False, f"regime confidence {confidence:.2f} < {min_trade}"
    score = cand.get("score", 0)
    if score < policy.minimum_score:
        return False, f"score {score:.1f} < {policy.minimum_score}"
    rsi = cand.get("rsi", 50) or 50
    if rsi >= _RSI_OVERBOUGHT:
        return False, f"RSI {rsi:.0f} overbought"
    # Fillable check: current price shouldn't have run far past the candidate price.
    cp = cand.get("current_price", 0) or 0
    live = _open_price(cand["symbol"])
    if cp and live and (live - cp) / cp * 100 > policy.open_reconcile_max_gap_pct:
        return False, f"gapped {(live - cp) / cp * 100:.2f}% past entry"
    return True, ""


def open_reconcile_agent(state: TradingState) -> dict[str, Any]:
    cfg = load_config()
    policy = cfg.trading_policy
    if not getattr(policy, "open_reconcile_enabled", True):
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="disabled", data={})]}

    positions = [p for p in state.get("positions", []) if p.get("status") == "OPEN"]
    if not positions:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="no_positions", data={})]}

    max_gap_pct = policy.open_reconcile_max_gap_pct
    kept: list[dict] = []
    cancelled: list[dict] = []

    for p in positions:
        sym = p["symbol"]
        entry = p.get("assumed_entry") or p.get("entry_price")
        target1 = p.get("target1")
        open_px = _open_price(sym)
        if not open_px or not entry:
            kept.append(p)  # can't validate — leave as is
            continue

        gap_pct = (open_px - entry) / entry * 100
        overextended = bool(target1 and open_px >= target1)
        gapped_away = gap_pct > max_gap_pct

        if overextended or gapped_away:
            reason = "overextended_at_open" if overextended else "gapped_past_entry"
            cancelled.append({**p, "status": "CANCELLED", "cancel_reason": reason,
                              "open_price": round(open_px, 2)})
            logger.info("[%s] Cancel %s at open — %s (entry %.2f, open %.2f, gap %.2f%%)",
                        AGENT_NAME, sym, reason, entry, open_px, gap_pct)
        else:
            # Keep, re-anchor cost basis to the actual open (kills the stale-entry bias).
            kept.append({**p, "entry_price": round(open_px, 2), "open_reconciled": True})

    audit: list[dict] = [audit_entry(agent=AGENT_NAME, action="reconciled", data={
        "kept": [p["symbol"] for p in kept],
        "cancelled": [{"symbol": p["symbol"], "reason": p.get("cancel_reason")} for p in cancelled],
    })]

    new_positions: list[dict] = []
    new_orders: list[dict] = []
    if cancelled:
        held = {p["symbol"] for p in kept} | {p["symbol"] for p in cancelled}
        confidence = state.get("market_confidence", 0.0)
        scored = state.get("scored_opportunities", [])
        # next-best candidates not already involved, validated "good enough"
        redeploy: list[dict] = []
        for cand in scored:
            if cand.get("symbol") in held:
                continue
            ok, why = _good_enough(cand, policy, confidence)
            if ok:
                redeploy.append(cand)
                if len(redeploy) >= len(cancelled):
                    break
            else:
                logger.info("[%s] Skip redeploy %s — %s", AGENT_NAME, cand.get("symbol"), why)

        if redeploy:
            from autotrader.agents.layer5.trade_construction import trade_construction_agent
            from autotrader.agents.layer5.execution import execution_agent
            sub_state = {**state, "scored_opportunities": redeploy, "positions": kept}
            tc = trade_construction_agent(sub_state)
            if tc.get("trade_plans"):
                ex = execution_agent({**sub_state, **tc})
                new_orders = ex.get("orders", [])
                # execution returns kept+added; isolate the added ones
                added = ex.get("positions", [])[len(kept):]
                new_positions = added
                audit += tc.get("audit_trail", []) + ex.get("audit_trail", [])
                logger.info("[%s] Redeployed %d freed slot(s) → %s",
                            AGENT_NAME, len(new_positions), [o.get("symbol") for o in new_orders])
        else:
            logger.info("[%s] No good-enough redeploy candidate — sitting out freed capital",
                        AGENT_NAME)
            audit.append(audit_entry(agent=AGENT_NAME, action="no_redeploy_waited", data={
                "freed": [p["symbol"] for p in cancelled]}))

    final_positions = kept + cancelled + new_positions
    out: dict[str, Any] = {
        "positions": final_positions,
        "audit_trail": audit,
    }
    if new_orders:
        out["orders"] = new_orders
    return out
