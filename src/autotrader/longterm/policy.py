"""Load config/longterm_policy.yaml with defaults for anything missing."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[3] / "config" / "longterm_policy.yaml"

DEFAULTS = {
    "portfolio_policy": {
        "target_holdings_min": 12,
        "target_holdings_max": 20,
        "min_position_pct": 2.0,
        "max_position_pct": 10.0,
        "max_group_pct": 15.0,
        "max_sector_pct": 25.0,
        "deep_loss_pct": -30.0,
        "core_target_pct": 50.0,
        "use_funds": True,
        "core_stocks": [],
        "core_instruments": [{"symbol": "NIFTYBEES", "name": "Nifty 50 ETF", "weight": 100}],
        "asset_mix_target_pct": {"equity": 85, "gold": 10, "debt": 5},
        "asset_class_overrides": {},
        "groups": {},
    },
    "tax_policy": {
        "stcg_rate_pct": 20.0,
        "ltcg_rate_pct": 12.5,
        "ltcg_exemption_inr": 125000,
        "cess_pct": 4.0,
        "sell_cost_pct": 0.12,
        "dp_charge_inr": 15.93,
        "non_equity_classes": ["gold", "silver", "debt", "reit"],
    },
    "sip_policy": {
        "monthly_amount_inr": 10000,
        "split_pct": {"core": 60, "satellite_top_up": 25, "gold": 10, "debt": 5},
    },
}


def load_policy(path: str | Path | None = None) -> dict:
    policy = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else DEFAULT_POLICY_PATH
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        for section, values in loaded.items():
            policy.setdefault(section, {}).update(values or {})
    return policy


def asset_class(symbol: str, sector: str, policy: dict) -> str:
    """equity | equity_index | gold | silver | debt | reit"""
    overrides = policy["portfolio_policy"].get("asset_class_overrides", {})
    if symbol in overrides:
        return overrides[symbol]
    if symbol.endswith("-RR") or symbol.endswith("-IV"):
        return "reit"
    if sector == "ETF":
        if "GOLD" in symbol:
            return "gold"
        if "SILV" in symbol:
            return "silver"
        if "LIQUID" in symbol:
            return "debt"
        return "equity_index"
    return "equity"


# Asset classes that count toward the "equity" bucket of the asset mix.
EQUITY_CLASSES = {"equity", "equity_index"}


def core_list(policy: dict) -> list[dict]:
    """The core buy list: index funds/ETFs, or direct stocks when use_funds is off."""
    pp = policy["portfolio_policy"]
    return (pp.get("core_instruments") if pp.get("use_funds", True) else pp.get("core_stocks")) or []
