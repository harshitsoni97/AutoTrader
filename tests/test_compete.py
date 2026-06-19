"""Tests for compete mode: coordinator, evaluator, config loading, and graph build."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autotrader.core.config import CompeteModeConfig, StackConfig, load_config
from autotrader.core.state import TradingState, create_initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scored(symbol: str, score: float, price: float = 1000.0) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "composite_score": score,
        "current_price": price,
        "rs_score": 80.0,
        "volume_score": 60.0,
        "technical_score": 70.0,
        "catalyst_score": 65.0,
        "sector": "Energy",
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


def _make_candidate(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "rs_score": 80.0,
        "volume_score": 60.0,
        "technical_score": 70.0,
        "catalyst_score": 65.0,
        "current_price": 1000.0,
        "sector": "Energy",
        "pattern": "BULL_FLAG",
        "rsi": 55,
        "atr": 10,
        "ema9": 990, "ema21": 980, "vwap": 995,
    }


def _base_state(**overrides) -> TradingState:
    state = create_initial_state()
    state.update({
        "market_regime": "bullish",
        "market_confidence": 0.8,
        "nifty_change_pct": 1.2,
        "india_vix": 14.0,
        "fii_net_cash": 1500.0,
        "global_change_pct": 0.5,
        "options_signal": "neutral",
        "top_sectors": ["Energy", "IT"],
        "sector_rankings": [],
        "candidates": [_make_candidate("RELIANCE"), _make_candidate("TCS"), _make_candidate("INFY")],
        "raw_catalysts": [
            {"symbol": "RELIANCE", "catalyst_score": 70, "reason": "Block deal", "catalyst_type": "block_buy"},
            {"symbol": "TCS", "catalyst_score": 65, "reason": "Analyst upgrade", "catalyst_type": "analyst_upgrade"},
        ],
        "scored_opportunities": [
            _make_scored("RELIANCE", 85.0),
            _make_scored("TCS", 82.0),
            _make_scored("INFY", 79.0),
        ],
    })
    state.update(overrides)
    return state


def _make_stack(name: str = "TestStack") -> StackConfig:
    return StackConfig(
        name=name,
        fast_provider="anthropic",
        fast_model="claude-haiku-4-5-20251001",
        analysis_provider="anthropic",
        analysis_model="claude-sonnet-4-6",
    )


def _make_compete_cfg(dry_run: bool = True, primary: str = "", names: list[str] | None = None) -> CompeteModeConfig:
    names = names or ["StackA", "StackB"]
    return CompeteModeConfig(
        enabled=True,
        dry_run=dry_run,
        primary=primary,
        stacks=[_make_stack(n) for n in names],
    )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_compete_config_defaults():
    cfg = load_config()
    assert isinstance(cfg.compete, CompeteModeConfig)
    assert cfg.compete.enabled is False


def test_stack_config_fields():
    s = _make_stack("Anthropic")
    assert s.name == "Anthropic"
    assert s.fast_provider == "anthropic"
    assert s.analysis_provider == "anthropic"
    assert s.report_thinking_budget == 0
    assert s.report_reasoning_effort == ""


def test_compete_mode_stacks_loaded():
    cfg = load_config()
    # 3 stacks defined in llm_config.yaml (Anthropic, OpenAI, Google)
    assert len(cfg.compete.stacks) == 3
    names = [s.name for s in cfg.compete.stacks]
    assert "Anthropic" in names
    assert "OpenAI" in names
    assert "Google" in names


def test_stack_config_openai_fields():
    cfg = load_config()
    openai_stack = next(s for s in cfg.compete.stacks if s.name == "OpenAI")
    assert openai_stack.fast_provider == "openai"
    assert openai_stack.analysis_provider == "openai"
    assert openai_stack.report_reasoning_effort == "high"


def test_stack_config_anthropic_fields():
    cfg = load_config()
    ant_stack = next(s for s in cfg.compete.stacks if s.name == "Anthropic")
    assert ant_stack.report_thinking_budget == 2000
    assert ant_stack.report_model == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# make_stack_llms tests
# ---------------------------------------------------------------------------

def test_make_stack_llms_no_api_key():
    from autotrader.core.llm import make_stack_llms
    stack = _make_stack()
    with patch("os.getenv", return_value=None):
        fast_llm, analysis_llm = make_stack_llms(stack)
    assert fast_llm is None
    assert analysis_llm is None


def test_make_stack_llms_unknown_provider():
    from autotrader.core.llm import make_stack_llms
    stack = StackConfig(
        name="X",
        fast_provider="nonexistent",
        fast_model="m",
        analysis_provider="nonexistent",
        analysis_model="m",
    )
    fast_llm, analysis_llm = make_stack_llms(stack)
    assert fast_llm is None
    assert analysis_llm is None


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


def test_coordinator_no_candidates():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    state["candidates"] = []
    compete_cfg = _make_compete_cfg()
    with patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg:
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)
    assert result["competitor_results"] == []


def test_coordinator_both_llms_unavailable():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(names=["StackA"])
    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_stack_llms", return_value=(None, None)),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)
    assert len(result["competitor_results"]) == 1
    assert result["competitor_results"][0]["pick"] is None
    assert "unavailable" in result["competitor_results"][0]["error"]


def test_coordinator_full_pipeline():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(names=["StackA", "StackB"])

    mock_llm = MagicMock()
    fake_review = {
        "top_symbol": "RELIANCE",
        "score_adjustment": 2.0,
        "rationale": "Strong momentum",
        "concerns": [],
        "pass_review": True,
    }

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_stack_llms", return_value=(mock_llm, mock_llm)),
        patch("autotrader.agents.compete.coordinator._llm_enrich_catalysts", return_value=state["raw_catalysts"]),
        patch("autotrader.agents.compete.coordinator._llm_enrich_regime", return_value=("bullish", 0.8, {})),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    assert len(result["competitor_results"]) == 2
    for r in result["competitor_results"]:
        assert r["pick"] == "RELIANCE"
        assert r["pass_review"] is True
        assert r["regime"] == "bullish"


def test_coordinator_each_stack_uses_own_regime():
    """Each stack can independently classify the regime differently."""
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(names=["StackA", "StackB"])

    mock_llm = MagicMock()
    # Stack A: bullish, Stack B: bearish
    call_count = {"n": 0}
    def fake_regime(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] % 2 == 1:
            return ("bullish", 0.8, {})
        return ("bearish", 0.6, {})

    fake_review = {"top_symbol": "RELIANCE", "score_adjustment": 0.0, "rationale": "", "concerns": [], "pass_review": True}

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_stack_llms", return_value=(mock_llm, mock_llm)),
        patch("autotrader.agents.compete.coordinator._llm_enrich_catalysts", return_value=state["raw_catalysts"]),
        patch("autotrader.agents.compete.coordinator._llm_enrich_regime", side_effect=fake_regime),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    regimes = [r["regime"] for r in result["competitor_results"]]
    assert "bullish" in regimes
    assert "bearish" in regimes


def test_coordinator_dry_run_does_not_modify_scored():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(dry_run=True, primary="StackA", names=["StackA"])

    mock_llm = MagicMock()
    # Stack picks TCS (not the top RELIANCE)
    fake_review = {"top_symbol": "TCS", "score_adjustment": 10.0, "rationale": "", "concerns": [], "pass_review": True}

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_stack_llms", return_value=(mock_llm, mock_llm)),
        patch("autotrader.agents.compete.coordinator._llm_enrich_catalysts", return_value=state["raw_catalysts"]),
        patch("autotrader.agents.compete.coordinator._llm_enrich_regime", return_value=("bullish", 0.8, {})),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    # In dry_run mode RELIANCE should still be on top (unchanged)
    assert result["scored_opportunities"][0]["symbol"] == "RELIANCE"


def test_coordinator_actual_mode_promotes_primary():
    from autotrader.agents.compete.coordinator import compete_coordinator_agent
    state = _base_state()
    compete_cfg = _make_compete_cfg(dry_run=False, primary="StackA", names=["StackA"])

    mock_llm = MagicMock()
    fake_review = {"top_symbol": "TCS", "score_adjustment": 10.0, "rationale": "", "concerns": [], "pass_review": True}

    with (
        patch("autotrader.agents.compete.coordinator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.coordinator.make_stack_llms", return_value=(mock_llm, mock_llm)),
        patch("autotrader.agents.compete.coordinator._llm_enrich_catalysts", return_value=state["raw_catalysts"]),
        patch("autotrader.agents.compete.coordinator._llm_enrich_regime", return_value=("bullish", 0.8, {})),
        patch("autotrader.agents.compete.coordinator._llm_review_opportunities", return_value=fake_review),
    ):
        mock_cfg.return_value.compete = compete_cfg
        result = compete_coordinator_agent(state)

    # TCS gets +10 → should be at top now
    assert result["scored_opportunities"][0]["symbol"] == "TCS"


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
        {"name": "Anthropic", "model": "claude-sonnet-4-6", "pick": "RELIANCE", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
        {"name": "OpenAI", "model": "gpt-5.4", "pick": "TCS", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
        {"name": "Google", "model": "gemini-2.5-flash", "pick": "INFY", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
    ]

    closing_prices = {"RELIANCE": 1030.0, "TCS": 1005.0, "INFY": 980.0}

    with (
        patch("autotrader.agents.compete.evaluator.load_config") as mock_cfg,
        patch("autotrader.agents.compete.evaluator._fetch_closing_price", side_effect=lambda s: closing_prices.get(s)),
    ):
        mock_cfg.return_value.compete.enabled = True
        result = compete_evaluator_agent(state)

    ranked = result["competitor_results"]
    assert ranked[0]["name"] == "Anthropic"    # RELIANCE: +3%
    assert ranked[1]["name"] == "OpenAI"       # TCS: +0.5%
    assert ranked[2]["name"] == "Google"       # INFY: -2%
    assert ranked[2]["hypothetical_pnl_pct"] == pytest.approx(-2.0, rel=0.01)


def test_evaluator_handles_missing_price():
    from autotrader.agents.compete.evaluator import compete_evaluator_agent

    state = _base_state()
    state["competitor_results"] = [
        {"name": "Anthropic", "model": "m", "pick": "UNKNOWN", "entry_price": 1000.0, "pass_review": True, "rationale": ""},
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
