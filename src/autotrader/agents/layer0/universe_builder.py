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

    # Layer 1: Base index constituents
    base = fetch_index_constituents(ucfg.index, ucfg.max_from_index)
    logger.info("[%s] Base: %d symbols from %s", AGENT_NAME, len(base), ucfg.index)

    # Layer 2: Momentum screen
    screened = momentum_screen(base, top_n=min(60, ucfg.max_total))

    universe: dict[str, dict] = {s["symbol"]: s for s in screened}

    # Layer 3: Corporate events
    if ucfg.include_corporate_events:
        all_events: list[dict] = []
        for sym_entry in list(universe.values())[:30]:
            all_events.extend(get_corporate_actions(sym_entry["symbol"]))
        for e in get_event_driven_symbols(all_events):
            sym = e["symbol"]
            if sym not in universe:
                universe[sym] = e
            else:
                universe[sym]["source"] = "momentum+event"
        logger.info("[%s] After events: %d symbols", AGENT_NAME, len(universe))

    # Layer 4: Block/bulk deals
    if ucfg.include_block_deals:
        for deal in get_bulk_deals() + get_block_deals():
            sym = (deal.get("symbol", "") or "").upper()
            if not sym:
                continue
            if (deal.get("dealType", "") or "").upper() == "BUY" and deal.get("quantity", 0) > 50_000:
                if sym not in universe:
                    universe[sym] = {"symbol": sym, "sector": "Unknown", "source": "block_deal", "momentum_score": 55}
                else:
                    universe[sym]["source"] = universe[sym].get("source", "momentum") + "+deal"
        logger.info("[%s] After block deals: %d symbols", AGENT_NAME, len(universe))

    # Layer 5: Pre-open movers (time-gated)
    if ucfg.include_preopen:
        for entry in get_preopen_movers(top_n=20):
            sym = entry["symbol"]
            if sym not in universe:
                universe[sym] = entry

    # Finalise
    final_list = sorted(universe.values(), key=lambda x: x.get("momentum_score", 0), reverse=True)
    final_list = final_list[:ucfg.max_total]

    sector_map: dict[str, list[str]] = {}
    for entry in final_list:
        sector_map.setdefault(entry.get("sector", "Unknown"), []).append(entry["symbol"])

    msg = create_message(
        source=AGENT_NAME,
        target="Layer1Agents",
        payload={"universe_size": len(final_list), "sectors": list(sector_map.keys())},
    )
    audit = audit_entry(
        agent=AGENT_NAME,
        action="universe_built",
        data={"count": len(final_list), "index": ucfg.index, "sectors": len(sector_map)},
    )

    logger.info("[%s] Final universe: %d symbols across %d sectors", AGENT_NAME, len(final_list), len(sector_map))
    return {"universe": final_list, "messages": [msg], "audit_trail": [audit]}
