"""Unit tests for AutoTrader agents and core components."""
import pytest
import sys
import os

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from autotrader.core.config import load_config, AppConfig
from autotrader.core.state import TradingState, create_initial_state
from autotrader.core.messages import create_message, audit_entry, A2AMessage
from autotrader.memory.short_term import ShortTermMemory
from autotrader.memory.long_term import LongTermMemory
from autotrader.safety.controls import SafetyControls


# ─────────────────────────────────────────────
# Config tests
# ─────────────────────────────────────────────

def test_config_loading():
    """load_config returns AppConfig with all required fields."""
    config = load_config()
    assert isinstance(config, AppConfig)
    assert config.trading_policy.max_daily_trades == 3
    assert config.trading_policy.total_capital == 1_000_000
    assert config.trading_policy.enabled is True
    assert config.memory_policy.minimum_confidence == 0.70
    assert config.strategy_version.strategy_version == "1.0.0"


# ─────────────────────────────────────────────
# Message tests
# ─────────────────────────────────────────────

def test_create_message():
    """create_message returns dict with all required fields."""
    msg = create_message(
        source="agent_a",
        target="agent_b",
        symbol="RELIANCE",
        payload={"foo": "bar"},
    )
    assert isinstance(msg, dict)
    assert "message_id" in msg
    assert "timestamp" in msg
    assert msg["source_agent"] == "agent_a"
    assert msg["target_agent"] == "agent_b"
    assert msg["symbol"] == "RELIANCE"
    assert msg["payload"] == {"foo": "bar"}
    assert len(msg["message_id"]) == 36  # UUID4 format


def test_audit_entry():
    """audit_entry returns dict with timestamp, agent, action, data."""
    entry = audit_entry(
        agent="test_agent",
        action="test_action",
        data={"key": "value"},
    )
    assert isinstance(entry, dict)
    assert "timestamp" in entry
    assert entry["agent"] == "test_agent"
    assert entry["action"] == "test_action"
    assert entry["data"] == {"key": "value"}


# ─────────────────────────────────────────────
# State tests
# ─────────────────────────────────────────────

def test_create_initial_state():
    """create_initial_state returns TradingState with all required fields."""
    state = create_initial_state(session_type="pre_market")
    
    assert state["market_regime"] == "unknown"
    assert state["market_confidence"] == 0.0
    assert state["top_sectors"] == []
    assert state["catalysts"] == []
    assert state["candidates"] == []
    assert state["scored_opportunities"] == []
    assert state["governance_approved"] is False
    assert state["governance_reason"] == ""
    assert state["risk_passed"] is False
    assert state["risk_reason"] == ""
    assert state["trade_plan"] == {}
    assert state["orders"] == []
    assert state["positions"] == []
    assert state["daily_pnl"] == 0.0
    assert state["daily_trades_taken"] == 0
    assert state["consecutive_losses"] == 0
    assert state["messages"] == []
    assert state["errors"] == []
    assert state["audit_trail"] == []
    assert state["session_type"] == "pre_market"
    assert "run_date" in state
    assert state["strategy_version"] == "1.0.0"


# ─────────────────────────────────────────────
# Governance tests
# ─────────────────────────────────────────────

def _make_approvable_state() -> dict:
    """Build a state where all governance checks should pass."""
    return {
        "market_regime": "bull",
        "market_confidence": 0.85,
        "top_sectors": ["BANK", "IT"],
        "scored_opportunities": [
            {"symbol": "HDFCBANK", "composite_score": 85.0}
        ],
        "governance_approved": False,
        "governance_reason": "",
        "risk_passed": False,
        "risk_reason": "",
        "daily_trades_taken": 0,
        "consecutive_losses": 0,
        "daily_pnl": 0.0,
        "positions": [],
        "orders": [],
        "trade_plan": {},
        "candidates": [],
        "catalysts": [],
        "messages": [],
        "errors": [],
        "audit_trail": [],
        "strategy_version": "1.0.0",
        "memory_version": "1.0",
        "config_version": "1",
        "run_date": "2025-01-01",
        "session_type": "pre_market",
    }


def test_governance_approved():
    """When all checks pass, governance_approved should be True."""
    from autotrader.agents.layer4.governance import governance_agent
    state = _make_approvable_state()
    result = governance_agent(state)
    assert result["governance_approved"] is True
    assert "All" in result["governance_reason"] or "passed" in result["governance_reason"]


def test_governance_rejected_disabled():
    """When trading.enabled is False, governance should reject."""
    from autotrader.agents.layer4.governance import governance_agent
    from unittest.mock import patch, MagicMock
    
    state = _make_approvable_state()
    
    # Mock config to return disabled policy
    mock_config = MagicMock()
    mock_config.trading_policy.enabled = False
    mock_config.trading_policy.max_daily_trades = 3
    mock_config.trading_policy.max_concurrent_positions = 2
    mock_config.trading_policy.max_daily_loss_pct = 2.0
    mock_config.trading_policy.total_capital = 1_000_000
    mock_config.trading_policy.stop_trading_after_losses = 3
    mock_config.trading_policy.minimum_score = 80
    mock_config.trading_policy.minimum_confidence = 0.75
    
    with patch("autotrader.agents.layer4.governance.load_config", return_value=mock_config):
        result = governance_agent(state)
    
    assert result["governance_approved"] is False
    assert "disabled" in result["governance_reason"].lower()


def test_governance_rejected_max_trades():
    """When daily_trades_taken >= max_daily_trades, governance should reject."""
    from autotrader.agents.layer4.governance import governance_agent
    
    state = _make_approvable_state()
    state["daily_trades_taken"] = 3  # equals max_daily_trades
    
    result = governance_agent(state)
    assert result["governance_approved"] is False
    assert "trade" in result["governance_reason"].lower() or "limit" in result["governance_reason"].lower()


# ─────────────────────────────────────────────
# Risk agent tests
# ─────────────────────────────────────────────

def test_risk_agent_passes():
    """Risk agent passes with good stock data."""
    from autotrader.agents.layer4.risk import risk_agent
    from unittest.mock import patch
    
    state = _make_approvable_state()
    state["scored_opportunities"] = [{"symbol": "TCS", "composite_score": 85.0}]
    
    mock_stock = {
        "symbol": "TCS",
        "price": 3500.0,
        "avg_volume_20d": 1_000_000,
        "atr": 50.0,
        "volume": 1_200_000,
    }
    
    with patch("autotrader.agents.layer4.risk.get_stock_data", return_value=mock_stock), \
         patch("autotrader.agents.layer4.risk.get_asm_gsm_list", return_value=[]), \
         patch("autotrader.agents.layer4.risk.get_corporate_actions", return_value=[]):
        result = risk_agent(state)
    
    assert result["risk_passed"] is True


def test_risk_agent_low_volume():
    """Risk agent fails when avg_volume < 500000."""
    from autotrader.agents.layer4.risk import risk_agent
    from unittest.mock import patch
    
    state = _make_approvable_state()
    state["scored_opportunities"] = [{"symbol": "SMALLCAP", "composite_score": 85.0}]
    
    mock_stock = {
        "symbol": "SMALLCAP",
        "price": 100.0,
        "avg_volume_20d": 100_000,  # too low
        "atr": 2.0,
        "volume": 150_000,
    }
    
    with patch("autotrader.agents.layer4.risk.get_stock_data", return_value=mock_stock):
        result = risk_agent(state)
    
    assert result["risk_passed"] is False
    assert "volume" in result["risk_reason"].lower()


# ─────────────────────────────────────────────
# Opportunity scoring tests
# ─────────────────────────────────────────────

def test_opportunity_scoring():
    """Verify composite score calculation uses correct weights."""
    from autotrader.agents.layer3.opportunity_scoring import opportunity_scoring_agent
    from unittest.mock import patch, MagicMock
    
    mock_config = MagicMock()
    mock_config.trading_policy.minimum_score = 0  # allow all through
    
    state = {
        "market_regime": "strong_bull",  # score=100
        "market_confidence": 0.9,
        "top_sectors": ["BANK"],
        "catalysts": [{"symbol": "HDFCBANK", "catalyst_type": "bulk_deal", "score": 65}],
        "candidates": [
            {
                "symbol": "HDFCBANK",
                "sector": "BANK",
                "rs_score": 80.0,
                "volume_score": 60.0,
                "technical_score": 70.0,
            }
        ],
        "scored_opportunities": [],
        "messages": [],
        "audit_trail": [],
    }
    
    with patch("autotrader.agents.layer3.opportunity_scoring.load_config", return_value=mock_config):
        result = opportunity_scoring_agent(state)
    
    scored = result.get("scored_opportunities", [])
    assert len(scored) == 1
    score = scored[0]["composite_score"]
    
    # Expected: market(100*0.20) + sector(100*0.20) + rs(80*0.20) + vol(60*0.15) + catalyst(65*0.15) + tech(70*0.10)
    expected = 100*0.20 + 100*0.20 + 80*0.20 + 60*0.15 + 65*0.15 + 70*0.10
    assert abs(score - expected) < 0.01


# ─────────────────────────────────────────────
# Safety controls tests
# ─────────────────────────────────────────────

def test_safety_controls_kill_switch():
    """When kill_switch is True, check_kill_switch returns False."""
    safety = SafetyControls()
    assert safety.check_kill_switch() is True  # starts off
    
    safety.kill_switch = True
    assert safety.check_kill_switch() is False
    
    ok, issues = safety.run_all_checks_basic()
    assert ok is False
    assert any("kill switch" in i.lower() for i in issues)


# ─────────────────────────────────────────────
# Memory tests
# ─────────────────────────────────────────────

def test_short_term_memory():
    """ShortTermMemory stores and retrieves values correctly."""
    mem = ShortTermMemory(ttl_days=30)
    
    mem.store("key1", {"data": 42})
    mem.store("key2", "hello world")
    
    assert mem.retrieve("key1") == {"data": 42}
    assert mem.retrieve("key2") == "hello world"
    assert mem.retrieve("nonexistent") is None
    
    assert "key1" in mem.keys()
    assert "key2" in mem.keys()
    
    results = mem.search("hello")
    assert len(results) >= 1
    assert any(r["key"] == "key2" for r in results)
    
    d = mem.to_dict()
    assert "key1" in d
    assert "key2" in d


def test_long_term_memory_pattern():
    """LongTermMemory stores patterns and retrieves by min_confidence."""
    mem = LongTermMemory()
    
    # Store a pattern with high confidence
    mid = mem.store_pattern(
        pattern="bull_BANK_BREAKOUT",
        observations=30,
        win_rate=0.75,
        confidence=0.8,
    )
    assert isinstance(mid, str)
    assert len(mid) == 36
    
    # Should appear with low threshold
    patterns = mem.retrieve_patterns(min_confidence=0.0)
    assert len(patterns) >= 1
    
    # Should appear with 0.7 threshold
    patterns_filtered = mem.retrieve_patterns(min_confidence=0.7)
    assert len(patterns_filtered) >= 1
    
    # Should NOT appear with very high threshold
    patterns_high = mem.retrieve_patterns(min_confidence=0.99)
    assert all(p["confidence"] >= 0.99 for p in patterns_high)
    
    # Test update
    mem.update_pattern(mid, new_observation=True)
    updated = mem.retrieve_patterns(min_confidence=0.0)
    found = next((p for p in updated if p["memory_id"] == mid), None)
    assert found is not None
    assert found["observations"] == 31
    
    # Stats
    stats = mem.get_stats()
    assert stats["total_patterns"] >= 1
    assert "avg_win_rate" in stats


# ─────────────────────────────────────────────
# Broker connector tests
# ─────────────────────────────────────────────
def test_broker_factory_selects_mock():
    from autotrader.tools.broker_tools import get_broker, MockBroker
    cfg = load_config()
    broker = get_broker(cfg.broker)
    assert isinstance(broker, MockBroker)
    assert broker.is_connected()


def test_broker_factory_unknown_provider_raises():
    from autotrader.tools.broker_tools import get_broker
    cfg = load_config()
    bad = cfg.broker.model_copy(update={"provider": "robinhood"})
    with pytest.raises(ValueError):
        get_broker(bad)


def test_live_brokers_fail_closed_without_credentials(monkeypatch):
    from autotrader.tools.broker_tools import get_broker, BrokerAuthError
    cfg = load_config()
    for var in ("KITE_API_KEY", "KITE_ACCESS_TOKEN", "UPSTOX_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    for provider in ("zerodha", "upstox"):
        bad = cfg.broker.model_copy(update={"provider": provider})
        with pytest.raises(BrokerAuthError):
            get_broker(bad)


def test_mock_broker_idempotent_tag():
    from autotrader.tools.broker_tools import MockBroker
    b = MockBroker()
    o1 = b.place_order("INFY", 10, "BUY", tag="AT-dedup")
    o2 = b.place_order("INFY", 10, "BUY", tag="AT-dedup")
    assert o1["order_id"] == o2["order_id"]
    assert len(b.get_orders()) == 1


def test_order_schema_validation():
    from autotrader.tools.broker_tools import Order, MockBroker
    order = MockBroker().place_order("BEL", 5, "BUY")
    # Round-trips through the canonical Pydantic schema
    assert Order(**order).symbol == "BEL"


# ─────────────────────────────────────────────
# Execution idempotency tests
# ─────────────────────────────────────────────
def test_execution_suppresses_duplicate():
    from autotrader.agents.layer5.execution import execution_agent
    plan = {"symbol": "BEL", "qty": 10, "entry": 412.0, "stop": 400.0, "target1": 420.0, "target2": 430.0}
    state = {"trade_plan": plan, "dry_run": True, "run_date": "2026-06-17", "orders": []}
    first = execution_agent(state)
    assert len(first["orders"]) == 1
    tag = first["orders"][0]["tag"]
    # Replay with the order already present -> no new order
    state2 = dict(state, orders=first["orders"])
    second = execution_agent(state2)
    assert "orders" not in second
    assert any(t["tag"] == tag for t in first["orders"])


# ─────────────────────────────────────────────
# Memory: embeddings, scoring, factory
# ─────────────────────────────────────────────
def test_local_embedder_is_deterministic_and_normalised():
    from autotrader.memory.embeddings import LocalHashEmbedder
    import math
    e = LocalHashEmbedder(dim=64)
    v1 = e.embed("high volume sector leader")
    v2 = e.embed("high volume sector leader")
    assert v1 == v2
    assert abs(math.sqrt(sum(x * x for x in v1)) - 1.0) < 1e-6


def test_retrieval_scoring_components():
    from autotrader.memory.scoring import cosine, recency_decay, composite_score
    from datetime import datetime, timezone
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    now_iso = datetime.now(timezone.utc).isoformat()
    assert recency_decay(now_iso, 30.0) > 0.99
    # Weights normalise even if they don't sum to 1
    assert 0.0 <= composite_score(1.0, 1.0, 1.0, 2, 2, 2) <= 1.0


def test_long_term_search_scored_ranks_relevant_first():
    from autotrader.memory.long_term import LongTermMemory
    mem = LongTermMemory()
    mem.store_pattern(pattern="volume_breakout_banking", description="High volume breakout in banking stocks",
                      observations=20, win_rate=0.7, confidence=0.8)
    mem.store_pattern(pattern="pharma_reversal", description="Mean reversion in pharma names",
                      observations=15, win_rate=0.6, confidence=0.75)
    results = mem.search_scored("banking volume breakout", top_k=2)
    assert results
    assert results[0]["pattern_key"] == "volume_breakout_banking"
    assert "retrieval_score" in results[0]
    assert "embedding" not in results[0]  # embeddings stripped from output


def test_short_term_search_scored():
    from autotrader.memory.short_term import ShortTermMemory
    stm = ShortTermMemory()
    stm.store("regime_2026", {"regime": "bullish", "vix": 12})
    stm.store("trade_BEL", {"symbol": "BEL", "pnl": 1200})
    hits = stm.search_scored("BEL trade outcome", top_k=1)
    assert hits and hits[0]["key"] == "trade_BEL"


def test_memory_factory_defaults_to_in_memory():
    from autotrader.memory import get_long_term_memory, get_short_term_memory
    from autotrader.memory.long_term import LongTermMemory
    from autotrader.memory.short_term import ShortTermMemory
    assert isinstance(get_long_term_memory(), LongTermMemory)
    assert isinstance(get_short_term_memory(), ShortTermMemory)


def test_memory_factory_postgres_falls_back_without_dsn(monkeypatch):
    from autotrader.core.config import load_config
    from autotrader.memory import get_long_term_memory
    from autotrader.memory.long_term import LongTermMemory
    monkeypatch.delenv("DATABASE_URL", raising=False)
    bcfg = load_config().memory_policy.backend.model_copy(update={"provider": "postgres"})
    # No DSN -> graceful fallback to in-memory
    assert isinstance(get_long_term_memory(bcfg), LongTermMemory)


# ----------------------------- Notifications ----------------------------- #
def test_notifier_disabled_is_noop():
    from autotrader.core.config import NotificationConfig
    from autotrader.tools.notifications import get_notifier
    notifier = get_notifier(NotificationConfig(enabled=False))
    assert notifier.send("subject", "body") == {}


def test_notifier_unknown_channel_skipped():
    from autotrader.core.config import NotificationConfig
    from autotrader.tools.notifications import get_notifier
    cfg = NotificationConfig(enabled=True, channels=["bogus"])
    # Unknown channel is skipped, not sent — result contains no entry for it.
    assert get_notifier(cfg).send("s", "b") == {}


def test_notifier_telegram_without_credentials(monkeypatch):
    from autotrader.core.config import NotificationConfig
    from autotrader.tools.notifications import get_notifier
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = NotificationConfig(enabled=True, channels=["telegram"])
    # Missing creds -> delivered False, never raises.
    assert get_notifier(cfg).send("s", "b") == {"telegram": False}


def test_notifier_telegram_sends_with_credentials(monkeypatch):
    from autotrader.core.config import NotificationConfig
    from autotrader.tools import notifications
    from autotrader.tools.notifications import get_notifier
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    calls = {}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["json"] = kwargs.get("json")
        return True

    monkeypatch.setattr(notifications, "_post", fake_post)
    cfg = NotificationConfig(enabled=True, channels=["telegram"])
    result = get_notifier(cfg).send("Entry", "BEL x10")
    assert result == {"telegram": True}
    assert "bottok" in calls["url"]
    assert calls["json"]["chat_id"] == "123"


def test_notifier_event_toggles(monkeypatch):
    from autotrader.core.config import NotificationConfig
    from autotrader.tools.notifications import get_notifier
    cfg = NotificationConfig(enabled=True, channels=["telegram"], notify_on_order=False)
    # notify_on_order disabled -> builder returns {} without touching channels.
    assert get_notifier(cfg).notify_order({"symbol": "BEL", "status": "DRY_RUN_ASSUMED"}) == {}


def test_config_loads_notifications():
    from autotrader.core.config import load_config
    cfg = load_config()
    assert hasattr(cfg, "notifications")
    assert isinstance(cfg.notifications.channels, list)
