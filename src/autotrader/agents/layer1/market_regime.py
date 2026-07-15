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

# Canonical regime labels the rest of the system gates on. The LLM enrichment can
# return free text ("mild_risk_on", "cautiously optimistic", …) which matches NONE
# of the coded sets and silently disables the hunt / catalyst relaxation / blocks.
_CANONICAL_REGIMES = {
    "risk_on", "bullish", "cautiously_bullish", "range_bound",
    "bearish", "risk_off", "high_volatility",
}


def _normalize_regime(label: str, fallback: str) -> str:
    """Map any LLM regime label onto the canonical set. Keep `fallback` (the
    deterministic regime) if the label is unmappable — never let free text through."""
    if not label:
        return fallback
    l = label.strip().lower().replace("-", "_").replace(" ", "_")
    if l in _CANONICAL_REGIMES:
        return l
    if any(w in l for w in ("range", "neutral", "sideways", "choppy", "consolidat")):
        return "range_bound"
    if "volatil" in l or "high_vol" in l:
        return "high_volatility"
    soft = any(w in l for w in ("mild", "cautious", "weak", "moderate", "slight", "tepid", "modest"))
    if "bear" in l or "risk_off" in l:
        return "risk_off" if ("strong" in l or "severe" in l or "risk_off" in l) else "bearish"
    if "bull" in l or "risk_on" in l:
        if soft:
            return "cautiously_bullish"
        return "risk_on" if ("strong" in l or "risk_on" in l) else "bullish"
    return fallback


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
    intraday_pct: float = 0.0,
    is_intraday: bool = False,
) -> tuple[str, float]:
    """Map market conditions to a regime label with confidence score.

    Inputs are deliberately short-horizon (2-day Nifty, overnight GIFT gap)
    so the regime reflects today's conditions, not a multi-week trend.
    GIFT Nifty gap is the highest-information pre-open forward signal and
    gets the most weight among same-day inputs.

    Intraday, the pre-open signals (GIFT gap especially) are stale — the market
    has already opened and may have PIVOTED off them. So when `is_intraday`, the
    GIFT gap is down-weighted and the LIVE intraday trend (`intraday_pct`, LTP vs
    today's open) is added as a dominant signal, letting a genuine same-day
    reversal move the regime the frozen pre-open signals couldn't see.
    """
    score_bull = 0.0
    score_bear = 0.0
    score_vol = 0.0

    # GIFT Nifty gap — best forward-looking signal for today's OPEN. Pre-market it
    # is the highest-weight input; intraday it is stale (the open already happened),
    # so we discount it and let the live intraday trend below carry the signal.
    gift_w = 0.35 if is_intraday else 1.0
    if gift_gap_pct > 0.5:
        score_bull += 30 * gift_w
    elif gift_gap_pct > 0.15:
        score_bull += 18 * gift_w
    elif gift_gap_pct > 0:
        score_bull += 8 * gift_w
    elif gift_gap_pct < -0.5:
        score_bear += 30 * gift_w
    elif gift_gap_pct < -0.15:
        score_bear += 18 * gift_w
    else:
        score_bear += 8 * gift_w

    # Live intraday trend — only fed intraday. LTP vs today's open is the freshest
    # read of the tape and gets top weight so a decisive same-day pivot (a weak
    # open that reverses green) can actually lift the regime, and a fade can sink it.
    if is_intraday:
        if intraday_pct >= 0.8:
            score_bull += 40
        elif intraday_pct >= 0.4:
            score_bull += 25
        elif intraday_pct >= 0.1:
            score_bull += 10
        elif intraday_pct <= -0.8:
            score_bear += 40
        elif intraday_pct <= -0.4:
            score_bear += 25
        elif intraday_pct <= -0.1:
            score_bear += 10

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
        canonical = _normalize_regime(result.regime_label, regime)
        enrichment = {
            "llm_regime_label": result.regime_label,       # raw, for audit
            "regime_normalized": canonical,
            "llm_confidence": result.adjusted_confidence,
            "llm_key_factors": result.key_factors,
            "llm_trading_implication": result.trading_implication,
        }
        if canonical != result.regime_label:
            logger.info("[%s] Normalized LLM regime '%s' → '%s'",
                        AGENT_NAME, result.regime_label, canonical)
        return canonical, result.adjusted_confidence, enrichment
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

    session_type = state.get("session_type", "pre_market")
    is_intraday = session_type == "intraday"

    # Live intraday trend — only meaningful (and only fetched) intraday. Captures a
    # same-day pivot the frozen pre-open signals (GIFT gap, 2-day return) can't see.
    intraday_pct = 0.0
    if is_intraday:
        live = upstox_data.get_nifty_intraday_move()
        if live:
            intraday_pct = live.get("pct_from_open", 0.0)
            logger.info("[%s] Live Nifty: LTP=%.1f  %+.2f%% from open  range_pos=%.2f",
                        AGENT_NAME, live.get("ltp", 0), intraday_pct, live.get("range_pos", 0))

    regime, confidence = _determine_regime(
        nifty_pct, vix, fii_net, global_pct, gift_gap_pct,
        intraday_pct=intraday_pct, is_intraday=is_intraday,
    )

    # Optional LLM synthesis — narrative enrichment + confidence refinement.
    # Intraday, the loop runs every few minutes; calling the analysis LLM each
    # cycle adds 5-15s latency for no benefit when nothing changed. So intraday
    # we ONLY enrich when the deterministic signal shifts materially — the regime
    # label flips, or VIX moves > 1 point vs the last cycle. Pre-market/post
    # always enrich (runs once).
    llm_enrichment: dict = {}
    cfg = load_config()

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
