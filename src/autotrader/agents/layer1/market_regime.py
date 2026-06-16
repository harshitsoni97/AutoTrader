"""Market Regime Agent — determines current market conditions."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.market_data import (
    get_banknifty_data,
    get_global_markets,
    get_nifty_data,
    get_vix_data,
)
from autotrader.tools.nse_tools import get_fii_dii_data

logger = logging.getLogger(__name__)

AGENT_NAME = "MarketRegimeAgent"


def _pct_change(rows: list[dict], lookback: int = 5) -> float:
    if len(rows) < 2:
        return 0.0
    n = min(lookback, len(rows) - 1)
    return (rows[-1]["close"] / rows[-n]["close"] - 1) * 100


def _determine_regime(
    nifty_pct: float,
    vix: float,
    fii_net: float,
    global_pct: float,
) -> tuple[str, float]:
    """Map market conditions to a regime label with confidence score."""
    score_bull = 0.0
    score_bear = 0.0
    score_vol = 0.0

    # Nifty trend
    if nifty_pct > 1.0:
        score_bull += 30
    elif nifty_pct > 0.0:
        score_bull += 15
    elif nifty_pct < -1.0:
        score_bear += 30
    else:
        score_bear += 15

    # VIX
    if vix < 14:
        score_bull += 25
    elif vix < 18:
        score_bull += 10
    elif vix > 22:
        score_vol += 30
        score_bear += 10
    elif vix > 18:
        score_vol += 15

    # FII activity
    if fii_net > 1000:
        score_bull += 25
    elif fii_net > 0:
        score_bull += 10
    elif fii_net < -1000:
        score_bear += 25
    else:
        score_bear += 10

    # Global cue
    if global_pct > 0.5:
        score_bull += 20
    elif global_pct > 0:
        score_bull += 5
    elif global_pct < -0.5:
        score_bear += 20
    else:
        score_bear += 5

    total = score_bull + score_bear + score_vol
    if total == 0:
        return "range_bound", 0.5

    if score_vol > 35:
        if score_bear > score_bull:
            return "high_volatility", round(score_vol / total, 2)
        return "high_volatility", round(score_vol / total, 2)

    if score_bull > score_bear * 1.5:
        regime = "risk_on" if score_bull > 60 else "bullish"
        return regime, round(score_bull / total, 2)
    elif score_bear > score_bull * 1.5:
        regime = "risk_off" if score_bear > 60 else "bearish"
        return regime, round(score_bear / total, 2)
    else:
        return "range_bound", round(max(score_bull, score_bear) / total, 2)


def market_regime_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running market regime analysis", AGENT_NAME)

    nifty = get_nifty_data()
    banknifty = get_banknifty_data()
    vix_data = get_vix_data()
    fii_dii = get_fii_dii_data()
    global_mkts = get_global_markets()

    nifty_pct = _pct_change(nifty, 5)
    banknifty_pct = _pct_change(banknifty, 5)
    vix = vix_data.get("vix", 15.0)
    fii_net = fii_dii.get("fii_net", 0.0)
    sp500_pct = global_mkts.get("sp500_change_pct", 0.0)

    # Blend global signal
    global_pct = (sp500_pct + global_mkts.get("nasdaq_change_pct", 0.0)) / 2

    regime, confidence = _determine_regime(nifty_pct, vix, fii_net, global_pct)

    msg = create_message(
        source=AGENT_NAME,
        target="SectorRotationAgent",
        payload={
            "market_regime": regime,
            "confidence": confidence,
            "nifty_5d_pct": round(nifty_pct, 3),
            "banknifty_5d_pct": round(banknifty_pct, 3),
            "vix": vix,
            "fii_net": fii_net,
            "global_pct": round(global_pct, 3),
        },
    )

    entry = audit_entry(
        agent=AGENT_NAME,
        action="regime_determined",
        data={"regime": regime, "confidence": confidence, "vix": vix, "fii_net": fii_net},
    )

    logger.info("[%s] Regime=%s Confidence=%.2f VIX=%.1f", AGENT_NAME, regime, confidence, vix)

    return {
        "market_regime": regime,
        "market_confidence": confidence,
        "messages": [msg],
        "audit_trail": [entry],
    }
