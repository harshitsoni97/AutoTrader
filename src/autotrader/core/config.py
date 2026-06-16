"""Configuration loader — reads YAML configs and exposes typed Pydantic models."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, Field


CONFIG_ROOT = Path(__file__).parent.parent.parent.parent / "config"


class TradingPolicy(BaseModel):
    enabled: bool = True
    max_daily_trades: int = 3
    max_concurrent_positions: int = 2
    max_capital_per_trade_pct: float = 10.0
    max_sector_exposure_pct: float = 30.0
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 5.0
    max_monthly_loss_pct: float = 10.0
    max_risk_per_trade_pct: float = 0.5
    min_risk_reward: float = 2.0
    minimum_score: float = 80.0
    minimum_confidence: float = 0.75
    minimum_volume_multiple: float = 2.0
    stop_trading_after_losses: int = 3
    allow_reentry_same_stock: bool = False
    allow_overnight_positions: bool = False
    total_capital: float = 1_000_000.0
    blocked_regimes: List[str] = Field(default_factory=list)
    dry_run: bool = True  # When True: no real orders sent; post-market compares assumed vs actual


class MemoryPolicy(BaseModel):
    auto_modify_strategy: bool = False
    require_review: bool = True
    minimum_observations: int = 20
    minimum_confidence: float = 0.70
    short_term_retention_days: int = 30
    compression_frequency_days: int = 7


class StrategyVersion(BaseModel):
    strategy_version: str = "1.0.0"
    memory_version: str = "1.0"
    config_version: str = "1"


class PlatformConfig(BaseModel):
    trading_policy: TradingPolicy = Field(default_factory=TradingPolicy)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    strategy_version: StrategyVersion = Field(default_factory=StrategyVersion)


# Alias used by tests and scripts
AppConfig = PlatformConfig


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_config(config_root: Path | None = None) -> PlatformConfig:
    root = Path(config_root) if config_root else CONFIG_ROOT

    tp_data = _load_yaml(root / "trading_policy.yaml").get("trading_policy", {})
    mp_data = _load_yaml(root / "memory_policy.yaml").get("memory_policy", {})
    sv_data = _load_yaml(root / "strategy_version.yaml")

    # Environment variable overrides for key settings
    if os.getenv("TRADING_ENABLED") is not None:
        tp_data["enabled"] = os.getenv("TRADING_ENABLED", "true").lower() == "true"
    if os.getenv("TOTAL_CAPITAL"):
        tp_data["total_capital"] = float(os.getenv("TOTAL_CAPITAL"))
    if os.getenv("DRY_RUN") is not None:
        tp_data["dry_run"] = os.getenv("DRY_RUN", "true").lower() == "true"

    return PlatformConfig(
        trading_policy=TradingPolicy(**tp_data),
        memory_policy=MemoryPolicy(**mp_data),
        strategy_version=StrategyVersion(**sv_data),
    )
