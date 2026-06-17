"""Report generators for AutoTrader."""
import os
import structlog
from datetime import datetime

logger = structlog.get_logger()


def generate_daily_trade_report(state: dict) -> str:
    """Generate a comprehensive daily trade report in Markdown."""
    run_date = state.get("run_date", datetime.utcnow().strftime("%Y-%m-%d"))
    orders = state.get("orders", [])
    positions = state.get("positions", [])
    daily_pnl = state.get("daily_pnl", 0.0)
    
    closed = [p for p in positions if p.get("status") == "closed"]
    wins = [p for p in closed if p.get("realized_pnl", 0) > 0]
    losses = [p for p in closed if p.get("realized_pnl", 0) <= 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0
    
    lines = [
        f"# Daily Trade Report — {run_date}",
        "",
        "## Summary",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Date | {run_date} |",
        f"| Session | {state.get('session_type', 'N/A')} |",
        f"| Market Regime | {state.get('market_regime', 'N/A')} |",
        f"| Market Confidence | {state.get('market_confidence', 0):.1%} |",
        f"| Daily P&L | ₹{daily_pnl:,.2f} |",
        f"| Total Orders | {len(orders)} |",
        f"| Closed Trades | {len(closed)} |",
        f"| Win Rate | {win_rate:.1f}% |",
        f"| Consecutive Losses | {state.get('consecutive_losses', 0)} |",
        "",
        "## Orders",
    ]
    
    if orders:
        lines.append("| Symbol | Side | Qty | Type | Status | Fill Price |")
        lines.append("|--------|------|-----|------|--------|------------|")
        for o in orders:
            lines.append(
                f"| {o.get('symbol')} | {o.get('side')} | {o.get('quantity')} | "
                f"{o.get('order_type')} | {o.get('status')} | "
                f"₹{o.get('fill_price', 0) or 0:.2f} |"
            )
    else:
        lines.append("_No orders placed._")
    
    lines.extend(["", "## Positions"])
    
    if positions:
        for pos in positions:
            pnl = pos.get("realized_pnl", pos.get("unrealized_pnl", 0)) or 0
            lines.append(
                f"- **{pos.get('symbol')}**: Entry ₹{pos.get('entry_price', 0):.2f}, "
                f"Status: {pos.get('status', 'open')}, P&L: ₹{pnl:+,.2f}"
            )
    else:
        lines.append("_No positions taken._")
    
    lines.append(f"\n_Generated at {datetime.utcnow().isoformat()} UTC_")
    return "\n".join(lines)


def generate_agent_diagnostic_report(state: dict) -> str:
    """Generate agent performance diagnostic report."""
    audit_trail = state.get("audit_trail", [])
    
    # Group by agent
    agent_activity: dict = {}
    for entry in audit_trail:
        agent = entry.get("agent", "unknown")
        if agent not in agent_activity:
            agent_activity[agent] = []
        agent_activity[agent].append(entry)
    
    lines = [
        "# Agent Diagnostic Report",
        f"_Generated: {datetime.utcnow().isoformat()} UTC_",
        "",
        f"**Total Audit Entries**: {len(audit_trail)}",
        f"**Active Agents**: {len(agent_activity)}",
        "",
        "## Agent Activity Summary",
        "",
    ]
    
    for agent, entries in sorted(agent_activity.items()):
        lines.append(f"### {agent}")
        lines.append(f"- Audit entries: {len(entries)}")
        for entry in entries:
            lines.append(f"  - [{entry.get('timestamp', '')}] {entry.get('action', '')}")
        lines.append("")
    
    if not agent_activity:
        lines.append("_No agent activity recorded._")
    
    return "\n".join(lines)


def generate_a2a_learning_report(state: dict) -> str:
    """Generate agent-to-agent message flow report."""
    messages = state.get("messages", [])
    
    lines = [
        "# A2A Message Flow Report",
        f"_Generated: {datetime.utcnow().isoformat()} UTC_",
        "",
        f"**Total Messages**: {len(messages)}",
        "",
        "## Message Log",
        "",
    ]
    
    if messages:
        lines.append("| # | From | To | Symbol | Timestamp |")
        lines.append("|---|------|----|--------|-----------|")
        for i, msg in enumerate(messages, 1):
            lines.append(
                f"| {i} | {msg.get('source_agent', 'N/A')} | "
                f"{msg.get('target_agent', 'N/A')} | "
                f"{msg.get('symbol', 'N/A')} | "
                f"{msg.get('timestamp', 'N/A')} |"
            )
    else:
        lines.append("_No A2A messages recorded._")
    
    return "\n".join(lines)


def generate_audit_trail_report(state: dict) -> str:
    """Generate full audit trail report."""
    audit_trail = state.get("audit_trail", [])
    
    lines = [
        "# Audit Trail Report",
        f"_Generated: {datetime.utcnow().isoformat()} UTC_",
        "",
        f"**Total Entries**: {len(audit_trail)}",
        "",
        "## Chronological Audit Log",
        "",
    ]
    
    for i, entry in enumerate(audit_trail, 1):
        lines.append(f"### Entry {i}: {entry.get('agent')} — {entry.get('action')}")
        lines.append(f"**Timestamp**: {entry.get('timestamp', 'N/A')}")
        data = entry.get("data", {})
        if data:
            lines.append("**Data**:")
            for k, v in data.items():
                lines.append(f"  - {k}: {v}")
        lines.append("")
    
    if not audit_trail:
        lines.append("_No audit entries recorded._")
    
    return "\n".join(lines)


def generate_governance_report(state: dict) -> str:
    """Generate governance decision report."""
    audit_trail = state.get("audit_trail", [])
    gov_entries = [e for e in audit_trail if e.get("agent") == "governance_agent"]
    risk_entries = [e for e in audit_trail if e.get("agent") == "risk_agent"]
    
    lines = [
        "# Governance & Risk Report",
        f"_Generated: {datetime.utcnow().isoformat()} UTC_",
        "",
        "## Governance Decisions",
        "",
    ]
    
    for entry in gov_entries:
        data = entry.get("data", {})
        status = "APPROVED" if data.get("approved") else "REJECTED"
        lines.append(f"- **{status}**: {data.get('reason', 'N/A')}")
        lines.append(f"  - Checks run: {data.get('checks_run', 0)}")
        lines.append(f"  - Timestamp: {entry.get('timestamp', 'N/A')}")
        lines.append("")
    
    if not gov_entries:
        lines.append("_No governance decisions recorded._")
    
    lines.extend(["", "## Risk Checks", ""])
    
    for entry in risk_entries:
        data = entry.get("data", {})
        status = "PASSED" if data.get("passed") else "FAILED"
        lines.append(f"- **{status}** [{data.get('symbol', 'N/A')}]: {data.get('reason', 'N/A')}")
    
    if not risk_entries:
        lines.append("_No risk checks recorded._")
    
    lines.extend([
        "",
        "## Current State",
        f"- Governance Approved: {state.get('governance_approved', False)}",
        f"- Governance Reason: {state.get('governance_reason', 'N/A')}",
        f"- Risk Passed: {state.get('risk_passed', False)}",
        f"- Risk Reason: {state.get('risk_reason', 'N/A')}",
    ])
    
    return "\n".join(lines)


def save_report(content: str, filename: str, reports_dir: str = "reports/") -> str:
    """Save report content to file. Returns the full path."""
    os.makedirs(reports_dir, exist_ok=True)
    full_path = os.path.join(reports_dir, filename)
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("report_saved", path=full_path)
    except Exception as e:
        logger.error("report_save_failed", path=full_path, error=str(e))
        raise
    return full_path
