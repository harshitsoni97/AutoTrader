"""Catalyst Intelligence Agent — discovers fresh market catalysts."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.llm import CatalystEnrichment, get_fast_llm, structured
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.prompts import get_prompt
from autotrader.core.state import TradingState
from autotrader.tools.nse_tools import get_block_deals, get_bulk_deals, get_corporate_actions

logger = logging.getLogger(__name__)

AGENT_NAME = "CatalystIntelligenceAgent"

# Catalyst type scores
CATALYST_SCORES = {
    "earnings_beat": 90,
    "defence_order": 88,
    "contract_win": 85,
    "analyst_upgrade": 70,
    "policy_announcement": 75,
    "bulk_buy": 65,
    "block_buy": 60,
    "management_buyback": 72,
    "fii_buy": 68,
}

# Sector-to-symbol mapping for catalyst discovery
SECTOR_WATCHLIST = {
    "Banking": ["HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN", "KOTAKBANK"],
    "Capital_Goods": ["L&T", "BEL", "HAL", "BHEL", "ABB"],
    "IT": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM"],
    "Pharma": ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "AUROPHARMA"],
    "Auto": ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "HEROMOTOCO"],
    "Realty": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD"],
    "Metal": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL"],
    "Energy": ["RELIANCE", "ONGC", "BPCL", "IOC", "POWERGRID"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR"],
    "Midcap": ["POLYCAB", "DELHIVERY", "ZOMATO", "NAUKRI", "IRCTC"],
}


def _score_bulk_deals(deals: list[dict]) -> list[dict]:
    catalysts = []
    for deal in deals:
        symbol = deal.get("symbol", "").upper()
        deal_type = deal.get("dealType", "").upper()
        qty = deal.get("quantity", 0)
        if deal_type == "BUY" and qty > 100_000:
            catalysts.append({
                "symbol": symbol,
                "catalyst_score": CATALYST_SCORES["bulk_buy"],
                "reason": f"Bulk buy {qty:,} shares by {deal.get('clientName', 'Institution')}",
                "catalyst_type": "bulk_buy",
            })
    return catalysts


def _score_corporate_actions(symbol: str, actions: list[dict]) -> list[dict]:
    catalysts = []
    for action in actions:
        subject = action.get("subject", "").lower()
        ex_date = action.get("exDate", "")
        if "buyback" in subject:
            catalysts.append({
                "symbol": symbol,
                "catalyst_score": CATALYST_SCORES["management_buyback"],
                "reason": f"Buyback announced, ex-date {ex_date}",
                "catalyst_type": "management_buyback",
            })
    return catalysts


def _llm_enrich_catalysts(
    catalysts: list[dict],
    market_regime: str,
    llm_cfg: Any,
) -> list[dict]:
    """Use fast LLM to refine scores on the top 5 catalysts (with Pydantic enforcement)."""
    llm = get_fast_llm(llm_cfg)
    if llm is None:
        return catalysts

    enriched = list(catalysts)
    chain = structured(llm, CatalystEnrichment)

    for i, cat in enumerate(enriched[:5]):
        prompt = get_prompt(
            "catalyst_enrichment",
            market_regime=market_regime,
            symbol=cat["symbol"],
            catalyst_type=cat.get("catalyst_type", "unknown"),
            base_score=cat["catalyst_score"],
            description=cat.get("reason", ""),
        )
        try:
            result: CatalystEnrichment = chain.invoke(prompt)
            enriched[i] = {
                **cat,
                "catalyst_score": round(result.adjusted_score, 1),
                "reason": result.narrative,
                "llm_impact": result.impact,
                "llm_confidence": result.confidence,
            }
        except Exception as exc:
            logger.warning("[%s] LLM enrichment failed for %s: %s", AGENT_NAME, cat["symbol"], exc)

    return enriched


def catalyst_intelligence_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Discovering market catalysts", AGENT_NAME)

    all_catalysts: list[dict] = []

    # 1. Bulk and block deals
    bulk = get_bulk_deals()
    block = get_block_deals()
    all_catalysts.extend(_score_bulk_deals(bulk))
    all_catalysts.extend(_score_bulk_deals(block))

    # 2. Corporate actions for watchlist symbols
    top_sectors = state.get("top_sectors", [])
    symbols_to_check: list[str] = []
    for sector in top_sectors:
        symbols_to_check.extend(SECTOR_WATCHLIST.get(sector, []))
    # Also scan all sectors for high-scoring actions
    for sector, syms in SECTOR_WATCHLIST.items():
        symbols_to_check.extend(syms[:2])
    symbols_to_check = list(set(symbols_to_check))[:20]

    for symbol in symbols_to_check:
        actions = get_corporate_actions(symbol)
        all_catalysts.extend(_score_corporate_actions(symbol, actions))

    # 3. Prioritise sector-aligned catalysts
    sector_symbols: set[str] = set()
    for sector in top_sectors:
        sector_symbols.update(SECTOR_WATCHLIST.get(sector, []))

    for cat in all_catalysts:
        if cat["symbol"] in sector_symbols:
            cat["catalyst_score"] = min(100, cat["catalyst_score"] + 5)

    # Deduplicate by symbol (keep highest score per symbol)
    by_symbol: dict[str, dict] = {}
    for cat in all_catalysts:
        sym = cat["symbol"]
        if sym not in by_symbol or cat["catalyst_score"] > by_symbol[sym]["catalyst_score"]:
            by_symbol[sym] = cat

    final_catalysts = sorted(by_symbol.values(), key=lambda x: x["catalyst_score"], reverse=True)

    # Optional LLM enrichment — refines scores with market context
    cfg = load_config()
    if cfg.llm.enable_catalyst_llm:
        market_regime = state.get("market_regime", "unknown")
        final_catalysts = _llm_enrich_catalysts(final_catalysts, market_regime, cfg.llm)
        final_catalysts.sort(key=lambda x: x["catalyst_score"], reverse=True)

    msg = create_message(
        source=AGENT_NAME,
        target="DiscoveryAgents",
        payload={"catalysts": final_catalysts, "count": len(final_catalysts)},
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="catalysts_discovered",
        data={"count": len(final_catalysts), "top": final_catalysts[:3]},
    )

    logger.info("[%s] Found %d catalysts", AGENT_NAME, len(final_catalysts))

    return {
        "catalysts": final_catalysts,
        "messages": [msg],
        "audit_trail": [entry],
    }
