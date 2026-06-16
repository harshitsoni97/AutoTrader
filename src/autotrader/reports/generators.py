"""Report generators — produce markdown reports from TradingState."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from autotrader.core.state import TradingState


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def generate_daily_trade_report(state: TradingState) -> str:
    orders = state.get("orders", [])
    positions = state.get("positions", [])
    daily_pnl = state.get("daily_pnl", 0.0)
    run_date = state.get("run_date", "N/A")

    orders_section = "\n".join(
        f"| {o.get('order_id')} | {o.get('symbol')} | {o.get('side')} | {o.get('qty')} | {o.get('fill_price', 0):.2f} | {o.get('status')} |"
        for o in orders
    ) or "| — | No orders | — | — | — | — |"

    positions_section = "\n".join(
        f"| {p.get('symbol')} | {p.get('qty')} | {p.get('entry_price', 0):.2f} | {p.get('stop', 0):.2f} | {p.get('target1', 0):.2f} | {p.get('status')} | {p.get('unrealized_pnl', 0):.0f} |"
        for p in positions
    ) or "| — | No positions | — | — | — | — | — |"

    return f"""# Daily Trade Report
**Date:** {run_date}  **Generated:** {_now_str()}
**Strategy:** {state.get('strategy_version')}  **Config:** v{state.get('config_version')}

## Orders
| Order ID | Symbol | Side | Qty | Fill Price | Status |
|----------|--------|------|-----|-----------|--------|
{orders_section}

## Positions
| Symbol | Qty | Entry | Stop | Target1 | Status | Unrealized PnL |
|--------|-----|-------|------|---------|--------|----------------|
{positions_section}

## P&L Summary
- **Daily PnL:** {daily_pnl:+,.0f} INR
- **Daily Trades Taken:** {state.get('daily_trades_taken', 0)}
- **Consecutive Losses:** {state.get('consecutive_losses', 0)}
"""


def generate_agent_diagnostic_report(state: TradingState) -> str:
    agent_scores = state.get("agent_scores", {})
    scores_section = "\n".join(
        f"- **{agent}:** {score:.1f}%"
        for agent, score in agent_scores.items()
    ) or "- No agent scores available"

    return f"""# Agent Diagnostic Report
**Date:** {state.get('run_date', 'N/A')}  **Generated:** {_now_str()}

## Market Intelligence
- **Regime:** {state.get('market_regime', 'N/A')} (confidence: {state.get('market_confidence', 0):.2f})
- **Top Sectors:** {', '.join(state.get('top_sectors', [])) or 'None'}
- **Catalysts Found:** {len(state.get('catalysts', []))}

## Discovery
- **Candidates Identified:** {len(state.get('candidates', []))}
- **Opportunities Scored:** {len(state.get('scored_opportunities', []))}

## Decision
- **Governance:** {'APPROVED' if state.get('governance_approved') else 'REJECTED'} — {state.get('governance_reason', '')}
- **Risk:** {'PASSED' if state.get('risk_passed') else 'FAILED'} — {state.get('risk_reason', '')}

## Agent Accuracy Scores
{scores_section}
"""


def generate_audit_trail_report(state: TradingState) -> str:
    trail = state.get("audit_trail", [])
    entries = "\n".join(
        f"- **{e.get('timestamp', '')}** | `{e.get('agent')}` | {e.get('action')} | {str(e.get('data', {}))[:120]}"
        for e in trail
    ) or "- No audit entries"

    return f"""# Audit Trail Report
**Date:** {state.get('run_date', 'N/A')}  **Generated:** {_now_str()}
**Total Decisions:** {len(trail)}

## Decision Log
{entries}
"""


def generate_governance_report(state: TradingState) -> str:
    return f"""# Governance Report
**Date:** {state.get('run_date', 'N/A')}  **Generated:** {_now_str()}

## Outcome
- **Approved:** {state.get('governance_approved')}
- **Reason:** {state.get('governance_reason', 'N/A')}

## Policy Limits Used
- **Daily Trades Taken:** {state.get('daily_trades_taken', 0)}
- **Open Positions:** {len(state.get('positions', []))}
- **Daily PnL:** {state.get('daily_pnl', 0):+,.0f} INR
- **Consecutive Losses:** {state.get('consecutive_losses', 0)}
- **Market Regime:** {state.get('market_regime', 'N/A')} (conf: {state.get('market_confidence', 0):.2f})
"""


def generate_a2a_communication_report(state: TradingState) -> str:
    messages = state.get("messages", [])
    msgs_section = "\n".join(
        f"- `{m.get('source_agent')}` → `{m.get('target_agent')}` | {m.get('symbol', '')} | {str(m.get('payload', {}))[:100]}"
        for m in messages
    ) or "- No A2A messages"

    return f"""# A2A Communication Report
**Date:** {state.get('run_date', 'N/A')}  **Generated:** {_now_str()}
**Total Messages:** {len(messages)}

## Message Log
{msgs_section}
"""


def save_report(content: str, filename: str, reports_dir: str = "reports") -> str:
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def save_all_reports(state: TradingState, reports_dir: str = "reports") -> dict[str, str]:
    run_date = state.get("run_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    paths = {}
    paths["trade"] = save_report(generate_daily_trade_report(state), f"{run_date}_trade_report.md", reports_dir)
    paths["diagnostic"] = save_report(generate_agent_diagnostic_report(state), f"{run_date}_diagnostic.md", reports_dir)
    paths["audit"] = save_report(generate_audit_trail_report(state), f"{run_date}_audit.md", reports_dir)
    paths["governance"] = save_report(generate_governance_report(state), f"{run_date}_governance.md", reports_dir)
    paths["a2a"] = save_report(generate_a2a_communication_report(state), f"{run_date}_a2a.md", reports_dir)
    return paths
