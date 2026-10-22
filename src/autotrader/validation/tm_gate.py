"""Time Machine gate — turn a pre-trade expectancy report into a BOOK/SKIP decision,
while preserving BOTH the raw decision and the Time-Machine-influenced final decision
so the Slack message can show the two side by side.

Kept out of entry.py so the agent stays readable and this stays unit-testable: the core
decision function is pure (takes bars), and only `recent_bars_for` touches the network.
"""

from __future__ import annotations

import datetime as _dt
import structlog

from autotrader.validation.time_machine import SetupSpec, time_machine, summarize_line

logger = structlog.get_logger()


def decide(plan: dict, bars: list[dict], cfg) -> dict:
    """Return a decision record for one plan.

    {
      raw_decision:   "BOOK"            # what the pipeline decided (all guards passed)
      final_decision: "BOOK" | "SKIP"   # after Time-Machine influence
      overridden:     bool              # True iff TM flipped BOOK -> SKIP
      verdict:        str               # favorable | marginal | unfavorable | insufficient_data
      line:           str               # one-liner for Slack/journal
      report:         dict              # full TM report (or {})
    }
    Fail-open: insufficient data never overrides. gate=False => advisory (never overrides).
    """
    tm = cfg.time_machine
    rec = {"raw_decision": "BOOK", "final_decision": "BOOK", "overridden": False,
           "verdict": "disabled", "line": "", "report": {}}
    if not tm.enabled:
        return rec

    entry = plan.get("entry") or 0
    stop = plan.get("stop") or 0
    target = plan.get("target1") or plan.get("target") or 0
    setup = SetupSpec(entry=entry, stop=stop, target=target, horizon_bars=tm.horizon_bars)
    if not setup.is_valid():
        rec["verdict"] = "insufficient_data"
        rec["line"] = "TimeMachine: n/a (bad setup geometry)"
        return rec

    report = time_machine(bars, setup, n_sims=tm.n_sims)
    rec["report"] = report
    rec["verdict"] = report.get("verdict", "insufficient_data")
    rec["line"] = summarize_line(report)

    exp = (report.get("monte_carlo") or {}).get("expectancy_r")
    # Only a REAL, below-threshold expectancy can override — and only when gating.
    if tm.gate and exp is not None and exp < tm.min_expectancy_r:
        rec["final_decision"] = "SKIP"
        rec["overridden"] = True
    return rec


def recent_bars_for(symbol: str, cfg) -> list[dict]:
    """Best-effort recent intraday bars for `symbol` (list of {close,...}). Fail-open [].

    Uses Upstox settled historical candles for the last `lookback_days` days. Returns []
    on any problem — the caller must treat [] as 'no override' (fail-open).
    """
    try:
        from autotrader.agents.layer2.technical_structure import _load_instrument_map
        ikey = _load_instrument_map().get(symbol)
        if not ikey:
            return []
        from autotrader.tools import upstox_data
        today = _dt.date.today()
        frm = (today - _dt.timedelta(days=max(2, cfg.time_machine.lookback_days))).strftime("%Y-%m-%d")
        to = today.strftime("%Y-%m-%d")
        candles = upstox_data.get_historical_candles(ikey, "minutes", 5, frm, to)
        return candles or []
    except Exception as exc:
        logger.warning("time_machine_bars_fetch_failed", symbol=symbol, error=str(exc))
        return []
