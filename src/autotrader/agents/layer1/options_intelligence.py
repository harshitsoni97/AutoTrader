"""Options Intelligence Agent — PCR, max pain, and IV skew from NSE options chain."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.nse_tools import get_options_chain
from autotrader.tools import upstox_data

logger = logging.getLogger(__name__)

AGENT_NAME = "OptionsIntelligenceAgent"

# PCR thresholds — empirically derived from NSE Nifty options historical data
PCR_BULLISH_THRESHOLD = 1.2   # Heavy put buying = hedging = potential support
PCR_BEARISH_THRESHOLD = 0.8   # Light put OI = complacency or directional call buying


def _interpret_options(pcr: float, iv_skew: float, max_pain: float, spot: float) -> str:
    """Derive a directional signal from options metrics.

    PCR > 1.2 and low skew = hedged market, likely support = bullish
    PCR < 0.8 and high skew = call buying with fear = bearish
    Max pain significantly above/below spot = price likely to drift toward pain
    """
    score = 0

    if pcr >= PCR_BULLISH_THRESHOLD:
        score += 1
    elif pcr <= PCR_BEARISH_THRESHOLD:
        score -= 1

    # High skew = downside fear premium
    if iv_skew > 3:
        score -= 1
    elif iv_skew < 0:
        score += 1

    # Max pain magnet: if spot is well below max pain, price tends to drift up
    if spot > 0 and max_pain > 0:
        gap_pct = (max_pain - spot) / spot * 100
        if gap_pct > 1.5:
            score += 1
        elif gap_pct < -1.5:
            score -= 1

    if score >= 1:
        return "bullish"
    elif score <= -1:
        return "bearish"
    return "neutral"


def options_intelligence_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Fetching options chain", AGENT_NAME)

    # Upstox is primary; NSE scraper is fallback
    chain = upstox_data.get_options_chain("Nifty 50") or get_options_chain("NIFTY")
    pcr = chain["pcr"]
    max_pain = chain["max_pain"]
    atm_iv = chain["atm_iv"]
    iv_skew = chain["iv_skew"]
    spot = chain.get("spot", 0.0)

    signal = _interpret_options(pcr, iv_skew, max_pain, spot)

    msg = create_message(
        source=AGENT_NAME,
        target="OpportunityScoringAgent",
        payload={
            "options_pcr": pcr,
            "options_max_pain": max_pain,
            "options_atm_iv": atm_iv,
            "options_iv_skew": iv_skew,
            "options_signal": signal,
        },
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="options_analyzed",
        data={
            "pcr": pcr,
            "max_pain": max_pain,
            "atm_iv": atm_iv,
            "iv_skew": iv_skew,
            "signal": signal,
            "spot": spot,
        },
    )

    logger.info(
        "[%s] PCR=%.3f MaxPain=%.0f ATM_IV=%.1f%% IVSkew=%.2f Signal=%s",
        AGENT_NAME, pcr, max_pain, atm_iv, iv_skew, signal,
    )

    return {
        "options_pcr": pcr,
        "options_max_pain": max_pain,
        "options_atm_iv": atm_iv,
        "options_iv_skew": iv_skew,
        "options_signal": signal,
        "messages": [msg],
        "audit_trail": [entry],
    }
