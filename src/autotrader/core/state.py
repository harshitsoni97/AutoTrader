"""LangGraph state definition for the trading platform."""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from autotrader.core.config import load_config


class TradingState(TypedDict):
    # Session metadata
    run_date: str
    session_type: str  # pre_market | intraday | post_market
    strategy_version: str
    memory_version: str
    config_version: str

    # Layer 1 outputs
    market_regime: str          # bullish | bearish | range_bound | high_volatility | risk_on | risk_off
    market_confidence: float    # 0-1
    top_sectors: list[str]
    sector_rankings: list[dict]
    catalysts: list[dict]       # [{symbol, catalyst_score, reason}]

    # Layer 2 outputs
    candidates: list[dict]      # [{symbol, relative_strength, volume_score, technical_score, pattern, ...}]

    # Layer 3 outputs
    scored_opportunities: list[dict]  # [{symbol, score, component_scores}]

    # Layer 4 outputs
    governance_approved: bool
    governance_reason: str
    risk_passed: bool
    risk_reason: str

    # Layer 5 outputs
    trade_plan: dict            # {symbol, entry, stop, target1, target2, position_size}
    orders: Annotated[list[dict], operator.add]
    positions: list[dict]

    # P&L tracking
    daily_pnl: float
    daily_trades_taken: int
    consecutive_losses: int

    # Communication
    messages: Annotated[list[dict], operator.add]
    audit_trail: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]

    # Post-market / learning
    trade_outcomes: list[dict]
    agent_scores: dict[str, float]
    learning_report_path: str


def create_initial_state(session_type: str = "pre_market") -> TradingState:
    cfg = load_config()
    sv = cfg.strategy_version
    today = date.today().isoformat()
    return TradingState(
        run_date=today,
        session_type=session_type,
        strategy_version=sv.strategy_version,
        memory_version=sv.memory_version,
        config_version=sv.config_version,
        market_regime="unknown",
        market_confidence=0.0,
        top_sectors=[],
        sector_rankings=[],
        catalysts=[],
        candidates=[],
        scored_opportunities=[],
        governance_approved=False,
        governance_reason="",
        risk_passed=False,
        risk_reason="",
        trade_plan={},
        orders=[],
        positions=[],
        daily_pnl=0.0,
        daily_trades_taken=0,
        consecutive_losses=0,
        messages=[],
        audit_trail=[],
        errors=[],
        trade_outcomes=[],
        agent_scores={},
        learning_report_path="",
    )
