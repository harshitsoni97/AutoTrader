"""Tests for UniverseBuilderAgent and universe_tools."""
from datetime import date, timedelta
from unittest.mock import patch


def test_map_industry_banking():
    from autotrader.tools.universe_tools import _map_industry
    assert _map_industry("Banks") == "Banking"
    assert _map_industry("Non Banking Financial Company") == "Banking"


def test_map_industry_it():
    from autotrader.tools.universe_tools import _map_industry
    assert _map_industry("IT Software") == "IT"


def test_map_industry_pharma():
    from autotrader.tools.universe_tools import _map_industry
    assert _map_industry("Pharmaceuticals") == "Pharma"


def test_map_industry_unknown():
    from autotrader.tools.universe_tools import _map_industry
    assert _map_industry("Miscellaneous") == "Midcap"


def test_fetch_index_constituents_fallback():
    from autotrader.tools.universe_tools import fetch_index_constituents
    with patch("requests.Session") as mock_sess:
        mock_sess.return_value.get.side_effect = Exception("network error")
        result = fetch_index_constituents("nifty50", max_count=10)
    assert len(result) == 10
    assert all("symbol" in r and "sector" in r for r in result)


def test_get_event_driven_symbols_future():
    from autotrader.tools.universe_tools import get_event_driven_symbols
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    events = [
        {"symbol": "RELIANCE", "subject": "Board Meeting Results", "exDate": tomorrow},
        {"symbol": "TCS", "subject": "Dividend", "exDate": tomorrow},
    ]
    result = get_event_driven_symbols(events)
    syms = [r["symbol"] for r in result]
    assert "RELIANCE" in syms
    assert "TCS" in syms
    assert all(r["source"] == "event" for r in result)


def test_get_event_driven_symbols_past():
    from autotrader.tools.universe_tools import get_event_driven_symbols
    past = (date.today() - timedelta(days=5)).isoformat()
    result = get_event_driven_symbols([{"symbol": "OLD", "subject": "Results", "exDate": past}])
    assert result == []


def test_get_event_driven_symbols_event_type():
    from autotrader.tools.universe_tools import get_event_driven_symbols
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    events = [{"symbol": "X", "subject": "Buyback", "exDate": tomorrow}]
    result = get_event_driven_symbols(events)
    assert result[0]["event_type"] == "buyback"


def test_preopen_movers_outside_window():
    from autotrader.tools.universe_tools import get_preopen_movers
    from datetime import datetime
    # 04:30 UTC = 10:00 IST — outside 9:00-9:08 window
    with patch("autotrader.tools.universe_tools.datetime") as mock_dt:
        mock_dt.utcnow.return_value = datetime(2025, 1, 1, 4, 30, 0)
        mock_dt.strptime = datetime.strptime
        mock_dt.today = datetime.today
        result = get_preopen_movers()
    assert result == []


def test_universe_builder_agent_returns_universe():
    from autotrader.core.state import create_initial_state
    state = create_initial_state()
    fake_base = [{"symbol": "TCS", "sector": "IT"}, {"symbol": "INFY", "sector": "IT"}]
    fake_screened = [{**s, "momentum_score": 60, "source": "momentum"} for s in fake_base]
    with patch("autotrader.agents.layer0.universe_builder.fetch_index_constituents", return_value=fake_base), \
         patch("autotrader.agents.layer0.universe_builder.momentum_screen", return_value=fake_screened), \
         patch("autotrader.agents.layer0.universe_builder.get_corporate_actions", return_value=[]), \
         patch("autotrader.agents.layer0.universe_builder.get_bulk_deals", return_value=[]), \
         patch("autotrader.agents.layer0.universe_builder.get_block_deals", return_value=[]):
        from autotrader.agents.layer0.universe_builder import universe_builder_agent
        result = universe_builder_agent(state)
    assert "universe" in result
    assert len(result["universe"]) == 2
    assert {r["symbol"] for r in result["universe"]} == {"TCS", "INFY"}


def test_universe_builder_adds_block_deal_symbols():
    from autotrader.core.state import create_initial_state
    state = create_initial_state()
    fake_base = [{"symbol": "TCS", "sector": "IT"}]
    fake_screened = [{**fake_base[0], "momentum_score": 60, "source": "momentum"}]
    bulk_deal = {"symbol": "NEWSTOCK", "dealType": "BUY", "quantity": 200_000, "clientName": "Fund"}
    with patch("autotrader.agents.layer0.universe_builder.fetch_index_constituents", return_value=fake_base), \
         patch("autotrader.agents.layer0.universe_builder.momentum_screen", return_value=fake_screened), \
         patch("autotrader.agents.layer0.universe_builder.get_corporate_actions", return_value=[]), \
         patch("autotrader.agents.layer0.universe_builder.get_bulk_deals", return_value=[bulk_deal]), \
         patch("autotrader.agents.layer0.universe_builder.get_block_deals", return_value=[]):
        from autotrader.agents.layer0.universe_builder import universe_builder_agent
        result = universe_builder_agent(state)
    syms = {r["symbol"] for r in result["universe"]}
    assert "NEWSTOCK" in syms


def test_catalyst_uses_dynamic_universe():
    """catalyst_intelligence_agent should use universe from state when present."""
    from autotrader.core.state import create_initial_state
    from unittest.mock import patch as p
    state = create_initial_state()
    state["universe"] = [
        {"symbol": "BEL", "sector": "Capital_Goods", "momentum_score": 75},
        {"symbol": "HAL", "sector": "Capital_Goods", "momentum_score": 70},
    ]
    state["top_sectors"] = ["Capital_Goods"]
    state["market_regime"] = "bullish"

    with p("autotrader.agents.layer1.catalyst_intelligence.get_bulk_deals", return_value=[]), \
         p("autotrader.agents.layer1.catalyst_intelligence.get_block_deals", return_value=[]), \
         p("autotrader.agents.layer1.catalyst_intelligence.get_corporate_actions", return_value=[]):
        from autotrader.agents.layer1.catalyst_intelligence import catalyst_intelligence_agent
        result = catalyst_intelligence_agent(state)
    assert "catalysts" in result


def test_universe_config_loaded():
    from autotrader.core.config import load_config
    cfg = load_config()
    assert cfg.universe.index == "nifty500"
    assert cfg.universe.max_from_index == 100
    assert cfg.universe.max_total == 80
