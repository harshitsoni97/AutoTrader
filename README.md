# AutoTrader — LangGraph Hybrid Intraday Trading Intelligence Platform

A production-grade, multi-agent intraday trading system for Indian equities (NSE) built with LangGraph. The platform orchestrates six layers of analytical agents that analyse market context, identify high-probability setups, enforce strict governance and risk rules, and continuously learn from trade outcomes — all without self-modifying its own strategy.

---

## Table of Contents

1. [Philosophy](#philosophy)
2. [Architecture Overview](#architecture-overview)
3. [Agent Layers](#agent-layers)
4. [Three Execution Graphs](#three-execution-graphs)
5. [Workflow Step Classification](#workflow-step-classification)
6. [Quick Start](#quick-start)
7. [Installation](#installation)
8. [Configuration](#configuration)
9. [LLM Configuration](#llm-configuration)
10. [API Keys and External Dependencies](#api-keys-and-external-dependencies)
11. [Broker Connectors](#broker-connectors)
12. [Running the Platform](#running-the-platform)
13. [Dry-Run Mode](#dry-run-mode)
14. [Output and Reports](#output-and-reports)
15. [Memory System](#memory-system)
16. [Safety Controls and Governance](#safety-controls-and-governance)
17. [LLMOps — Tracing & Prompt Repository](#llmops--tracing--prompt-repository)
18. [Testing](#testing)
19. [Project Structure](#project-structure)
20. [Extending the Platform](#extending-the-platform)
21. [Disclaimer](#disclaimer)

---

## Philosophy

AutoTrader operates on three principles:

- **Signal alignment over single-factor bets** — a trade is taken only when market regime, sector strength, relative strength, volume, catalyst, and technical structure all agree.
- **Governance before execution** — nine deterministic policy checks gate every trade. No opportunity is taken if daily loss limits, position counts, or regime constraints are breached.
- **Learning without self-modification** — the Layer 6 agents observe, evaluate, and store patterns, but they never rewrite strategy code or change config values automatically. All strategy changes require a human review cycle.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PRE-MARKET GRAPH (08:00 IST)                 │
│                                                                     │
│  Layer 1: Market Intelligence                                       │
│    MarketRegime → SectorRotation → CatalystIntelligence             │
│                                                                     │
│  Layer 2: Opportunity Discovery                                     │
│    RelativeStrength → VolumeIntelligence → TechnicalStructure       │
│                                                                     │
│  Layer 3: Decision Engine                                           │
│    OpportunityScoring                                               │
│                                                                     │
│  Layer 4: Governance & Risk                                         │
│    Governance ──[rejected]──► END                                   │
│         │[approved]                                                 │
│    Risk ──[failed]──► END                                           │
│         │[passed]                                                   │
│                                                                     │
│  Layer 5: Execution                                                 │
│    TradeConstruction → Execution                                    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│              INTRADAY GRAPH (every 5 min, 09:15–15:30 IST)          │
│  MarketRegime → Governance → Monitoring                             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   POST-MARKET GRAPH (15:45 IST)                     │
│  DailyLearning → AgentEvaluator → LongTermMemory → MemoryCompression│
└─────────────────────────────────────────────────────────────────────┘
```

---

## Agent Layers

### Layer 1 — Market Intelligence

| Agent | What it does |
|---|---|
| `MarketRegimeAgent` | Reads Nifty50, BankNifty, India VIX, FII/DII flows, and global markets. Classifies the day into one of: `bullish`, `bearish`, `range_bound`, `high_volatility`, `risk_on`, `risk_off`, `strong_bull`. Outputs `market_regime` and `market_confidence` (0–1). |
| `SectorRotationAgent` | Ranks 10 NSE sectors by composite momentum (50% 5-day return + 30% 1-day return + 20% volume breadth). Returns the top 3 sectors and full `sector_rankings`. |
| `CatalystIntelligenceAgent` | Scrapes NSE bulk deals (score: 65), block deals (60), and corporate actions (buyback: 72). Adds +5 for sector alignment. Returns a deduplicated `catalysts` list. |

### Layer 2 — Opportunity Discovery

| Agent | What it does |
|---|---|
| `RelativeStrengthAgent` | For each symbol in top-sector watchlists and catalyst list, computes RS = 50 + (stock 5d return / \|Nifty 5d return\|) × 16.67, clamped 0–100. Builds the `candidates` list. |
| `VolumeIntelligenceAgent` | Adds `volume_score` (capped at 100) = today's volume / 20-day avg × 33.33. Flags `delivery_spike` when price range is narrow but volume > 2×. |
| `TechnicalStructureAgent` | Computes EMA9/21/50, RSI(14), ADX(14), VWAP, ATR(14). Detects patterns: `BREAKOUT`, `ORB`, `VWAP_CROSS`, `EMA_ALIGNMENT`. Adds `technical_score` (max 100). |

### Layer 3 — Decision Engine

| Agent | What it does |
|---|---|
| `OpportunityScoringAgent` | Combines all signals into a weighted composite score. Only candidates above `minimum_score` (default 80) advance. |

**Composite Score Weights**

| Signal | Weight |
|---|---|
| Market Regime | 20% |
| Sector Strength | 20% |
| Relative Strength | 20% |
| Volume | 15% |
| Catalyst | 15% |
| Technical Structure | 10% |

### Layer 4 — Governance & Risk

| Agent | What it does |
|---|---|
| `GovernanceAgent` | Nine sequential policy checks (all deterministic). Fails fast on the first violation. |
| `RiskAgent` | Liquidity check (avg vol > 500k), spread check (<0.2%), ASM/GSM blacklist, corporate action ex-date, ATR sanity, R/R ≥ 2.0, gap risk (<3% gap-up). |

**Governance Checks (in order)**

1. Trading enabled
2. Has eligible opportunities
3. Daily trade limit not reached
4. Concurrent position limit not reached
5. Daily loss limit not breached
6. Consecutive loss circuit breaker
7. Market regime not in blocked list
8. Market confidence above threshold
9. No re-entry on same stock (configurable)

### Layer 5 — Execution

| Agent | What it does |
|---|---|
| `TradeConstructionAgent` | Entry = market price (ORB/BREAKOUT) or just above VWAP (VWAP_CROSS). Stop = entry − 1.5×ATR. Target1 = entry + 2R. Target2 = entry + 3R. Quantity sized by risk budget. |
| `ExecutionAgent` | Sends order to broker (live) or records assumed fill at plan price (dry-run). Stores position in state. |
| `MonitoringAgent` | Checks every 5 minutes: stop hit → exit, Target2 hit → exit, Target1 hit → exit 50%. Updates `daily_pnl` and `consecutive_losses`. |

### Layer 6 — Learning & Memory

| Agent | What it does |
|---|---|
| `DailyLearningAgent` | Generates a 7-section markdown report: session summary, trade outcomes, agent performance, market context, patterns observed, improvement proposals, next-session checklist. |
| `AgentEvaluatorAgent` | Scores each analytical agent on signal accuracy for the day. |
| `LongTermMemoryAgent` | Extracts validated patterns (≥20 observations, ≥0.70 confidence) and proposes storage — never auto-modifies strategy. |
| `MemoryCompressionAgent` | Expires stale patterns (<50% confidence, >90 days old), merges duplicates, boosts high performers. |

---

## Three Execution Graphs

### Pre-Market Graph (`graphs/pre_market.py`)
Runs at **08:00 IST** before the NSE open. Performs the full pipeline from market regime analysis through to order placement (or dry-run assumed fill). The graph uses `conditional_edges` to short-circuit at governance and risk gates — no further agents execute if a gate fails.

### Intraday Graph (`graphs/intraday.py`)
Runs on a **5-minute loop** from 09:15 to 15:30 IST. Refreshes market regime, re-checks governance, and monitors open positions for stop/target triggers.

### Post-Market Graph (`graphs/post_market.py`)
Runs at **15:45 IST** after the market closes. Evaluates the session, stores validated patterns in long-term memory, and produces all daily reports.

---

## Workflow Step Classification

| Step | Classification | Reason |
|---|---|---|
| Market regime determination | Hybrid | Rule-based thresholds on quantitative data; VIX boundary is deterministic but regime label interpretation is heuristic |
| Sector ranking | Deterministic | Fixed momentum formula, deterministic sort |
| Catalyst scoring | Deterministic | Fixed score table per event type; no LLM involved |
| Relative strength calculation | Deterministic | Fixed RS formula |
| Volume intelligence | Deterministic | Fixed ratio and threshold rules |
| Technical indicators (EMA, RSI, ATR) | Deterministic | Standard mathematical formulas |
| Pattern detection | Hybrid | Rule-based but uses approximate VWAP/EMA comparisons with floating-point thresholds |
| Composite scoring | Deterministic | Weighted sum of normalised scores |
| Governance checks | Deterministic | Pure boolean policy rule evaluation |
| Risk validation | Deterministic | Threshold comparisons on market data |
| Trade sizing | Deterministic | Formula-based (risk budget / risk per share) |
| Order execution | Deterministic (dry-run) / Probabilistic (live) | Dry-run: assumed fill at plan price; live: market microstructure introduces slippage |
| Stop/target monitoring | Deterministic | Price comparison against fixed levels |
| Daily learning report | Probabilistic | If LLM is wired in for narrative generation |
| Agent evaluation scoring | Hybrid | Quantitative scores with rule-based accuracy bounds |
| Pattern admission to LTM | Deterministic | Hard thresholds (≥20 obs, ≥0.70 confidence) |
| Memory compression | Deterministic | Fixed staleness and confidence thresholds |

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/harshitsoni97/autotrader.git
cd autotrader
pip install -e .

# 2. Copy and fill in your environment variables
cp .env.example .env
# edit .env with your API keys

# 3. Run in safe dry-run mode (no real orders)
python scripts/run_pre_market.py
```

---

## Installation

**Requirements:** Python 3.11+

```bash
pip install -e .
# or
pip install -r requirements.txt
```

**Key dependencies**

| Package | Purpose |
|---|---|
| `langgraph` | Agent graph orchestration |
| `langchain-anthropic` | Claude LLM integration |
| `yfinance` | Market data (Nifty, stocks) |
| `pydantic` | Config and message validation |
| `structlog` | Structured JSON logging |
| `apscheduler` | Cron-style session scheduling |
| `qdrant-client` | Vector store for semantic memory search |
| `psycopg2-binary` | Postgres for long-term pattern storage |
| `requests` | NSE API scraping |

---

## Configuration

All configuration lives in the `config/` directory. No code changes are needed for normal tuning.

### `config/trading_policy.yaml`

```yaml
trading_policy:
  enabled: true
  dry_run: true                    # true = no real orders; safe default
  total_capital: 1000000           # INR
  max_daily_trades: 3
  max_concurrent_positions: 2
  max_capital_per_trade_pct: 10.0  # % of total capital per trade
  max_daily_loss_pct: 2.0          # halt trading if daily P&L drops below this
  max_weekly_loss_pct: 5.0
  max_monthly_loss_pct: 10.0
  max_risk_per_trade_pct: 0.5      # max % of capital risked per trade
  min_risk_reward: 2.0             # minimum R:R ratio to take a trade
  minimum_score: 80.0              # composite score threshold (0–100)
  minimum_confidence: 0.75         # market regime confidence threshold
  stop_trading_after_losses: 3     # consecutive loss circuit breaker
  allow_reentry_same_stock: false
  allow_overnight_positions: false
  blocked_regimes: []              # e.g. ["bearish", "high_volatility"]
```

### `config/memory_policy.yaml`

```yaml
memory_policy:
  auto_modify_strategy: false      # NEVER change to true — humans review first
  require_review: true
  minimum_observations: 20         # pattern must be seen ≥20 times
  minimum_confidence: 0.70
  short_term_retention_days: 30
  compression_frequency_days: 7
```

### `config/strategy_version.yaml`

```yaml
strategy_version: "1.0.0"
memory_version: "1.0"
config_version: "1"
```

### Environment Variable Overrides

| Variable | Effect |
|---|---|
| `TRADING_ENABLED=false` | Emergency kill switch |
| `DRY_RUN=true` | Force dry-run regardless of YAML |
| `TOTAL_CAPITAL=500000` | Override capital (useful for paper trading) |

---

## LLM Configuration

LLM integration is controlled entirely through `config/llm_config.yaml`. No code changes are needed to swap models.

```yaml
llm:
  # Fast / low-cost — used for catalyst interpretation and regime narrative
  fast_model: "claude-haiku-4-5-20251001"
  fast_max_tokens: 512
  fast_temperature: 0.1

  # Analysis tier — used for scoring review (top opportunity holistic check)
  analysis_model: "claude-sonnet-4-6"
  analysis_max_tokens: 1024
  analysis_temperature: 0.2

  # Report synthesis — used once post-market for daily learning narrative
  # Set report_thinking_budget: 0 to disable extended thinking (faster/cheaper)
  report_model: "claude-sonnet-4-6"
  report_max_tokens: 4096
  report_thinking_budget: 2000

  # Feature flags — disable any to use pure deterministic fallback
  enable_catalyst_llm: true     # Layer 1: catalyst score refinement
  enable_regime_llm: true       # Layer 1: regime narrative enrichment
  enable_scoring_llm: true      # Layer 3: holistic opportunity review
  enable_report_llm: true       # Layer 6: daily learning report narrative
```

### Where LLMs are used and why

| Agent | Model tier | What it does | Fallback if disabled |
|---|---|---|---|
| `CatalystIntelligenceAgent` | Fast (haiku) | Re-scores top 5 catalysts with contextual narrative; adjusts score ±10 max | Deterministic score table unchanged |
| `MarketRegimeAgent` | Fast (haiku) | Adds narrative key factors and trading implication; adjusts confidence ±0.10 max | Quantitative regime score only |
| `OpportunityScoringAgent` | Analysis (sonnet) | Holistic review of top 3 setups; adjusts winner score ±5; can veto if clearly wrong | Deterministic composite score unchanged |
| `DailyLearningAgent` | Report (sonnet + thinking) | Writes executive summary, pattern insights, and tomorrow's recommendations | Template-based boilerplate sections |

### Pydantic enforcement

Every LLM call uses LangChain's `with_structured_output()` bound to a Pydantic model. This means:
- The LLM **cannot** return free-form text that breaks downstream parsing
- Score adjustments are constrained by `ge`/`le` validators (e.g. ±5 max for scoring review)
- If the LLM returns an invalid structure, the call raises an exception and the agent falls back to deterministic logic

### Override models via environment variables

```bash
LLM_FAST_MODEL=claude-haiku-4-5-20251001
LLM_ANALYSIS_MODEL=claude-sonnet-4-6
LLM_REPORT_MODEL=claude-opus-4-8
```

---

## API Keys and External Dependencies

Copy `.env.example` to `.env` and fill in all values before running live.

```bash
# --- LLM (required for narrative report generation) ---
ANTHROPIC_API_KEY=sk-ant-...

# --- LLMOps tracing (optional; see LLMOps section) ---
# LangSmith — set when llmops.backend = "langsmith"
LANGCHAIN_API_KEY=ls__...
# MLflow needs no key; point llmops.mlflow_tracking_uri at your server

# --- Broker API (required for live trading) ---
# Select the provider in config/broker_config.yaml (broker.provider) or via:
# BROKER_PROVIDER=mock | zerodha | upstox
#
# Zerodha (Kite Connect v3):
KITE_API_KEY=your_kite_api_key
KITE_ACCESS_TOKEN=your_kite_access_token
#
# Upstox (API v2):
UPSTOX_ACCESS_TOKEN=your_upstox_access_token

# --- Database (optional; for production LTM persistence) ---
DATABASE_URL=postgresql://user:password@localhost:5432/autotrader

# --- Vector Store (optional; for semantic memory search) ---
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_key

# --- Market Data ---
# yfinance is free; NSE data is scraped from the public NSE website
# No key needed for either by default
```

**What each dependency is used for:**

| Dependency | Used for | Required for dry-run? |
|---|---|---|
| Anthropic API | LLM enrichment + narrative in reports | No |
| LangSmith / MLflow | LLM call tracing & prompt versioning | No (tracing off by default) |
| Zerodha Kite / Upstox | Live order placement (see Broker Connectors) | No (MockBroker used) |
| yfinance | Nifty, VIX, stock OHLCV data | Yes (falls back to mock data) |
| NSE website | FII/DII, bulk deals, corporate actions, ASM/GSM | Yes (falls back to mock data) |
| PostgreSQL | Production long-term memory persistence | No (in-memory store used) |
| Qdrant | Semantic search over short-term memory | No (in-memory store used) |

> **Network not available?** Every data tool has a deterministic mock fallback. The graph will run end-to-end with synthetic data — useful for testing and CI environments.

---

## Broker Connectors

The platform is broker-agnostic: every connector implements `BrokerInterface` in `src/autotrader/tools/broker_tools.py`. Select the active broker in `config/broker_config.yaml` (or via `BROKER_PROVIDER`):

| Provider | Class | API | Credentials (env) |
|---|---|---|---|
| `mock` | `MockBroker` | none — simulated fills | none |
| `zerodha` | `ZerodhaBroker` | [Kite Connect v3](https://kite.trade/docs/connect/v3/) | `KITE_API_KEY`, `KITE_ACCESS_TOKEN` |
| `upstox` | `UpstoxBroker` | [Upstox API v2](https://upstox.com/developer/api-documentation/) | `UPSTOX_ACCESS_TOKEN` |

```yaml
# config/broker_config.yaml
broker:
  provider: "zerodha"     # mock | zerodha | upstox
  exchange: "NSE"
  product: "MIS"          # intraday; mapped per-broker (Upstox: MIS->I)
  timeout_seconds: 10.0
  max_retries: 3
  circuit_breaker_threshold: 5
  circuit_breaker_cooldown_seconds: 60.0
```

**Production-grade behaviour built into every live connector:**
- **Resilient HTTP** — per-call timeout, exponential-backoff retry, and a circuit breaker that trips after N consecutive failures and short-circuits during a cooldown window.
- **Fail-closed auth** — a missing API key raises `BrokerAuthError` at construction; live trading never silently degrades to mock.
- **Schema validation** — all order/quote responses are normalised through Pydantic models (`Order`, `Quote`), so a changed upstream field is caught rather than silently mis-read.
- **Idempotency** — the execution agent sends a deterministic `tag` per trade intent; a repeated run is deduped (both in `state["orders"]` and via the broker tag), preventing duplicate live orders. The monitoring agent records `exit_order_id` per position so a stop/target exit is never sent twice.

> **Upstox note:** Upstox addresses instruments by `instrument_key` (e.g. `NSE_EQ|INE009A01021`). Provide a `{SYMBOL: instrument_key}` map at `config/upstox_instruments.json` (path configurable via `broker.upstox_instrument_map`).

---

## Running the Platform

### Manual execution

```bash
# Pre-market session (run before 09:15 IST)
python scripts/run_pre_market.py

# Intraday monitoring (run between 09:15 and 15:30 IST)
python scripts/run_intraday.py

# Post-market learning (run after 15:30 IST)
python scripts/run_post_market.py
```

### Scheduled execution (APScheduler)

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

IST = pytz.timezone("Asia/Kolkata")
scheduler = BlockingScheduler(timezone=IST)

scheduler.add_job(run_pre_market,  CronTrigger(hour=8,  minute=0,  timezone=IST))
scheduler.add_job(run_intraday,    CronTrigger(hour=9,  minute=15, timezone=IST))
scheduler.add_job(run_post_market, CronTrigger(hour=15, minute=45, timezone=IST))

scheduler.start()
```

### Safety checks before each session

The scripts automatically run `SafetyControls.run_all_checks()` which verifies:
- Kill switch not activated
- Not a market holiday
- Daily/weekly loss limits not breached
- Market hours appropriate for the session type

---

## Dry-Run Mode

Dry-run is **enabled by default** (`dry_run: true` in `trading_policy.yaml`). In this mode:

1. The pre-market graph runs the full pipeline and selects the best trade
2. The execution agent records an **assumed fill** at the exact plan entry price (zero slippage)
3. Order ID is prefixed `DRY-` (e.g., `DRY-REL-000`)
4. The intraday monitoring graph tracks the assumed position against live prices
5. The post-market report compares the assumed entry/exit vs. what prices actually did

This lets you validate signal quality and strategy behaviour before risking real capital.

To enable live trading:
```yaml
# config/trading_policy.yaml
trading_policy:
  dry_run: false
```
Or set `DRY_RUN=false` in your environment.

---

## Output and Reports

All reports are saved to the `reports/` directory.

| Report | Filename | Generated by |
|---|---|---|
| Daily trade report | `YYYY-MM-DD_daily_report.md` | `DailyLearningAgent` |
| Agent diagnostic | `YYYY-MM-DD_agent_diagnostic.md` | `AgentEvaluatorAgent` |
| A2A learning | `YYYY-MM-DD_a2a_learning.md` | `LongTermMemoryAgent` |
| Audit trail | `YYYY-MM-DD_audit_trail.md` | `run_post_market.py` |
| Governance report | `YYYY-MM-DD_governance.md` | `run_post_market.py` |

The daily report includes:
- Session summary (regime, top sectors, candidates analysed)
- Trade outcomes with P&L, R-multiple achieved, and slippage
- Agent performance scores
- Market context that influenced the session
- Patterns observed today
- Proposals for human review (never auto-applied)
- Next-session watchlist

---

## Memory System

### Short-Term Memory (`memory/short_term.py`)

Rolling 30-day in-memory store (TTL-based). Used to hold intraday signals, candidate lists, and session context between graph runs.

```python
from autotrader.memory.short_term import ShortTermMemory

mem = ShortTermMemory(retention_days=30)
mem.store("session_2026-06-16", {"regime": "bullish", "top": "RELIANCE"})
data = mem.retrieve("session_2026-06-16")
```

In production, replace the in-memory dict with a Qdrant or Weaviate client for semantic search capability.

### Long-Term Memory (`memory/long_term.py`)

Singleton store for validated trading patterns with win-rate tracking. Patterns are admitted only after meeting strict criteria:
- ≥ 20 observations
- ≥ 0.70 confidence
- Positive expectancy

```python
from autotrader.memory.long_term import LongTermMemory

ltm = LongTermMemory()
mid = ltm.store_pattern(
    pattern="high_volume_sector_leader",
    observations=25,
    win_rate=0.72,
    confidence=0.75,
)
ltm.update_pattern(mid, new_observation=True, win=True)
stats = ltm.get_stats()  # {"total_patterns": 1, "avg_win_rate": ..., "avg_confidence": ...}
```

In production, replace the in-memory dict with a Postgres table (with pgvector) or Qdrant collection.

**Memory lifecycle operations:**
- `expire_stale()` — removes patterns with <50% confidence not updated in 90 days
- `merge_duplicates()` — consolidates entries with the same pattern key
- `boost_high_performers()` — adds +0.02 confidence to patterns with win rate ≥ 65%

---

## Safety Controls and Governance

### Kill Switch
Set `TRADING_ENABLED=false` to halt all trading immediately. The safety check runs before every session.

### Holiday Calendar
`SafetyControls` maintains an embedded NSE holiday list (2024–2025). The system will not run pre-market or intraday sessions on market holidays.

### Governance Gate
The `GovernanceAgent` runs nine checks in sequence. The first failure immediately routes the LangGraph flow to `END` — no further agents execute, no trade is taken. All governance decisions are logged to the audit trail.

### Risk Gate
After governance approval, the `RiskAgent` independently validates:
- Average 20-day volume > 500,000 shares
- Bid-ask spread < 0.2%
- Symbol not on NSE ASM/GSM surveillance list
- No corporate action ex-date today
- ATR and price are non-zero
- Risk/reward ≥ 2.0
- Stock has not gapped up > 3% (gap risk)

### Audit Trail
Every agent appends an `audit_entry` to the state's `audit_trail` list. The trail is written to a timestamped markdown file after each post-market session.

---

## LLMOps — Tracing & Prompt Repository

AutoTrader ships with built-in support for **LangSmith** and **MLflow** to trace every LLM call, version prompt templates, and compare runs.

### Prompt Repository

All LLM prompt templates are centralised in `config/prompts.yaml`. Each entry has a version field so changes are tracked. The registry is loaded at startup by `src/autotrader/core/prompts.py` and served via `get_prompt(name, **kwargs)`.

```yaml
# config/prompts.yaml (excerpt)
prompts:
  catalyst_enrichment:
    version: "v1"
    template: |
      Market regime today: {market_regime}.
      NSE catalyst: symbol={symbol}, ...
```

Edit prompt wording without touching agent code, then bump `version` to distinguish runs in your tracing backend.

### Enabling LangSmith Tracing

LangChain auto-traces all LLM calls when `LANGCHAIN_TRACING_V2=true` is set. AutoTrader sets this automatically when the backend is configured:

```yaml
# config/llm_config.yaml
llmops:
  backend: "langsmith"
  project_name: "autotrader"
```

```bash
export LANGCHAIN_API_KEY="ls__..."   # or LANGSMITH_API_KEY
python scripts/run_pre_market.py     # traces appear in LangSmith UI
```

Every run is tagged with the project name. Prompt versions are captured in trace metadata for comparison.

### Enabling MLflow Tracing

```yaml
# config/llm_config.yaml
llmops:
  backend: "mlflow"
  project_name: "autotrader"
  mlflow_tracking_uri: "http://localhost:5000"
```

```bash
mlflow server --host 0.0.0.0 --port 5000 &
python scripts/run_pre_market.py
```

MLflow LangChain autolog captures model name, prompt, response, latency, and token counts per agent call.

### Disabling Tracing

```yaml
llmops:
  backend: "none"   # default — no external calls
```

Tracing is always best-effort: a missing API key or unreachable server is logged at INFO level and the run continues normally.

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --tb=short
```

The test suite covers:
- Config loading (Pydantic validation, YAML overrides)
- A2A message creation and structure
- State initialisation
- Governance agent (approval and four rejection scenarios)
- Risk agent (pass case and low-volume failure)
- Opportunity scoring with composite weight verification
- Safety kill switch
- Short-term memory (store/retrieve/expire/search)
- Long-term memory (store/update/get_stats/merge/boost)

All tests use mock/patch for external API calls so they run fully offline.

---

## Project Structure

```
autotrader/
├── config/
│   ├── trading_policy.yaml       # All trading constraints and limits
│   ├── memory_policy.yaml        # Pattern admission and compression settings
│   ├── strategy_version.yaml     # Versioning for strategy and memory schemas
│   ├── llm_config.yaml           # Model tiers, feature flags, llmops backend
│   ├── prompts.yaml              # Versioned prompt repository
│   ├── broker_config.yaml        # Broker provider + resilience settings
│   └── upstox_instruments.json   # Symbol -> Upstox instrument_key map
│
├── src/autotrader/
│   ├── core/
│   │   ├── config.py             # Pydantic config models + load_config()
│   │   ├── state.py              # TradingState TypedDict + create_initial_state()
│   │   ├── messages.py           # A2AMessage model + create_message() + audit_entry()
│   │   ├── llm.py                # LLM factories + Pydantic output schemas
│   │   ├── prompts.py            # Prompt registry (get_prompt)
│   │   └── tracing.py            # LangSmith/MLflow setup_tracing()
│   │
│   ├── agents/
│   │   ├── layer1/               # Market Intelligence
│   │   │   ├── market_regime.py
│   │   │   ├── sector_rotation.py
│   │   │   └── catalyst_intelligence.py
│   │   ├── layer2/               # Opportunity Discovery
│   │   │   ├── relative_strength.py
│   │   │   ├── volume_intelligence.py
│   │   │   └── technical_structure.py
│   │   ├── layer3/               # Decision Engine
│   │   │   └── opportunity_scoring.py
│   │   ├── layer4/               # Governance & Risk
│   │   │   ├── governance.py
│   │   │   └── risk.py
│   │   ├── layer5/               # Execution
│   │   │   ├── trade_construction.py
│   │   │   ├── execution.py
│   │   │   └── monitoring.py
│   │   └── layer6/               # Learning & Memory
│   │       ├── daily_learning.py
│   │       ├── agent_evaluator.py
│   │       ├── long_term_memory.py
│   │       └── memory_compression.py
│   │
│   ├── graphs/
│   │   ├── pre_market.py         # 11-node pre-market LangGraph
│   │   ├── intraday.py           # 3-node intraday monitoring graph
│   │   └── post_market.py        # 4-node post-market learning graph
│   │
│   ├── memory/
│   │   ├── short_term.py         # 30-day rolling TTL store
│   │   └── long_term.py          # Singleton validated-pattern store
│   │
│   ├── tools/
│   │   ├── market_data.py        # yfinance wrappers + mock fallbacks
│   │   ├── nse_tools.py          # NSE scraping (FII/DII, bulk deals, ASM/GSM)
│   │   └── broker_tools.py       # BrokerInterface + Mock/Zerodha/Upstox + get_broker()
│   │
│   ├── safety/
│   │   └── controls.py           # Kill switch, holiday check, limit checks
│   │
│   └── reports/
│       └── generators.py         # Markdown report generators
│
├── scripts/
│   ├── run_pre_market.py
│   ├── run_intraday.py
│   └── run_post_market.py
│
├── tests/
│   └── test_agents.py            # 13 offline unit tests
│
└── reports/                      # Generated daily reports (git-ignored)
```

---

## Extending the Platform

### Adding a new agent
1. Create the agent function in the appropriate layer directory: `def my_agent(state: TradingState) -> dict[str, Any]`
2. Return only the state keys you want to update — LangGraph merges partial state returns
3. For list fields (`messages`, `audit_trail`, `errors`), return a list; the reducer appends it
4. Wire the node into the relevant graph in `graphs/`

### Connecting a real broker
Implement the `BrokerInterface` ABC in `tools/broker_tools.py`:
```python
class KiteBroker(BrokerInterface):
    def place_order(self, symbol, qty, order_type, price=None) -> dict: ...
    def get_positions(self) -> list[dict]: ...
    # ... all abstract methods
```
Then instantiate it in `agents/layer5/execution.py` instead of `MockBroker`.

### Connecting a real vector store
Replace the `_store` dict in `memory/short_term.py` with a Qdrant client:
```python
from qdrant_client import QdrantClient
self._client = QdrantClient(url=os.getenv("QDRANT_URL"))
```

---

## Disclaimer

This platform is for educational and research purposes. Intraday trading in equities involves significant financial risk. Past signal accuracy does not guarantee future profitability. Always paper-trade (dry-run) for an extended period before risking real capital. The authors are not responsible for financial losses incurred through use of this software.
