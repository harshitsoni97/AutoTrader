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

    # Options Intelligence (Layer 1 parallel)
    options_pcr: float          # Put-Call Ratio by OI
    options_max_pain: float     # Max pain strike
    options_atm_iv: float       # ATM implied volatility %
    options_iv_skew: float      # OTM put IV - OTM call IV (>0 = fear)
    options_signal: str         # bullish | bearish | neutral

    # FII derivatives positioning (from MarketRegimeAgent)
    fii_future_net: float       # FII net index future contracts (long - short)
    fii_net_cash: float         # FII cash net buy/sell (crores) — used for regime enrichment

    # GIFT Nifty gap (from MarketRegimeAgent)
    gift_nifty_gap_pct: float   # Pre-market gap % vs previous Nifty close

    # Raw market inputs stored for compete-mode per-stack re-enrichment
    nifty_change_pct: float     # Nifty 5-day % change
    india_vix: float            # India VIX level
    global_change_pct: float    # Blended global market % change (SP500 + Nasdaq avg)

    # Raw (pre-LLM) catalysts — stored so compete coordinator can re-enrich per stack
    raw_catalysts: list[dict]

    # Layer 2 outputs
    candidates: list[dict]      # [{symbol, relative_strength, volume_score, technical_score, pattern, ...}]

    # Layer 3 outputs
    scored_opportunities: list[dict]  # [{symbol, score, component_scores}]

    # Layer 4 outputs
    governance_approved: bool
    governance_reason: str
    risk_passed: bool
    risk_reason: str
    kelly_fraction: float       # 0 = use fixed-fraction; >0 = Kelly-derived position size

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

    # Dry-run mode flag (propagated from config)
    dry_run: bool

    # Post-market / learning
    trade_outcomes: list[dict]
    agent_scores: dict[str, float]
    learning_report_path: str

    # Compete mode — one entry per competitor, filled by compete_coordinator
    competitor_results: list[dict]


def create_initial_state(session_type: str = "pre_market") -> TradingState:
    cfg = load_config()
    sv = cfg.strategy_version
    today = date.today().isoformat()
    dry_run = cfg.trading_policy.dry_run
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
        options_pcr=1.0,
        options_max_pain=0.0,
        options_atm_iv=0.0,
        options_iv_skew=0.0,
        options_signal="neutral",
        fii_future_net=0.0,
        fii_net_cash=0.0,
        gift_nifty_gap_pct=0.0,
        nifty_change_pct=0.0,
        india_vix=15.0,
        global_change_pct=0.0,
        raw_catalysts=[],
        candidates=[],
        scored_opportunities=[],
        governance_approved=False,
        governance_reason="",
        risk_passed=False,
        risk_reason="",
        kelly_fraction=0.0,
        trade_plan={},
        orders=[],
        positions=[],
        daily_pnl=0.0,
        daily_trades_taken=0,
        consecutive_losses=0,
        dry_run=dry_run,
        messages=[],
        audit_trail=[],
        errors=[],
        trade_outcomes=[],
        agent_scores={},
        learning_report_path="",
        competitor_results=[],
    )
