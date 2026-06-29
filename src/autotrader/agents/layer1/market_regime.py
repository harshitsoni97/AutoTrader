"""Market Regime Agent — determines current market conditions."""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.llm import RegimeEnrichment, get_analysis_llm, structured
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.prompts import get_prompt
from autotrader.core.state import TradingState
from autotrader.tools.market_data import (
    get_banknifty_data,
    get_gift_nifty,
    get_global_markets,
    get_nifty_data,
    get_vix_data,
)
from autotrader.tools.nse_tools import get_fii_dii_data, get_fii_derivatives
from autotrader.tools import upstox_data

logger = structlog.get_logger()
from autotrader.core.snapshot import stamp as _snapshot_stamp

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
    gift_gap_pct: float = 0.0,
) -> tuple[str, float]:
    """Map market conditions to a regime label with confidence score.

    Inputs are deliberately short-horizon (2-day Nifty, overnight GIFT gap)
    so the regime reflects today's conditions, not a multi-week trend.
    GIFT Nifty gap is the highest-information pre-open forward signal and
    gets the most weight among same-day inputs.
    """
    score_bull = 0.0
    score_bear = 0.0
    score_vol = 0.0

    # GIFT Nifty gap — best forward-looking signal for today's open (highest weight)
    if gift_gap_pct > 0.5:
        score_bull += 30
    elif gift_gap_pct > 0.15:
        score_bull += 18
    elif gift_gap_pct > 0:
        score_bull += 8
    elif gift_gap_pct < -0.5:
        score_bear += 30
    elif gift_gap_pct < -0.15:
        score_bear += 18
    else:
        score_bear += 8

    # Short-term Nifty trend (2-day return — intraday context, not multi-week trend)
    if nifty_pct > 1.0:
        score_bull += 20
    elif nifty_pct > 0.0:
        score_bull += 10
    elif nifty_pct < -1.0:
        score_bear += 20
    else:
        score_bear += 10

    # VIX — fear gauge (low VIX = complacency = bullish for trend-following)
    if vix < 14:
        score_bull += 25
    elif vix < 18:
        score_bull += 10
    elif vix > 22:
        score_vol += 30
        score_bear += 10
    elif vix > 18:
        score_vol += 15

    # FII activity (cash segment net flows)
    if fii_net > 1000:
        score_bull += 20
    elif fii_net > 0:
        score_bull += 8
    elif fii_net < -1000:
        score_bear += 20
    else:
        score_bear += 8

    # Global overnight cue (S&P 500 + Nasdaq avg)
    if global_pct > 0.5:
        score_bull += 15
    elif global_pct > 0:
        score_bull += 5
    elif global_pct < -0.5:
        score_bear += 15
    else:
        score_bear += 5

    total = score_bull + score_bear + score_vol
    if total == 0:
        return "range_bound", 0.5

    if score_vol > 35:
        return "high_volatility", round(score_vol / total, 2)

    if score_bull > score_bear * 1.5:
        regime = "risk_on" if score_bull > 60 else "bullish"
        return regime, round(score_bull / total, 2)
    elif score_bear > score_bull * 1.5:
        regime = "risk_off" if score_bear > 60 else "bearish"
        return regime, round(score_bear / total, 2)
    else:
        return "range_bound", round(max(score_bull, score_bear) / total, 2)


def _llm_enrich_regime(
    regime: str,
    confidence: float,
    nifty_pct: float,
    vix: float,
    fii_net: float,
    global_pct: float,
    llm: Any,
) -> tuple[str, float, dict]:
    """Use analysis-tier LLM to synthesize a regime narrative and adjust confidence.

    Accepts a pre-built LangChain chat model so the compete coordinator can
    call this with any stack's analysis LLM.

    Regime is a multiplier on all downstream scoring — misclassification on a
    risk_off day biases every signal bullish simultaneously. A capable model
    here is worth the extra ~$0.01/month.
    """
    if llm is None:
        return regime, confidence, {}

    chain = structured(llm, RegimeEnrichment)
    prompt = get_prompt(
        "regime_enrichment",
        nifty_pct=nifty_pct,
        vix=vix,
        fii_net=fii_net,
        global_pct=global_pct,
        regime=regime,
        confidence=confidence,
    )
    try:
        result: RegimeEnrichment = chain.invoke(prompt)
        enrichment = {
            "llm_regime_label": result.regime_label,
            "llm_confidence": result.adjusted_confidence,
            "llm_key_factors": result.key_factors,
            "llm_trading_implication": result.trading_implication,
        }
        return result.regime_label, result.adjusted_confidence, enrichment
    except Exception as exc:
        logger.warning("[%s] LLM regime enrichment failed: %s", AGENT_NAME, exc)
        return regime, confidence, {}


def _compute_gift_gap(gift_data: dict, nifty_rows: list[dict]) -> float:
    """Gap between GIFT Nifty futures price and previous Nifty close (%)."""
    gift_price = gift_data.get("gift_nifty", 0.0)
    prev_close = nifty_rows[-1]["close"] if nifty_rows else 0.0
    if prev_close > 0 and gift_price > 0:
        return round((gift_price / prev_close - 1) * 100, 3)
    return 0.0


def market_regime_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running market regime analysis", AGENT_NAME)

    # Upstox is primary; yfinance/NSE-scraper are fallbacks
    upstox_nifty = upstox_data.get_nifty_data()
    nifty = upstox_nifty if upstox_nifty else get_nifty_data()

    banknifty = get_banknifty_data()

    upstox_vix = upstox_data.get_vix()
    if upstox_vix:
        vix_data = upstox_vix
    else:
        vix_data = get_vix_data()

    upstox_fii = upstox_data.get_fii_data()
    if upstox_fii:
        fii_dii = {"fii_net": upstox_fii.get("fii_net", 0.0)}
        fii_deriv = {"fii_index_future_net": upstox_fii.get("fii_future_net", 0.0)}
    else:
        fii_dii = get_fii_dii_data()
        fii_deriv = get_fii_derivatives()

    global_mkts = get_global_markets()
    gift_data = get_gift_nifty()

    # Use 2-day return for intraday regime — shorter memory so today's
    # conditions dominate; GIFT gap provides the actual forward-looking signal.
    nifty_pct = _pct_change(nifty, 2)
    banknifty_pct = _pct_change(banknifty, 2)
    vix = vix_data.get("vix", 15.0)
    fii_net = fii_dii.get("fii_net", 0.0)
    sp500_pct = global_mkts.get("sp500_change_pct", 0.0)

    # Blend global signal
    global_pct = (sp500_pct + global_mkts.get("nasdaq_change_pct", 0.0)) / 2

    # FII derivatives net position (index futures long - short)
    fii_future_net = fii_deriv.get("fii_index_future_net", 0.0)

    # GIFT Nifty gap vs previous close — highest-information pre-open signal
    gift_gap_pct = _compute_gift_gap(gift_data, nifty)

    regime, confidence = _determine_regime(nifty_pct, vix, fii_net, global_pct, gift_gap_pct)

    # Optional LLM synthesis — narrative enrichment + confidence refinement.
    # Intraday, the loop runs every few minutes; calling the analysis LLM each
    # cycle adds 5-15s latency for no benefit when nothing changed. So intraday
    # we ONLY enrich when the deterministic signal shifts materially — the regime
    # label flips, or VIX moves > 1 point vs the last cycle. Pre-market/post
    # always enrich (runs once).
    llm_enrichment: dict = {}
    cfg = load_config()
    session_type = state.get("session_type", "pre_market")
    is_intraday = session_type == "intraday"

    prev_regime = state.get("market_regime")
    prev_vix = state.get("india_vix")
    material_shift = (
        prev_regime is None
        or regime != prev_regime
        or (prev_vix is not None and abs(vix - prev_vix) > 1.0)
    )
    should_enrich = cfg.llm.enable_regime_llm and (not is_intraday or material_shift)

    if should_enrich:
        regime, confidence, llm_enrichment = _llm_enrich_regime(
            regime, confidence, nifty_pct, vix, fii_net, global_pct, get_analysis_llm(cfg.llm)
        )
    elif is_intraday:
        logger.info("[%s] Intraday: deterministic regime unchanged (%s) — skipping LLM enrich",
                    AGENT_NAME, regime)

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
            "fii_future_net": fii_future_net,
            "gift_nifty_gap_pct": gift_gap_pct,
            "global_pct": round(global_pct, 3),
        },
    )

    entry = audit_entry(
        agent=AGENT_NAME,
        action="regime_determined",
        data={
            "regime": regime,
            "confidence": confidence,
            "vix": vix,
            "fii_net": fii_net,
            "fii_future_net": fii_future_net,
            "gift_nifty_gap_pct": gift_gap_pct,
            **llm_enrichment,
        },
    )

    logger.info(
        "[%s] Regime=%s Confidence=%.2f VIX=%.1f FIIFut=%+.0f GIFTGap=%+.2f%%",
        AGENT_NAME, regime, confidence, vix, fii_future_net, gift_gap_pct,
    )

    return {
        "market_regime": regime,
        "market_confidence": confidence,
        "fii_future_net": fii_future_net,
        "fii_net_cash": fii_net,
        "gift_nifty_gap_pct": gift_gap_pct,
        "nifty_change_pct": round(nifty_pct, 3),
        "india_vix": vix,
        "global_change_pct": round(global_pct, 3),
        "messages": [msg],
        "audit_trail": [entry],
        "data_fetch_log": _snapshot_stamp("market_regime"),
    }
