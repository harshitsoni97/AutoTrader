"""Long-term investment advisor for Indian equities.

Offline, deterministic tools behind the invest-* Claude skills: holdings
diagnostics, Indian capital-gains tax and harvesting, rebalance planning,
fundamentals scoring and scenario (EV) checks. Nothing here places orders.
"""
from .analysis import analyze
from .holdings import Holding, load_holdings
from .policy import load_policy
from .rebalance import plan_rebalance
from .research import quality_score, scenario_ev
from .tax import compute_tax, tax_report

__all__ = ["Holding", "load_holdings", "load_policy", "analyze", "tax_report", "compute_tax",
           "plan_rebalance", "quality_score", "scenario_ev"]
