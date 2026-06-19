"""Universe Builder Agent — dynamic stock universe for the trading pipeline."""
from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.nse_tools import get_block_deals, get_bulk_deals, get_corporate_actions
from autotrader.tools.universe_tools import (
    fetch_index_constituents,
    get_event_driven_symbols,
    get_preopen_movers,
    momentum_screen,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "UniverseBuilderAgent"


def universe_builder_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Building dynamic stock universe", AGENT_NAME)

    cfg = load_config()
    ucfg = cfg.universe

    # Layer 1: Base — index constituents
    base = fetch_index_constituents(ucfg.index, ucfg.max_from_index)
    logger.info("[%s] Base universe: %d symbols from %s", AGENT_NAME, len(base), ucfg.index)

    # Layer 2: Momentum screen — score and filter base universe
    screened = momentum_screen(base, top_n=min(60, ucfg.max_total))

    # Build working set (symbol → entry)
    universe: dict[str, dict] = {s["symbol"]: s for s in screened}

    # Layer 3: Corporate event-driven additions
    if ucfg.include_corporate_events:
        # Collect events for screened symbols (sample to avoid too many API calls)
        all_events: list[dict] = []
        for sym_entry in list(universe.values())[:30]:
            events = get_corporate_actions(sym_entry["symbol"])
            all_events.extend(events)
        event_symbols = get_event_driven_symbols(all_events)
        for e in event_symbols:
            sym = e["symbol"]
            if sym not in universe:
                universe[sym] = e
            else:
                universe[sym]["source"] = "momentum+event"
        logger.info("[%s] After events: %d symbols", AGENT_NAME, len(universe))

    # Layer 4: Block/bulk deal additions
    if ucfg.include_block_deals:
        bulk = get_bulk_deals()
        block = get_block_deals()
        for deal in bulk + block:
            sym = (deal.get("symbol", "") or "").upper()
            if not sym:
                continue
            deal_type = (deal.get("dealType", "") or "").upper()
            qty = deal.get("quantity", 0)
            if deal_type == "BUY" and qty > 50_000:
                if sym not in universe:
                    universe[sym] = {"symbol": sym, "sector": "Unknown", "source": "block_deal", "momentum_score": 55}
                else:
                    universe[sym]["source"] = universe[sym].get("source", "momentum") + "+deal"
        logger.info("[%s] After block deals: %d symbols", AGENT_NAME, len(universe))

    # Layer 5: Pre-open movers (only if enabled and within time window)
    if ucfg.include_preopen:
        preopen = get_preopen_movers(top_n=20)
        for entry in preopen:
            sym = entry["symbol"]
            if sym not in universe:
                universe[sym] = entry
        logger.info("[%s] After pre-open: %d symbols", AGENT_NAME, len(universe))

    # Cap and finalise
    final_list = sorted(universe.values(), key=lambda x: x.get("momentum_score", 0), reverse=True)
    final_list = final_list[:ucfg.max_total]

    # Build sector mapping for downstream agents
    sector_map: dict[str, list[str]] = {}
    for entry in final_list:
        sec = entry.get("sector", "Unknown")
        sector_map.setdefault(sec, []).append(entry["symbol"])

    msg = create_message(
        source=AGENT_NAME,
        target="Layer1Agents",
        payload={"universe_size": len(final_list), "sectors": list(sector_map.keys())},
    )
    entry_audit = audit_entry(
        agent=AGENT_NAME,
        action="universe_built",
        data={"count": len(final_list), "index": ucfg.index, "sectors": len(sector_map)},
    )

    logger.info("[%s] Final universe: %d symbols across %d sectors", AGENT_NAME, len(final_list), len(sector_map))

    return {
        "universe": final_list,
        "messages": [msg],
        "audit_trail": [entry_audit],
    }
