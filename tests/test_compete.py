"""Tests for compete mode: coordinator, evaluator, config loading, and graph build."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autotrader.core.config import CompeteModeConfig, CompetitorConfig, PlatformConfig, load_config
from autotrader.core.state import TradingState, create_initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scored(symbol: str, score: float) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "composite_score": score,
        "current_price": 1000.0,
        "component_scores": {
            "market_regime": 80, "sector_strength": 80, "relative_strength": 80,
            "volume": 60, "catalyst": 65, "technical": 70, "options_sentiment": 50,
        },
        "pattern": "BULL_FLAG",
        "rsi": 55,
        "atr": 10,
        "ema9": 990, "ema21": 980, "vwap": 995,
        "catalyst_reason": "",
    }


def _base_state(**overrides) -> TradingState:
    state = create_initial_state()
    state.update({
        "market_regime": "bullish",
        "market_confidence": 0.8,
        "scored_opportunities": [
            _make_scored("RELIANCE", 85.0),
            _make_scored("TCS", 82.0),
            _make_scored("INFY", 79.0),
        ],
    })
    state.update(overrides)
    return state


def _make_compete_cfg(dry_run: bool = True, primary: str = "", names: list[str] | None = None) -> CompeteModeConfig:
    names = names or ["ModelA", "ModelB"]
    return CompeteModeConfig(
        enabled=True,
        dry_run=dry_run,
        primary=primary,
        competitors=[
            CompetitorConfig(name=n, provider="anthropic", model="claude-opus-4-8")
            for n in names
        ],
    )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_compete_config_defaults():
    cfg = load_config()
    assert isinstance(cfg.compete, CompeteModeConfig)
    assert cfg.compete.enabled is False


def test_competitor_config_fields():
    c = CompetitorConfig(name="X", provider="openai", model="gpt-5.4", reasoning_effort="high")
    assert c.name == "X"
    assert c.reasoning_effort == "high"
    assert c.thinking_budget == 0


def test_compete_mode_config_competitors():
    cfg = _make_compete_cfg(names=["A", "B", "C"])
    assert len(cfg.competitors) == 3
    assert cfg.competitors[0].name == "A"


# ---------------------------------------------------------------------------
# make_competitor_llm tests
# ---------------------------------------------------------------------------

def test_make_competitor_llm_no_api_key():
    from autotrader.core.llm import make_competitor_llm
    c = CompetitorConfig(name="X", provider="anthropic", model="claude-opus-4-8")
    with patch("os.getenv", return_value=None):
        result = make_competitor_llm(c)
    assert result is None


def test_make_competitor_llm_unknown_provider():
    from autotrader.core.llm import make_competitor_llm
    c = CompetitorConfig(name="X", provider="nonexistent_provider", model="m")
    result = make_competitor_llm(c)
    assert result is None


# ---------------------------------------------------------------------------
# Compete coordinator tests
# ---------------------------------------------------------------------------

def test_coordinator_compete_disabled():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    with patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg:
        mock_cfg.return_value.compete.enabled = False
        result = compete_coordinator_agent(state)
    assert "competitor_results" not in result
    assert result.get("audit_trail")


def test_coordinator_no_scored_opportunities():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    state["scored_opportunities"] = []
    compete_cfg = _make_compete_cfg()
    with patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg:
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)
    assert result["competitor_results"] == []


def test_coordinator_llm_unavailable():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(names=["ModelA"])
    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_competitor_llm", return_value=None),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)
    assert len(result["competitor_results"]) == 1
    assert result["competitor_results"][0]["pick"] is None
    assert "unavailable" in result["competitor_results"][0]["error"]


def test_coordinator_successful_review():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(names=["ModelA", "ModelB"])

    mock_llm = MagicMock()
    fake_review = {
        "top_symbol": "RELIANCE",
        "score_adjustment": 2.0,
        "rationale": "Strong breakout",
        "concerns": [],
        "pass_review": True,
    }

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_competitor_llm", return_value=mock_llm),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    assert len(result["competitor_results"]) == 2
    for r in result["competitor_results"]:
        assert r["pick"] == "RELIANCE"
        assert r["adjusted_score"] == 87.0   # 85.0 + 2.0
        assert r["pass_review"] is True


def test_coordinator_dry_run_does_not_update_scored():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(dry_run=True, primary="ModelA", names=["ModelA"])

    mock_llm = MagicMock()
    fake_review = {"top_symbol": "TCS", "score_adjustment": 3.0, "rationale": "", "concerns": [], "pass_review": True}

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_competitor_llm", return_value=mock_llm),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    # In dry_run mode scored_opportunities should remain unchanged (RELIANCE on top)
    assert result["scored_opportunities"][0]["symbol"] == "RELIANCE"


def test_coordinator_actual_mode_promotes_primary():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(dry_run=False, primary="ModelA", names=["ModelA"])

    mock_llm = MagicMock()
    fake_review = {"top_symbol": "TCS", "score_adjustment": 5.0, "rationale": "", "concerns": [], "pass_review": True}

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_competitor_llm", return_value=mock_llm),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    # TCS (82 + 5 = 87) should be promoted above RELIANCE (85)
    assert result["scored_opportunities"][0]["symbol"] == "TCS"
    assert result["scored_opportunities"][0]["score"] == 87.0


# ---------------------------------------------------------------------------
# Compete evaluator tests
# ---------------------------------------------------------------------------

def test_evaluator_compete_disabled():
    from autotrader.agents.compete.evaluator import compete_evaluator_agent
    state = _base_state()
    with patch("autotrader.agents.compete.evaluator.load_config") as mock_cfg:
        mock_cfg.return_value.compete.enabled = False
        result = compete_evaluator_agent(state)
    assert result.get("audit_trail")
    assert "competitor_results" not in result


def test_evaluator_ranks_by_pnl():
    from autotrader.agents.compete.evaluator import compete_evaluator_agent

    state = _base_state()
    state["competitor_results"] = [
        {"name": "ModelA", "model": "m1", "pick": "RELIANCE", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
        {"name": "ModelB", "model": "m2", "pick": "TCS", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
    ]

    closing_prices = {"RELIANCE": 1020.0, "TCS": 1005.0}

    def _fake_close(symbol: str):
        return closing_prices.get(symbol)

    with (
        patch("autotrader.agents.compete.evaluator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.evaluator._fetch_closing_price", side_effect=_fake_close),
    ):
        mock_cfg.return_value.compete.enabled = True
        result = compete_evaluator_agent(state)

    ranked = result["competitor_results"]
    assert ranked[0]["name"] == "ModelA"          # RELIANCE: +2%
    assert ranked[1]["name"] == "ModelB"          # TCS: +0.5%
    assert ranked[0]["hypothetical_pnl_pct"] == pytest.approx(2.0, rel=0.01)


def test_evaluator_handles_missing_price():
    from autotrader.agents.compete.evaluator import compete_evaluator_agent

    state = _base_state()
    state["competitor_results"] = [
        {"name": "ModelA", "model": "m1", "pick": "UNKNOWN", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
    ]

    with (
        patch("autotrader.agents.compete.evaluator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.evaluator._fetch_closing_price", return_value=None),
    ):
        mock_cfg.return_value.compete.enabled = True
        result = compete_evaluator_agent(state)

    assert result["competitor_results"][0]["hypothetical_pnl_pct"] is None


# ---------------------------------------------------------------------------
# Graph build test
# ---------------------------------------------------------------------------

def test_compete_graph_builds():
    from autotrader.graphs.compete import build_compete_graph
    graph = build_compete_graph()
    assert graph is not None
