"""Compete Coordinator — runs every configured competitor model through the scoring review.

The deterministic pipeline (market regime → sector → RS → volume → technical → composite)
runs once and is shared. Each competitor independently calls the LLM scoring review with
their model and records their top pick, score adjustment, and rationale.

In actual mode the primary competitor's pick is promoted to scored_opportunities so the
downstream governance → risk → execution nodes operate on the primary's decision.
"""

from __future__ import annotations

import logging
from typing import Any

from autotrader.agents.layer3.opportunity_scoring import _llm_review_opportunities
from autotrader.core.config import load_config
from autotrader.core.llm import make_competitor_llm
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = logging.getLogger(__name__)

AGENT_NAME = "CompeteCoordinator"


def compete_coordinator_agent(state: TradingState) -> dict[str, Any]:
    """Run each competitor's LLM over the shared scored_opportunities pool.

    Returns:
        competitor_results: list of per-competitor dicts with pick, score,
            rationale, concerns, and pass_review flag.
        scored_opportunities: updated list when actual mode + primary set
            (primary competitor's adjusted score replaces the default).
    """
    cfg = load_config()
    if not cfg.compete.enabled:
        entry = audit_entry(agent=AGENT_NAME, action="compete_disabled", data={})
        return {"audit_trail": [entry]}

    scored = list(state.get("scored_opportunities", []))
    top3 = scored[:3]
    regime = state.get("market_regime", "unknown")
    confidence = state.get("market_confidence", 0.5)

    if not top3:
        logger.info("[%s] No scored opportunities — skipping compete", AGENT_NAME)
        entry = audit_entry(agent=AGENT_NAME, action="no_opportunities", data={})
        return {"competitor_results": [], "audit_trail": [entry]}

    results: list[dict] = []

    for competitor in cfg.compete.competitors:
        logger.info("[%s] Running competitor: %s (%s/%s)", AGENT_NAME, competitor.name, competitor.provider, competitor.model)

        llm = make_competitor_llm(competitor)
        if llm is None:
            results.append({
                "name": competitor.name,
                "provider": competitor.provider,
                "model": competitor.model,
                "pick": None,
                "adjusted_score": None,
                "rationale": "",
                "concerns": [],
                "pass_review": False,
                "entry_price": None,
                "error": "LLM unavailable — missing API key or provider error",
                "hypothetical_pnl": None,
                "closing_price": None,
            })
            continue

        review = _llm_review_opportunities(top3, regime, confidence, llm)

        if not review:
            # LLM failed — record the deterministic top pick as fallback
            top = top3[0]
            results.append({
                "name": competitor.name,
                "provider": competitor.provider,
                "model": competitor.model,
                "pick": top["symbol"],
                "adjusted_score": top["score"],
                "rationale": "LLM review failed — using deterministic top pick",
                "concerns": [],
                "pass_review": True,
                "entry_price": top.get("current_price", 0),
                "error": "scoring_review_failed",
                "hypothetical_pnl": None,
                "closing_price": None,
            })
            continue

        top_sym = review.get("top_symbol") or top3[0]["symbol"]
        pick_candidate = next((s for s in top3 if s["symbol"] == top_sym), top3[0])
        adjusted_score = round(pick_candidate["score"] + review.get("score_adjustment", 0.0), 2)

        results.append({
            "name": competitor.name,
            "provider": competitor.provider,
            "model": competitor.model,
            "pick": top_sym,
            "adjusted_score": adjusted_score,
            "rationale": review.get("rationale", ""),
            "concerns": review.get("concerns", []),
            "pass_review": review.get("pass_review", True),
            "entry_price": pick_candidate.get("current_price", 0),
            "error": None,
            "hypothetical_pnl": None,
            "closing_price": None,
        })
        logger.info("[%s] %s → pick=%s score=%.1f pass=%s", AGENT_NAME, competitor.name, top_sym, adjusted_score, review.get("pass_review", True))

    # In actual (non-dry-run) mode: if a primary is designated, promote its pick
    # into scored_opportunities so governance/risk/execution see the primary's choice.
    primary_name = cfg.compete.primary
    new_scored = scored
    if not cfg.compete.dry_run and primary_name:
        primary_result = next((r for r in results if r["name"] == primary_name and r["pick"]), None)
        if primary_result and primary_result["pass_review"]:
            primary_sym = primary_result["pick"]
            new_scored = []
            for s in scored:
                if s["symbol"] == primary_sym:
                    s = {**s, "score": primary_result["adjusted_score"], "composite_score": primary_result["adjusted_score"]}
                new_scored.append(s)
            new_scored.sort(key=lambda x: x["score"], reverse=True)
            logger.info("[%s] Primary competitor %r promotes %s to top", AGENT_NAME, primary_name, primary_sym)
        else:
            logger.warning("[%s] Primary competitor %r vetoed or unavailable — using deterministic scoring", AGENT_NAME, primary_name)

    msg = create_message(
        source=AGENT_NAME,
        target="CompeteEvaluator",
        payload={
            "competitors": len(results),
            "picks": {r["name"]: r["pick"] for r in results if r["pick"]},
        },
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="compete_round_complete",
        data={
            "competitors": len(results),
            "regime": regime,
            "results_summary": [
                {"name": r["name"], "pick": r["pick"], "score": r["adjusted_score"]}
                for r in results
            ],
        },
    )

    return {
        "competitor_results": results,
        "scored_opportunities": new_scored,
        "messages": [msg],
        "audit_trail": [entry],
    }
