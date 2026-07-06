#!/usr/bin/env python3
"""Test the self-healing deferred re-pricing flow on OCI with REAL Upstox data.

Simulates: post-market runs before candles publish (deferred) → next pre-market
re-prices it once candles are available. Uses a real saved session for pricing,
but mocks the notifier (no Slack) and the journal (no writes), and restores the
pending store afterward — so it's safe to run anytime.

    python3 scripts/test_reprice.py [YYYY-MM-DD]

With no date it picks the most recent reports/<date>_session.json that has trades.
Pick a date that already has candles published (e.g. a prior trading day) so the
re-price phase gets real prices.
"""
import os
import sys
import glob
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pathlib import Path
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env, override=True)
    except ImportError:
        for line in _env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

REPORTS = os.path.join(os.path.dirname(__file__), "..", "reports")


def _pick_date(arg):
    if arg:
        return arg
    files = sorted(glob.glob(os.path.join(REPORTS, "*_session.json")))
    for f in reversed(files):
        try:
            s = json.load(open(f))
            if s.get("positions") and s.get("daily_trades_taken", 0) > 0:
                return s.get("run_date") or os.path.basename(f).split("_")[0]
        except Exception:
            continue
    return None


def main():
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_date = _pick_date(date_arg)
    if not run_date:
        print("No session with trades found. Pass a date: python3 scripts/test_reprice.py 2026-07-03")
        sys.exit(1)

    import autotrader.agents.layer6.dry_run_pnl as dp
    import autotrader.core.reprice as rp
    import autotrader.core.pending_reprice as pr
    import autotrader.tools.notifications as notif
    import autotrader.core.trade_journal as tj
    from autotrader.core.session_store import load_session
    from autotrader.core.config import load_config

    saved = load_session(run_date)
    if not saved or not saved.get("positions"):
        print(f"No positions in session for {run_date}")
        sys.exit(1)

    print(f"=== Testing deferred re-pricing for {run_date} "
          f"({len(saved['positions'])} positions) ===\n")

    # Protect real state: no journal writes, no Slack, snapshot pending store.
    tj.append_outcomes = lambda **k: 0
    tj.count_rows = lambda: 0
    sent = []

    class FakeNotifier:
        def notify_daily_summary(self, s): sent.append(("summary", s))
        def send(self, subject, body=None): sent.append(("send", subject))
    notif.get_notifier = lambda cfg: FakeNotifier()
    pending_backup = pr.list_pending()

    real_day_ohlc = dp._day_ohlc
    cfg = load_config()

    try:
        # ---- PHASE 1: candles NOT published → deferred ----
        print("PHASE 1 — post-market before candles publish (mock unavailable):")
        dp._day_ohlc = lambda sym, target_date=None: None
        state = {**saved, "dry_run": True, "run_date": run_date}
        r1 = dp.dry_run_pnl_agent(state)
        incomplete = r1.get("pricing_incomplete")
        print(f"  pricing_incomplete = {incomplete} (expect True)")
        if incomplete and run_date not in pr.list_pending():
            pr.add(run_date)
        print(f"  pending list = {pr.list_pending()}\n")

        # ---- PHASE 2: candles available (REAL data) → re-price ----
        print("PHASE 2 — next pre-market, real Upstox prices:")
        dp._day_ohlc = real_day_ohlc
        rp.reprice_pending(cfg)
        still_pending = run_date in pr.list_pending()
        summaries = [s for k, s in sent if k == "summary"]
        ok = incomplete and not still_pending and summaries and summaries[0].get("final_label")
        if summaries:
            s = summaries[0]
            print(f"  Final P&L sent: run_date={s.get('run_date')} "
                  f"daily_pnl=₹{s.get('daily_pnl')} final_label={s.get('final_label')}")
            for o in s.get("trade_outcomes", []):
                print(f"    {o.get('symbol'):<12} {o.get('scenario'):<18} ₹{o.get('pnl')}")
        else:
            print("  (no final summary produced — candles for this date may not be "
                  "published yet; pick an older trading day)")
        print(f"  pending after reprice = {pr.list_pending()}\n")
        print("RESULT:", "PASS ✅" if ok else "FAIL / inconclusive ❌")
    finally:
        # restore pending store exactly as it was
        dp._day_ohlc = real_day_ohlc
        for d in pr.list_pending():
            if d not in pending_backup:
                pr.remove(d)


if __name__ == "__main__":
    main()
