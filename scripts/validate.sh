#!/usr/bin/env bash
# Validate a change BEFORE deploying — run this after any logic/scoring edit.
#
#   bash scripts/validate.sh
#
# Always runs: unit tests + import/compile checks (fast, no network).
# If UPSTOX_ANALYTICS_TOKEN is available (loaded from .env): also runs the
# walk-forward daily backtest and the intraday backtest, and prints their
# bootstrap-significance verdicts. Backtests never write live params here
# (--no-write), so this is safe to run anytime.
#
# What this catches: strategy/scoring/param/guard REGRESSIONS on historical data.
# What it does NOT catch: live API/integration bugs (e.g. a broker key mismatch),
# scheduling, or real-fill quirks — those still need a live paper day.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=src
DATE=$(date +%F)

echo "── 1. Unit tests ──────────────────────────────────────────────"
python -m pytest tests/ -q 2>&1 | tail -6

echo "── 2. Compile / import checks ─────────────────────────────────"
python -m py_compile \
  src/autotrader/agents/layer3/opportunity_scoring.py \
  src/autotrader/agents/layer5/entry.py \
  src/autotrader/agents/layer5/intraday_hunt.py \
  src/autotrader/agents/layer6/dry_run_pnl.py \
  src/autotrader/agents/layer1/market_regime.py \
  scripts/run_backtest.py scripts/run_intraday_backtest.py \
  && echo "compile OK"

# Load .env so a manual run can see the token (systemd injects it in prod).
[ -f .env ] && set -a && . ./.env 2>/dev/null && set +a
if [ -z "${UPSTOX_ANALYTICS_TOKEN:-}" ]; then
  echo "── Backtests SKIPPED (no UPSTOX_ANALYTICS_TOKEN) ──────────────"
  echo "Run on OCI (or export the token) to validate strategy on history."
  exit 0
fi

echo "── 3. Walk-forward daily backtest (selection, --no-write) ─────"
python scripts/run_backtest.py --months 12 --universe broad --no-write \
  --out "reports/val_daily_${DATE}.json" 2>&1 | grep -E "A/B ON HELD-OUT|baseline|enhanced|WINNER|significant" | tail -8

echo "── 4. Intraday backtest (the real machinery + regime split) ───"
python scripts/run_intraday_backtest.py --months 12 --universe broad \
  --level-mode pct --stop-pct 0.6 --out "reports/val_intraday_${DATE}.json" 2>&1 \
  | grep -E "INTRADAY BACKTEST|machinery|naive|Δ|significant|LONG-FAV|OTHER|entries" | tail -20

echo "── Done. Read the CIs: an edge whose 95% CI straddles 0 is NOT real. ──"
