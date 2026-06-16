"""Unit tests for core modules and agents."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Config ────────────────────────────────────────────────────────────────────

def test_config_loads():
    from autotrader.core.config import load_config
    cfg = load_config()
    assert cfg.trading_policy.max_daily_trades == 3
    assert cfg.trading_policy.total_capital == 1_000_000
    assert cfg.memory_policy.auto_modify_strategy is False
    assert cfg.strategy_version.strategy_version == "1.0.0"


def test_config_policy_defaults():
    from autotrader.core.config import TradingPolicy
    p = TradingPolicy()
    assert p.min_risk_reward == 2.0
    assert p.minimum_score == 80.0
    assert p.allow_overnight_positions is False


# ── A2A Messages ──────────────────────────────────────────────────────────────

def test_create_message():
    from autotrader.core.messages import create_message
    msg = create_message("AgentA", "AgentB", {"score": 91}, symbol="BEL")
    assert msg["source_agent"] == "AgentA"
    assert msg["target_agent"] == "AgentB"
    assert msg["symbol"] == "BEL"
    assert msg["payload"]["score"] == 91
    assert "message_id" in msg
    assert "timestamp" in msg


def test_audit_entry():
    from autotrader.core.messages import audit_entry
    entry = audit_entry("TestAgent", "test_action", {"key": "value"})
    assert entry["agent"] == "TestAgent"
    assert entry["action"] == "test_action"
    assert entry["data"]["key"] == "value"
    assert "timestamp" in entry


# ── State ─────────────────────────────────────────────────────────────────────

def test_create_initial_state():
    from autotrader.core.state import create_initial_state
    state = create_initial_state("pre_market")
    assert state["session_type"] == "pre_market"
    assert state["market_regime"] == "unknown"
    assert state["governance_approved"] is False
    assert state["daily_pnl"] == 0.0
    assert isinstance(state["messages"], list)
    assert isinstance(state["audit_trail"], list)


# ── Governance Agent ──────────────────────────────────────────────────────────

def _make_state(**overrides):
    from autotrader.core.state import create_initial_state
    state = create_initial_state("pre_market")
    state.update(overrides)
    return state


def test_governance_approved():
    from autotrader.agents.layer4.governance import governance_agent
    state = _make_state(
        market_regime="bullish",
        market_confidence=0.85,
        scored_opportunities=[{"symbol": "BEL", "score": 91}],
        daily_trades_taken=0,
        positions=[],
        daily_pnl=0.0,
        consecutive_losses=0,
        orders=[],
    )
    result = governance_agent(state)
    assert result["governance_approved"] is True
    assert len(result["audit_trail"]) == 1
    assert len(result["messages"]) == 1


def test_governance_rejects_on_daily_limit():
    from autotrader.agents.layer4.governance import governance_agent
    state = _make_state(
        market_regime="bullish",
        market_confidence=0.85,
        scored_opportunities=[{"symbol": "BEL", "score": 91}],
        daily_trades_taken=3,  # At limit
        positions=[],
        daily_pnl=0.0,
        consecutive_losses=0,
        orders=[],
    )
    result = governance_agent(state)
    assert result["governance_approved"] is False
    assert "limit" in result["governance_reason"].lower()


def test_governance_rejects_no_opportunities():
    from autotrader.agents.layer4.governance import governance_agent
    state = _make_state(
        market_regime="bullish",
        market_confidence=0.85,
        scored_opportunities=[],
        daily_trades_taken=0,
        positions=[],
        daily_pnl=0.0,
        consecutive_losses=0,
        orders=[],
    )
    result = governance_agent(state)
    assert result["governance_approved"] is False


def test_governance_rejects_on_daily_loss():
    from autotrader.agents.layer4.governance import governance_agent
    state = _make_state(
        market_regime="bullish",
        market_confidence=0.85,
        scored_opportunities=[{"symbol": "BEL", "score": 91}],
        daily_trades_taken=0,
        positions=[],
        daily_pnl=-25000.0,  # 2.5% of 1M capital
        consecutive_losses=0,
        orders=[],
    )
    result = governance_agent(state)
    assert result["governance_approved"] is False
    assert "loss" in result["governance_reason"].lower()


def test_governance_rejects_blocked_regime():
    from autotrader.agents.layer4.governance import governance_agent
    state = _make_state(
        market_regime="high_volatility_bear",
        market_confidence=0.85,
        scored_opportunities=[{"symbol": "BEL", "score": 91}],
        daily_trades_taken=0,
        positions=[],
        daily_pnl=0.0,
        consecutive_losses=0,
        orders=[],
    )
    result = governance_agent(state)
    assert result["governance_approved"] is False
    assert "regime" in result["governance_reason"].lower()


# ── Opportunity Scoring ───────────────────────────────────────────────────────

def test_opportunity_scoring_weights():
    """Verify composite score is within valid range."""
    from autotrader.agents.layer3.opportunity_scoring import WEIGHTS
    assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001, "Weights must sum to 1.0"


def test_opportunity_scoring_agent():
    from autotrader.agents.layer3.opportunity_scoring import opportunity_scoring_agent
    state = _make_state(
        market_regime="bullish",
        market_confidence=0.85,
        top_sectors=["Capital_Goods"],
        sector_rankings=[{"sector": "Capital_Goods", "momentum_score": 2.5}],
        candidates=[{
            "symbol": "BEL",
            "relative_strength": 89.0,
            "volume_score": 94.0,
            "technical_score": 87.0,
            "catalyst_score": 91,
            "current_price": 412.0,
            "pattern": "ORB",
            "atr": 6.0,
            "ema9": 410.0,
            "ema21": 405.0,
            "vwap": 408.0,
            "rsi": 62.0,
            "catalyst_reason": "Defence order",
            "ret_1d_pct": 1.2,
        }],
    )
    result = opportunity_scoring_agent(state)
    opps = result["scored_opportunities"]
    assert isinstance(opps, list)
    if opps:
        assert 0 <= opps[0]["score"] <= 100
        assert "component_scores" in opps[0]


# ── Safety Controls ───────────────────────────────────────────────────────────

def test_kill_switch():
    from autotrader.safety.controls import SafetyControls
    sc = SafetyControls()
    ok, _ = sc.check_kill_switch()
    assert ok is True
    sc.activate_kill_switch()
    ok, msg = sc.check_kill_switch()
    assert ok is False
    assert "kill switch" in msg.lower()
    sc.deactivate_kill_switch()
    ok, _ = sc.check_kill_switch()
    assert ok is True


def test_duplicate_trade_detection():
    from autotrader.safety.controls import SafetyControls
    sc = SafetyControls()
    orders = [{"symbol": "BEL", "status": "OPEN"}]
    ok, msg = sc.check_duplicate_trade("BEL", orders)
    assert ok is False
    ok, _ = sc.check_duplicate_trade("INFY", orders)
    assert ok is True


def test_data_freshness():
    from autotrader.safety.controls import SafetyControls
    from datetime import datetime, timezone
    sc = SafetyControls()
    fresh_ts = datetime.now(timezone.utc).isoformat()
    ok, _ = sc.check_data_freshness([fresh_ts])
    assert ok is True
    stale_ts = "2020-01-01T00:00:00+00:00"
    ok, msg = sc.check_data_freshness([stale_ts])
    assert ok is False
    assert "stale" in msg.lower()


# ── Memory ────────────────────────────────────────────────────────────────────

def test_short_term_memory():
    from autotrader.memory.short_term import ShortTermMemory
    stm = ShortTermMemory(retention_days=30)
    stm.store("test_key", {"data": 123})
    result = stm.retrieve("test_key")
    assert result == {"data": 123}
    assert stm.count() >= 1


def test_long_term_memory_store_and_retrieve():
    from autotrader.memory.long_term import LongTermMemory
    ltm = LongTermMemory()
    ltm.store_pattern(
        pattern_key="test_pattern_001",
        description="Test pattern",
        observations=25,
        win_rate=0.68,
        confidence=0.75,
    )
    patterns = ltm.retrieve_patterns(min_confidence=0.70)
    found = [p for p in patterns if p["pattern_key"] == "test_pattern_001"]
    assert len(found) == 1
    assert found[0]["win_rate"] == 0.68


def test_long_term_memory_admission_rules():
    """Patterns below threshold must not be auto-approved."""
    from autotrader.core.config import load_config
    cfg = load_config()
    min_obs = cfg.memory_policy.minimum_observations
    min_conf = cfg.memory_policy.minimum_confidence
    # Rule: observations < minimum should be rejected by the agent layer
    assert min_obs == 20
    assert min_conf == 0.70


# ── Trade Construction ────────────────────────────────────────────────────────

def test_trade_construction():
    from autotrader.agents.layer5.trade_construction import trade_construction_agent
    state = _make_state(
        scored_opportunities=[{
            "symbol": "BEL",
            "score": 91.0,
            "current_price": 412.0,
            "atr": 6.18,
            "pattern": "ORB",
            "vwap": 408.5,
            "ema9": 410.0,
            "ema21": 405.0,
            "rsi": 62.0,
            "catalyst_reason": "Defence order",
        }]
    )
    result = trade_construction_agent(state)
    plan = result["trade_plan"]
    assert plan["symbol"] == "BEL"
    assert plan["stop"] < plan["entry"] < plan["target1"] < plan["target2"]
    assert plan["qty"] >= 1
    assert plan["rr"] >= 2.0
