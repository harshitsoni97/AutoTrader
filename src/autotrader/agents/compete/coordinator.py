"""Compete Coordinator — runs each provider stack end-to-end through all LLM enrichment steps.

Each stack is a complete fast + analysis tier configuration (e.g. Anthropic, OpenAI, Google).
The deterministic pipeline (market regime raw data, sector scores, RS, volume, technical composites)
runs once and is shared. The coordinator then, for each stack:

  1. Re-enriches raw_catalysts with the stack's fast LLM
  2. Re-enriches the regime with the stack's analysis LLM
  3. Recomputes per-candidate composite scores using enriched catalysts + regime
  4. Runs the scoring review with the stack's analysis LLM → picks top opportunity

This gives a true apples-to-apples comparison: each stack sees the same raw data but
interprets it through its own models.

In actual mode the primary stack's pick is promoted into scored_opportunities so
governance → risk → execution operate on the primary stack's decision.
"""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.agents.layer1.catalyst_intelligence import _llm_enrich_catalysts
from autotrader.agents.layer1.market_regime import _determine_regime, _llm_enrich_regime
from autotrader.agents.layer3.opportunity_scoring import (
    WEIGHTS,
    _llm_review_opportunities,
    _market_regime_score,
    _sector_score,
)
from autotrader.core.config import load_config
from autotrader.core.llm import make_stack_llms
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "CompeteCoordinator"


def _recompute_scores(
    candidates: list[dict],
    regime_score: float,
    options_s: float,
    sector_rankings: list[dict],
    top_sectors: list[str],
    enriched_catalysts: list[dict],
    top_sectors_state: list[str],
) -> list[dict]:
    """Recompute composite scores for each candidate using stack-specific inputs.

    Uses the stack's enriched catalyst scores and regime score; all other
    components (sector, RS, volume, technical) come from shared deterministic state.
    """
    cat_by_symbol = {c["symbol"]: float(c.get("catalyst_score", 0)) for c in enriched_catalysts}
    scored: list[dict] = []

    for candidate in candidates:
        symbol = candidate["symbol"]

        candidate_sector = candidate.get("sector")
        if candidate_sector and candidate_sector in top_sectors_state:
            rank = top_sectors_state.index(candidate_sector)
            sector_s = 100.0 - rank * 10
        else:
            sector_s = _sector_score(symbol, sector_rankings, top_sectors_state)

        rs_s = candidate.get("rs_score", candidate.get("relative_strength", 50.0))
        vol_s = candidate.get("volume_score", 0.0)
        tech_s = candidate.get("technical_score", 0.0)
        cat_s = cat_by_symbol.get(symbol, float(candidate.get("catalyst_score", 0)))

        composite = (
            regime_score * WEIGHTS["market_regime"]
            + sector_s * WEIGHTS["sector_strength"]
            + rs_s * WEIGHTS["relative_strength"]
            + vol_s * WEIGHTS["volume"]
            + cat_s * WEIGHTS["catalyst"]
            + tech_s * WEIGHTS["technical"]
            + options_s * WEIGHTS["options_sentiment"]
        )
        composite = round(composite, 2)

        scored.append({
            **candidate,
            "score": composite,
            "composite_score": composite,
            "component_scores": {
                "market_regime": round(regime_score, 2),
                "sector_strength": round(sector_s, 2),
                "relative_strength": round(rs_s, 2),
                "volume": round(vol_s, 2),
                "catalyst": round(cat_s, 2),
                "technical": round(tech_s, 2),
                "options_sentiment": round(options_s, 2),
            },
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def compete_coordinator_agent(state: TradingState) -> dict[str, Any]:
    """Run each stack's LLMs through the full enrichment pipeline and record picks."""
    cfg = load_config()
    if not cfg.compete.enabled:
        entry = audit_entry(agent=AGENT_NAME, action="compete_disabled", data={})
        return {"audit_trail": [entry]}

    # Shared inputs
    raw_catalysts = state.get("raw_catalysts", [])
    candidates = state.get("candidates", [])
    market_regime_det = state.get("market_regime", "unknown")     # deterministic regime label
    market_conf_det = state.get("market_confidence", 0.5)         # deterministic confidence
    nifty_pct = state.get("nifty_change_pct", 0.0)
    vix = state.get("india_vix", 15.0)
    fii_net = state.get("fii_net_cash", 0.0)
    global_pct = state.get("global_change_pct", 0.0)
    sector_rankings = state.get("sector_rankings", [])
    top_sectors = state.get("top_sectors", [])
    options_signal = state.get("options_signal", "neutral")
    options_s = {"bullish": 80.0, "neutral": 50.0, "bearish": 20.0}.get(options_signal, 50.0)

    if not candidates:
        logger.info("[%s] No candidates — skipping compete", AGENT_NAME)
        entry = audit_entry(agent=AGENT_NAME, action="no_candidates", data={})
        return {"competitor_results": [], "audit_trail": [entry]}

    results: list[dict] = []

    for stack in cfg.compete.stacks:
        logger.info("[%s] Running stack: %s", AGENT_NAME, stack.name)
        fast_llm, analysis_llm = make_stack_llms(stack)

        if fast_llm is None and analysis_llm is None:
            results.append({
                "name": stack.name,
                "fast_provider": stack.fast_provider,
                "analysis_provider": stack.analysis_provider,
                "pick": None,
                "adjusted_score": None,
                "regime": market_regime_det,
                "regime_confidence": market_conf_det,
                "rationale": "",
                "concerns": [],
                "pass_review": False,
                "entry_price": None,
                "error": "Both fast and analysis LLMs unavailable — missing API keys",
                "hypothetical_pnl_pct": None,
                "closing_price": None,
            })
            continue

        # Step 1: Catalyst enrichment with stack's fast LLM
        enriched_catalysts = _llm_enrich_catalysts(
            list(raw_catalysts), market_regime_det, fast_llm
        ) if raw_catalysts and fast_llm else list(raw_catalysts)

        # Step 2: Regime enrichment with stack's analysis LLM
        stack_regime, stack_confidence, _ = _llm_enrich_regime(
            market_regime_det, market_conf_det,
            nifty_pct, vix, fii_net, global_pct,
            analysis_llm,
        )
        regime_score = _market_regime_score(stack_regime, stack_confidence)

        # Step 3: Recompute composite scores using stack's enriched inputs
        stack_scored = _recompute_scores(
            candidates, regime_score, options_s,
            sector_rankings, top_sectors, enriched_catalysts, top_sectors,
        )
        top3 = stack_scored[:3]

        # Step 4: Scoring review with stack's analysis LLM
        review = _llm_review_opportunities(top3, stack_regime, stack_confidence, analysis_llm)

        if not review:
            top = top3[0] if top3 else {}
            ep = top.get("current_price", 0) or 0
            results.append({
                "name": stack.name,
                "fast_provider": stack.fast_provider,
                "fast_model": stack.fast_model,
                "analysis_provider": stack.analysis_provider,
                "analysis_model": stack.analysis_model,
                "pick": top.get("symbol"),
                "adjusted_score": top.get("score"),
                "regime": stack_regime,
                "regime_confidence": stack_confidence,
                "rationale": "LLM scoring review failed — using deterministic top pick",
                "concerns": [],
                "pass_review": True,
                "entry_price": ep,
                "hypothetical_stop":    round(ep * 0.985, 2) if ep else None,
                "hypothetical_target1": round(ep * 1.030, 2) if ep else None,
                "hypothetical_target2": round(ep * 1.045, 2) if ep else None,
                "stop_hit": False,
                "target1_hit": False,
                "target2_hit": False,
                "error": "scoring_review_failed",
                "hypothetical_pnl_pct": None,
                "closing_price": None,
            })
            continue

        top_sym = review.get("top_symbol") or (top3[0]["symbol"] if top3 else None)
        pick_candidate = next((s for s in top3 if s["symbol"] == top_sym), top3[0] if top3 else {})
        adjusted_score = round((pick_candidate.get("score") or 0) + review.get("score_adjustment", 0.0), 2)

        entry_price = pick_candidate.get("current_price", 0) or 0
        # Hypothetical levels: 1.5% stop, 3% target1 (2R), 4.5% target2 (3R)
        hyp_stop    = round(entry_price * 0.985, 2) if entry_price else None
        hyp_target1 = round(entry_price * 1.030, 2) if entry_price else None
        hyp_target2 = round(entry_price * 1.045, 2) if entry_price else None

        results.append({
            "name": stack.name,
            "fast_provider": stack.fast_provider,
            "fast_model": stack.fast_model,
            "analysis_provider": stack.analysis_provider,
            "analysis_model": stack.analysis_model,
            "pick": top_sym,
            "adjusted_score": adjusted_score,
            "regime": stack_regime,
            "regime_confidence": stack_confidence,
            "rationale": review.get("rationale", ""),
            "concerns": review.get("concerns", []),
            "pass_review": review.get("pass_review", True),
            "entry_price": entry_price,
            "hypothetical_stop": hyp_stop,
            "hypothetical_target1": hyp_target1,
            "hypothetical_target2": hyp_target2,
            "stop_hit": False,
            "target1_hit": False,
            "target2_hit": False,
            "error": None,
            "hypothetical_pnl_pct": None,
            "closing_price": None,
        })
        logger.info(
            "[%s] %s → regime=%s pick=%s score=%.1f pass=%s",
            AGENT_NAME, stack.name, stack_regime, top_sym, adjusted_score, review.get("pass_review", True),
        )

    # In actual mode with a primary: promote the primary stack's pick into scored_opportunities
    scored = list(state.get("scored_opportunities", []))
    primary_name = cfg.compete.primary
    if not cfg.compete.dry_run and primary_name:
        primary = next((r for r in results if r["name"] == primary_name and r["pick"] and r["pass_review"]), None)
        if primary:
            # Re-sort so primary's pick is first, with its adjusted score
            primary_sym = primary["pick"]
            updated = []
            for s in scored:
                if s["symbol"] == primary_sym:
                    s = {**s, "score": primary["adjusted_score"], "composite_score": primary["adjusted_score"]}
                updated.append(s)
            updated.sort(key=lambda x: x["score"], reverse=True)
            scored = updated
            logger.info("[%s] Primary stack %r promotes %s to top", AGENT_NAME, primary_name, primary_sym)
        else:
            logger.warning("[%s] Primary stack %r vetoed or unavailable — keeping deterministic order", AGENT_NAME, primary_name)

    msg = create_message(
        source=AGENT_NAME,
        target="CompeteEvaluator",
        payload={
            "stacks": len(results),
            "picks": {r["name"]: r["pick"] for r in results if r["pick"]},
        },
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="compete_round_complete",
        data={
            "stacks": len(results),
            "summary": [
                {
                    "name": r["name"],
                    "pick": r["pick"],
                    "score": r["adjusted_score"],
                    "regime": r.get("regime"),
                }
                for r in results
            ],
        },
    )

    return {
        "competitor_results": results,
        "scored_opportunities": scored,
        "messages": [msg],
        "audit_trail": [entry],
    }
