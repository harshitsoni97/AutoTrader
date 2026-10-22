"""Strategist — the thin agentic layer of the hybrid design.

Runs ONCE pre-market. Reads the day's regime + recent journal P&L and produces a
DailyBias (favor/avoid strategies, de-risk after losses, tighten the throttle in weak
tape). It does NOT trade, size, or place orders — it only emits a clamped DailyBias that
the deterministic pipeline consumes. The guards still fire independently.

Two layers, same as market_regime:
  1. deterministic(...) — rule-based bias from what the journal taught us. Always runs,
     always safe, fully testable offline. This is the floor.
  2. enrich_with_llm(...) — OPTIONAL. An analysis LLM may adjust the bias, but its output
     is re-clamped, so it can only tighten/bias within the safe sandbox. Falls back to
     the deterministic bias on any error or when no LLM is configured.
"""

from __future__ import annotations

import structlog

from autotrader.agent.bias import DailyBias, clamp_bias

logger = structlog.get_logger()

# Regimes where momentum (ORB/composite breakouts) tends to bleed and mean-reversion
# tends to do better — the journal lesson (7/23, 8/14, 8/17-19 were all weak/choppy).
_WEAK_REGIMES = {"bearish", "risk_off", "range_bound", "high_volatility"}
_STRONG_REGIMES = {"risk_on", "bullish"}


def deterministic(regime: str, confidence: float, recent_pnls: list[float],
                  base_throttle_pct: float, base_max_positions: int) -> DailyBias:
    """Rule-based DailyBias from regime + recent daily P&L. Pure; always safe.

    recent_pnls: most-recent-LAST list of day P&Ls (e.g. last 5 days).
    """
    favor: list[str] = []
    avoid: list[str] = []
    risk_mult = 1.0
    throttle = None
    max_pos = None
    reasons: list[str] = []

    regime = (regime or "").lower()
    conf = confidence or 0.0

    # 1) Regime bias — momentum in strong tape, mean-reversion in weak/choppy tape.
    if regime in _STRONG_REGIMES and conf >= 0.75:
        favor = ["ORB", "COMPOSITE"]
        reasons.append(f"{regime} {conf:.0%}: favor momentum")
    elif regime in _WEAK_REGIMES or conf < 0.6:
        favor = ["MEAN_REVERSION"]
        throttle = -0.3                      # tighter throttle on weak/uncertain tape
        risk_mult = 0.8
        reasons.append(f"{regime} {conf:.0%}: favor mean-reversion, tighten throttle, de-risk")

    # 2) Losing-streak de-risking — the strongest, simplest discipline.
    tail = [p for p in recent_pnls[-3:]] if recent_pnls else []
    last2 = recent_pnls[-2:] if len(recent_pnls) >= 2 else []
    if len(last2) == 2 and all(p < 0 for p in last2):
        risk_mult = min(risk_mult, 0.6)
        throttle = -0.3 if throttle is None else max(throttle, -0.3)
        reasons.append("2 red days: de-risk to 0.6x")
    if len(tail) == 3 and all(p < 0 for p in tail):
        risk_mult = min(risk_mult, 0.4)
        max_pos = max(1, base_max_positions - 1)
        reasons.append("3 red days: de-risk to 0.4x, cut one position slot")

    raw = DailyBias(favor=favor, avoid=avoid, throttle_min_nifty_pct=throttle,
                    risk_mult=risk_mult, max_positions=max_pos,
                    rationale="; ".join(reasons) or "neutral day, no bias")
    return clamp_bias(raw, base_throttle_pct, base_max_positions)


def strategist_bias(regime: str, confidence: float, recent_pnls: list[float], cfg,
                    use_llm: bool = False) -> DailyBias:
    """Produce the day's DailyBias. Deterministic floor + optional LLM enrichment,
    the enriched result always re-clamped to the safe sandbox."""
    base_throttle = getattr(getattr(cfg, "market_throttle", None), "min_nifty_pct", -0.5)
    base_max_pos = getattr(getattr(cfg, "orb", None), "max_positions", 3)
    bias = deterministic(regime, confidence, recent_pnls, base_throttle, base_max_pos)

    if use_llm:
        try:
            enriched = _enrich_with_llm(bias, regime, confidence, recent_pnls, cfg)
            if enriched is not None:
                # Re-clamp: the LLM can only tighten/bias within the sandbox.
                bias = clamp_bias(enriched, base_throttle, base_max_pos)
        except Exception as exc:
            logger.warning("strategist_llm_enrich_failed", error=str(exc))
    return bias


def _enrich_with_llm(base: DailyBias, regime: str, confidence: float,
                     recent_pnls: list[float], cfg) -> DailyBias | None:
    """Optional LLM pass. Returns an (unclamped) DailyBias or None to keep the
    deterministic one. Kept minimal; the caller re-clamps. No-op if no analysis LLM."""
    from autotrader.core.llm import get_analysis_llm
    llm = get_analysis_llm(cfg.llm) if getattr(cfg, "llm", None) else None
    if llm is None:
        return None
    # Intentionally conservative: until a dedicated prompt/schema is validated, the LLM
    # layer is a no-op (returns the deterministic bias). Wiring a structured strategist
    # prompt is the next step, behind this same clamp.
    return None
