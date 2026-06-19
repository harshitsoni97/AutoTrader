"""Tests for UniverseBuilderAgent."""
from unittest.mock import MagicMock, patch
import pytest


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
    """When NSE fetch fails, should return fallback symbols."""
    from autotrader.tools.universe_tools import fetch_index_constituents
    with patch("requests.Session") as mock_sess:
        mock_sess.return_value.get.side_effect = Exception("network error")
        result = fetch_index_constituents("nifty50", max_count=10)
    assert len(result) == 10
    assert all("symbol" in r for r in result)
    assert all("sector" in r for r in result)


def test_get_event_driven_symbols():
    from autotrader.tools.universe_tools import get_event_driven_symbols
    from datetime import date, timedelta
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


def test_get_event_driven_symbols_old_date():
    """Events in the past should be excluded."""
    from autotrader.tools.universe_tools import get_event_driven_symbols
    from datetime import date, timedelta
    past = (date.today() - timedelta(days=5)).isoformat()
    events = [{"symbol": "OLD", "subject": "Results", "exDate": past}]
    result = get_event_driven_symbols(events)
    assert result == []


def test_preopen_movers_outside_window():
    """Should return empty list outside 9:00-9:08 IST."""
    from autotrader.tools.universe_tools import get_preopen_movers
    from unittest.mock import patch
    from datetime import datetime
    # Simulate 10:00 IST = 04:30 UTC
    with patch("autotrader.tools.universe_tools.datetime") as mock_dt:
        mock_dt.utcnow.return_value = datetime(2025, 1, 1, 4, 30, 0)
        result = get_preopen_movers()
    assert result == []


def test_universe_builder_agent_uses_universe():
    """Agent should return universe list."""
    from autotrader.core.state import create_initial_state
    from unittest.mock import patch

    state = create_initial_state()

    fake_symbols = [{"symbol": "TCS", "sector": "IT"}, {"symbol": "INFY", "sector": "IT"}]

    with patch("autotrader.agents.layer0.universe_builder.fetch_index_constituents", return_value=fake_symbols), \
         patch("autotrader.agents.layer0.universe_builder.momentum_screen", return_value=[{**s, "momentum_score": 60, "source": "momentum"} for s in fake_symbols]), \
         patch("autotrader.agents.layer0.universe_builder.get_corporate_actions", return_value=[]), \
         patch("autotrader.agents.layer0.universe_builder.get_bulk_deals", return_value=[]), \
         patch("autotrader.agents.layer0.universe_builder.get_block_deals", return_value=[]):
        from autotrader.agents.layer0.universe_builder import universe_builder_agent
        result = universe_builder_agent(state)

    assert "universe" in result
    assert len(result["universe"]) == 2
    assert result["universe"][0]["symbol"] in ("TCS", "INFY")
