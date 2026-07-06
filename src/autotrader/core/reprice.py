"""Re-price trading days that were deferred because candles weren't published.

Called at the start of each pre-market run. For every pending run_date, reload
that day's saved session, re-run the dry-run P&L (candles are guaranteed
available by the next morning), and if it now prices cleanly, send the final P&L
summary and clear it from the pending list. Anything still unpriced stays pending
and is retried next run.
"""

from __future__ import annotations

import structlog

from autotrader.core import pending_reprice
from autotrader.core.session_store import load_session

logger = structlog.get_logger()


def reprice_pending(config) -> None:
    pending = pending_reprice.list_pending()
    if not pending:
        return

    from autotrader.agents.layer6.dry_run_pnl import dry_run_pnl_agent
    from autotrader.tools.notifications import get_notifier
    notifier = get_notifier(config.notifications)

    for run_date in pending:
        try:
            saved = load_session(run_date)
            if not saved:
                logger.warning("reprice_no_session", run_date=run_date)
                pending_reprice.remove(run_date)  # nothing to price, ever
                continue

            state = dict(saved)
            state.setdefault("run_date", run_date)
            state["dry_run"] = saved.get("dry_run", True)

            result = dry_run_pnl_agent(state)
            if result.get("pricing_incomplete"):
                logger.info("reprice_still_pending", run_date=run_date)
                continue  # candles still not there — retry next run

            outcomes = result.get("trade_outcomes", [])
            notifier.notify_daily_summary({
                "run_date": run_date,
                "dry_run": saved.get("dry_run", True),
                "trades": saved.get("daily_trades_taken", len(outcomes)),
                "daily_pnl": round(result.get("daily_pnl", 0.0), 2),
                "regime": saved.get("market_regime", "n/a"),
                "trade_outcomes": outcomes,
                "journal_total": result.get("journal_total", 0),
                "final_label": True,
            })
            pending_reprice.remove(run_date)
            logger.info("reprice_finalized", run_date=run_date,
                        pnl=round(result.get("daily_pnl", 0.0), 2))
        except Exception as exc:
            logger.warning("reprice_failed", run_date=run_date, error=str(exc))
