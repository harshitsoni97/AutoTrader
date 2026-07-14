"""Opportunity Scoring Agent — combines all signals into a composite score."""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.llm import ScoringReview, get_analysis_llm, structured
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.prompts import get_prompt
from autotrader.core.state import TradingState
from autotrader.tools.notifications import get_notifier

logger = structlog.get_logger()

AGENT_NAME = "OpportunityScoringAgent"

# Default weights (must sum to 1.0)
# options_sentiment gets a 5% weight carved from market_regime and sector_strength
WEIGHTS = {
    "market_regime": 0.18,
    "sector_strength": 0.17,
    "relative_strength": 0.20,
    "volume": 0.15,
    "catalyst": 0.15,
    "technical": 0.10,
    "options_sentiment": 0.05,
}


# Strong long-favorable regimes where broad breadth is itself the edge and a
# pure-technical momentum breakout is a legitimate setup even without news.
_STRONG_BULL = {"risk_on", "strong_bull", "bullish", "bull"}


def _composite_weights(regime: str, confidence: float) -> dict[str, float]:
    """Signal weights for the composite, regime-aware.

    Default: a news catalyst carries real weight (15%) — in a choppy or bearish
    tape you want a specific reason to be long.

    In a STRONG long-favorable regime (risk_on/bullish with high confidence),
    breadth is the edge: clean technical-momentum breakouts are tradeable without
    a catalyst, exactly as trend desks treat them. So the catalyst weight is
    reduced and reallocated to relative-strength/technical/regime — a no-news
    momentum name is no longer dead-weighted out of eligibility. The extension
    penalty still filters genuinely over-extended (chase) names, so this loosens
    the catalyst demand WITHOUT lowering the guard against chasing.
    """
    if regime in _STRONG_BULL and confidence >= 0.75:
        return {
            "market_regime": 0.19,
            "sector_strength": 0.17,
            "relative_strength": 0.26,
            "volume": 0.15,
            "catalyst": 0.05,
            "technical": 0.13,
            "options_sentiment": 0.05,
        }
    return WEIGHTS


def _overbought_penalty(rsi: float) -> float:
    """Composite penalty for an EXTREME-overbought RSI (the stretched tail only).

    Motivated by repeated live evidence (ANANDRATHI RSI 82 → −1.25%, GODREJPROP
    RSI 83, both flagged by every LLM) and the backtest showing selection over-
    weights extended momentum. This is deliberately soft and only bites the tail
    (RSI ≥ 75) so it does NOT fight normal trend momentum (RSI 60–70 stays clean);
    it just stops an RSI-82 name from topping the list where the extension-over-
    VWAP penalty can't see it (a stock grinding up with a rising VWAP).

      rsi < 75  → 0
      75–80     → 3
      80–85     → 7
      ≥ 85      → 12
    """
    if rsi >= 85:
        return 12.0
    if rsi >= 80:
        return 7.0
    if rsi >= 75:
        return 3.0
    return 0.0


def _extension_penalty_from_atr(ext_atr: float) -> float:
    """Same buckets as _extension_penalty, but from a stored extension-in-ATR value."""
    if ext_atr > 3.0:
        return 22.0
    if ext_atr > 2.0:
        return 14.0
    if ext_atr > 1.0:
        return 6.0
    return 0.0


def rescore_for_regime(item: dict, regime: str, confidence: float) -> float:
    """Recompute a scored candidate's composite under a DIFFERENT regime.

    Used intraday: a name filtered out pre-market (e.g. a weak/bearish open) may
    clear the bar once the regime improves, because the regime component and the
    catalyst-relaxation weights change. We reuse the stored per-signal sub-scores
    (component_scores) so no data re-fetch is needed — only the regime-dependent
    pieces are recomputed. Returns the new composite (extension penalty preserved).
    """
    comps = item.get("component_scores") or {}
    if not comps:
        return float(item.get("score", 0) or 0)
    w = _composite_weights(regime, confidence)
    regime_s = _market_regime_score(regime, confidence)
    composite = (
        regime_s * w["market_regime"]
        + comps.get("sector_strength", 0) * w["sector_strength"]
        + comps.get("relative_strength", 0) * w["relative_strength"]
        + comps.get("volume", 0) * w["volume"]
        + comps.get("catalyst", 0) * w["catalyst"]
        + comps.get("technical", 0) * w["technical"]
        + comps.get("options_sentiment", 0) * w["options_sentiment"]
    )
    composite -= _extension_penalty_from_atr(item.get("extension_atr", 0) or 0)
    composite -= _overbought_penalty(item.get("rsi", 50) or 50)
    return round(composite, 2)


def _market_regime_score(regime: str, confidence: float) -> float:
    # Confidence is applied at the composite level; base score reflects regime only
    base = {
        "risk_on": 95, "strong_bull": 100, "bullish": 80, "range_bound": 60,
        "bearish": 30, "risk_off": 20, "high_volatility": 40, "unknown": 50,
        "bull": 85,
    }.get(regime, 50)
    return float(base)


def _extension_penalty(candidate: dict) -> tuple[float, float]:
    """Penalty (composite points) for a price already extended above VWAP.

    Real-desk "don't chase" measure: how far price has run above VWAP in ATR
    units. A fresh breakout sits near VWAP; an exhausted one is several ATR above
    it and tends to mean-revert. This is the principled alternative to an RSI
    penalty (which fights momentum). Returns (penalty_points, extension_atr).

      ext <= 1.0 ATR   → 0     (healthy)
      1.0-2.0 ATR      → 6
      2.0-3.0 ATR      → 14
      > 3.0 ATR        → 22    (badly extended / chasing)
    """
    price = candidate.get("current_price", 0) or 0
    vwap = candidate.get("vwap", 0) or 0
    atr = candidate.get("daily_atr") or candidate.get("atr") or 0
    if not price or not vwap or not atr or atr <= 0:
        return 0.0, 0.0
    ext = (price - vwap) / atr
    if ext <= 1.0:
        pen = 0.0
    elif ext <= 2.0:
        pen = 6.0
    elif ext <= 3.0:
        pen = 14.0
    else:
        pen = 22.0
    return pen, round(ext, 2)


def _sector_score(symbol: str, sector_rankings: list[dict], top_sectors: list[str]) -> float:
    # Map symbol to sector — simplified lookup
    from autotrader.agents.layer1.catalyst_intelligence import _FALLBACK_SECTOR_WATCHLIST
    symbol_sector = None
    for sector, syms in _FALLBACK_SECTOR_WATCHLIST.items():
        if symbol in syms:
            symbol_sector = sector
            break
    if symbol_sector in top_sectors:
        rank = top_sectors.index(symbol_sector)
        return 100 - rank * 10
    # Check ranking list for momentum score
    for r in sector_rankings:
        if r.get("sector") == symbol_sector:
            raw_score = r.get("momentum_score", 0)
            return max(0, min(100, 50 + raw_score * 10))
    return 50.0


def _llm_review_opportunities(
    top3: list[dict],
    regime: str,
    confidence: float,
    llm: Any,
) -> dict:
    """Analysis LLM holistically reviews top 3 and can adjust the winner's score by ±5.

    Accepts a pre-built LangChain chat model so the compete coordinator can
    call this with any competitor's model without re-building LLM config.
    """
    if llm is None:
        return {}

    chain = structured(llm, ScoringReview)
    candidates_text = "\n".join(
        f"  {i+1}. {c['symbol']} | composite={c['score']:.1f} | pattern={c.get('pattern','NONE')} "
        f"| rsi={c.get('rsi',50):.0f} | catalyst={c['component_scores'].get('catalyst',0):.0f} "
        f"| tech={c['component_scores'].get('technical',0):.0f}"
        for i, c in enumerate(top3)
    )
    prompt = get_prompt(
        "scoring_review",
        regime=regime,
        confidence=confidence,
        candidates_text=candidates_text,
    )
    try:
        result: ScoringReview = chain.invoke(prompt)
        return {
            "top_symbol": result.top_symbol,
            "score_adjustment": result.score_adjustment,
            "rationale": result.rationale,
            "concerns": result.concerns,
            "pass_review": result.pass_review,
        }
    except Exception as exc:
        logger.warning("[%s] LLM scoring review failed: %s", AGENT_NAME, exc)
        return {}


def opportunity_scoring_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Scoring opportunities", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    candidates = state.get("candidates", [])
    market_regime = state.get("market_regime", "unknown")
    market_confidence = state.get("market_confidence", 0.5)
    sector_rankings = state.get("sector_rankings", [])
    top_sectors = state.get("top_sectors", [])

    regime_score = _market_regime_score(market_regime, market_confidence)
    weights = _composite_weights(market_regime, market_confidence)
    if weights is not WEIGHTS:
        logger.info("[%s] Strong %s (%.0f%%) — catalyst-relaxed weights (breadth is the edge)",
                    AGENT_NAME, market_regime, market_confidence * 100)

    # Options sentiment score (0-100) from PCR + IV skew + max pain alignment
    options_signal = state.get("options_signal", "neutral")
    options_s = {"bullish": 80.0, "neutral": 50.0, "bearish": 20.0}.get(options_signal, 50.0)

    scored: list[dict] = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        # Accept explicit sector field on candidate (from test states)
        candidate_sector = candidate.get("sector")
        if candidate_sector and candidate_sector in top_sectors:
            rank = top_sectors.index(candidate_sector)
            sector_s = 100 - rank * 10
        else:
            sector_s = _sector_score(symbol, sector_rankings, top_sectors)
        # Accept both field naming conventions
        rs_s = candidate.get("rs_score", candidate.get("relative_strength", 50.0))
        vol_s = candidate.get("volume_score", 0.0)
        tech_s = candidate.get("technical_score", 0.0)
        # Catalyst score: from candidate directly OR from state catalysts list
        cat_s = float(candidate.get("catalyst_score", 0))
        if cat_s == 0:
            cat_entry = next(
                (c for c in state.get("catalysts", []) if c.get("symbol") == symbol),
                None,
            )
            if cat_entry:
                cat_s = float(cat_entry.get("score", cat_entry.get("catalyst_score", 0)))

        composite = (
            regime_score * weights["market_regime"]
            + sector_s * weights["sector_strength"]
            + rs_s * weights["relative_strength"]
            + vol_s * weights["volume"]
            + cat_s * weights["catalyst"]
            + tech_s * weights["technical"]
            + options_s * weights["options_sentiment"]
        )
        # Extension penalty — dock names already run too far above VWAP (chasing).
        ext_pen, ext_atr = _extension_penalty(candidate)
        # Extreme-overbought dampener — dock the stretched-RSI tail (what the
        # extension-over-VWAP penalty misses on a grind-up).
        ob_pen = _overbought_penalty(candidate.get("rsi", 50) or 50)
        composite = round(composite - ext_pen - ob_pen, 2)
        if ext_pen or ob_pen:
            logger.info("[%s] %s penalties: extension -%.0f (%.1f ATR>VWAP), overbought -%.0f (RSI %.0f)",
                        AGENT_NAME, symbol, ext_pen, ext_atr, ob_pen, candidate.get("rsi", 50) or 50)

        scored.append({
            "symbol": symbol,
            "sector": candidate.get("sector"),
            "score": composite,
            "extension_atr": ext_atr,
            "composite_score": composite,  # alias for test compatibility
            "component_scores": {
                "market_regime": round(regime_score, 2),
                "sector_strength": round(sector_s, 2),
                "relative_strength": round(rs_s, 2),
                "volume": round(vol_s, 2),
                "catalyst": round(cat_s, 2),
                "technical": round(tech_s, 2),
                "options_sentiment": round(options_s, 2),
            },
            "current_price": candidate.get("current_price", 0),
            "pattern": candidate.get("pattern", "NONE"),
            "atr": candidate.get("atr", 0),
            "ema9": candidate.get("ema9", 0),
            "ema21": candidate.get("ema21", 0),
            "vwap": candidate.get("vwap", 0),
            "rsi": candidate.get("rsi", 50),
            "catalyst_reason": candidate.get("catalyst_reason", ""),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Optional LLM holistic review of the top 3 candidates
    llm_review: dict = {}
    if cfg.llm.enable_scoring_llm and scored:
        llm_review = _llm_review_opportunities(scored[:3], market_regime, market_confidence, get_analysis_llm(cfg.llm))
        if llm_review:
            top_sym = llm_review.get("top_symbol")
            adjustment = llm_review.get("score_adjustment", 0.0)
            veto = llm_review.get("pass_review", True) is False
            for s in scored:
                if s["symbol"] == top_sym:
                    s["score"] = round(s["score"] + adjustment, 2)
                    s["composite_score"] = s["score"]
                    s["llm_rationale"] = llm_review.get("rationale", "")
                    s["llm_concerns"] = llm_review.get("concerns", [])
                    if veto:
                        s["llm_vetoed"] = True
            if veto and scored and scored[0]["symbol"] == top_sym:
                scored = [s for s in scored if not s.get("llm_vetoed")]
            scored.sort(key=lambda x: x["score"], reverse=True)

    eligible = [s for s in scored if s["score"] >= policy.minimum_score]
    # Broader shortlist (pre-eligibility) the intraday hunt can reconsider if the
    # regime improves during the session — names just under the bar pre-market.
    watchlist = scored[:max(len(eligible), 10)]

    msg = create_message(
        source=AGENT_NAME,
        target="GovernanceAgent",
        payload={
            "total_candidates": len(scored),
            "eligible": len(eligible),
            "top_opportunity": eligible[0] if eligible else None,
        },
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="opportunities_scored",
        data={
            "total": len(scored),
            "eligible": len(eligible),
            "threshold": policy.minimum_score,
            "top": eligible[:3],
        },
    )

    logger.info("[%s] %d candidates scored, %d above threshold %.0f", AGENT_NAME, len(scored), len(eligible), policy.minimum_score)

    # Send pre-market summary notification (uses state context built up so far)
    summary_state = {**state, "scored_opportunities": eligible}
    get_notifier(cfg.notifications).notify_pre_market_summary(summary_state)

    return {
        "scored_opportunities": eligible,
        "watchlist": watchlist,
        "messages": [msg],
        "audit_trail": [entry],
    }
