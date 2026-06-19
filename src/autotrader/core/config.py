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


class MemoryBackendConfig(BaseModel):
    # provider: memory | postgres  (postgres+pgvector keeps state AND vectors in ONE store)
    provider: str = "memory"
    dsn_env: str = "DATABASE_URL"          # env var holding the Postgres DSN
    # Embedding provider — pick the one native to your chosen cloud ecosystem.
    # local (no deps) | voyage | bedrock | vertex | azure_openai
    embedding_provider: str = "local"
    embedding_model: str = "local-hash"
    embedding_dim: int = 256
    # FinMem / Generative-Agents retrieval scoring: recency × relevance × importance
    recency_weight: float = 0.34
    relevance_weight: float = 0.33
    importance_weight: float = 0.33
    recency_half_life_days: float = 30.0
    top_k: int = 5


class MemoryPolicy(BaseModel):
    auto_modify_strategy: bool = False
    require_review: bool = True
    minimum_observations: int = 20
    minimum_confidence: float = 0.70
    short_term_retention_days: int = 30
    compression_frequency_days: int = 7
    backend: MemoryBackendConfig = Field(default_factory=MemoryBackendConfig)


class StrategyVersion(BaseModel):
    strategy_version: str = "1.0.0"
    memory_version: str = "1.0"
    config_version: str = "1"


class LLMOpsConfig(BaseModel):
    backend: str = "none"            # langsmith | mlflow | none
    project_name: str = "autotrader"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    mlflow_tracking_uri: str = "http://localhost:5000"
    push_prompts_to_hub: bool = False
    tags: List[str] = Field(default_factory=lambda: ["nse", "intraday"])


class BrokerConfig(BaseModel):
    # provider: mock | zerodha | upstox
    provider: str = "mock"
    exchange: str = "NSE"            # NSE | BSE
    product: str = "MIS"             # zerodha: MIS|CNC|NRML ; upstox maps MIS->I, CNC/NRML->D
    variety: str = "regular"         # zerodha order variety
    slippage_bps: float = 5.0        # mock broker only
    timeout_seconds: float = 10.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_seconds: float = 60.0
    # Path (relative to config/) of a JSON map: {SYMBOL: instrument_key} — required for Upstox
    upstox_instrument_map: str = "upstox_instruments.json"


class NotificationConfig(BaseModel):
    """Outbound notifications for trades, exits, daily summaries and errors.

    Credentials (bot tokens, SMTP passwords, Twilio auth) come ONLY from
    environment variables — never from this config or any committed file.
    Each channel degrades gracefully: an unconfigured or failing channel logs
    a warning and never raises into the trading path.
    """
    enabled: bool = False
    # Which channels to fan out to: telegram | email | whatsapp | slack
    channels: List[str] = Field(default_factory=list)

    # Event toggles
    notify_on_order: bool = True       # entry order placed (dry-run and live)
    notify_on_exit: bool = True        # stop / target exits
    notify_on_daily_summary: bool = True
    notify_on_error: bool = True

    # Telegram — env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    # Email (SMTP) — env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
    # WhatsApp/SMS (Twilio) — env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, TWILIO_TO
    # Slack — env: SLACK_WEBHOOK_URL
    timeout_seconds: float = 10.0


class LLMConfig(BaseModel):
    # Provider per tier — each tier can use a different vendor.
    # Supported: anthropic | openai | google | mistral | groq | ollama | azure_openai
    fast_provider: str = "anthropic"
    fast_model: str = "claude-haiku-4-5-20251001"
    fast_max_tokens: int = 512
    fast_temperature: float = 0.1

    analysis_provider: str = "anthropic"
    analysis_model: str = "claude-sonnet-4-6"
    analysis_max_tokens: int = 1024
    analysis_temperature: float = 0.2

    report_provider: str = "anthropic"
    report_model: str = "claude-sonnet-4-6"
    report_max_tokens: int = 4096
    # Thinking/reasoning config — provider-specific:
    #   anthropic  → report_thinking_budget > 0 enables extended thinking (budget_tokens)
    #   google     → report_thinking_budget > 0 sets Gemini thinking_budget (tokens)
    #   openai     → report_reasoning_effort sets reasoning depth for GPT-5.x models
    #                correct API format: reasoning={"effort": effort}
    #                levels: "" (off) | none | minimal | low | medium | high | xhigh
    #   openai_o   → same effort levels; temperature is rejected by o-series API
    #   all others → both fields ignored
    report_thinking_budget: int = 2000
    # OpenAI reasoning effort levels (none/minimal/low/medium/high/xhigh):
    #   none/minimal → latency-critical tasks (voice, classification)
    #   low          → tool-use, planning, multi-step — fast + cheap
    #   medium       → default; quality + reliability for agentic tasks
    #   high         → complex reasoning, deep planning — our trade veto gate
    #   xhigh        → deep research, async workflows — rarely needed here
    report_reasoning_effort: str = "high"  # openai / openai_o: high suits trade veto

    # Feature flags — disable individually to fall back to deterministic logic
    enable_catalyst_llm: bool = True
    enable_regime_llm: bool = True
    enable_scoring_llm: bool = True
    enable_report_llm: bool = True


class StackConfig(BaseModel):
    """A complete provider stack for compete mode — fast + analysis + report tiers."""
    name: str                               # Display label, e.g. "Anthropic"
    # Fast tier — catalyst enrichment
    fast_provider: str
    fast_model: str
    fast_temperature: float = 0.1
    fast_max_tokens: int = 512
    # Analysis tier — regime enrichment + scoring review
    analysis_provider: str
    analysis_model: str
    analysis_temperature: float = 0.2
    analysis_max_tokens: int = 1024
    # Report tier — end-of-day learning report (optional; falls back to fast if empty)
    report_provider: str = ""
    report_model: str = ""
    report_max_tokens: int = 4096
    report_thinking_budget: int = 0         # anthropic / google: tokens; 0 = disabled
    report_reasoning_effort: str = ""       # openai / openai_o: low|medium|high|xhigh


class CompeteModeConfig(BaseModel):
    """Compete mode: run full provider stacks side-by-side and rank by end-of-day PnL."""
    enabled: bool = False
    dry_run: bool = True                    # True → no real orders for any stack
    primary: str = ""                       # stack name that drives real execution (actual mode only)
    stacks: List[StackConfig] = Field(default_factory=list)


class UniverseConfig(BaseModel):
    index: str = "nifty500"
    max_from_index: int = 100
    include_corporate_events: bool = True
    include_block_deals: bool = True
    include_chartink: bool = False
    include_preopen: bool = False
    max_total: int = 80


class PlatformConfig(BaseModel):
    trading_policy: TradingPolicy = Field(default_factory=TradingPolicy)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    strategy_version: StrategyVersion = Field(default_factory=StrategyVersion)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    llmops: LLMOpsConfig = Field(default_factory=LLMOpsConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    compete: CompeteModeConfig = Field(default_factory=CompeteModeConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)


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
    llm_cfg_file = _load_yaml(root / "llm_config.yaml")
    llm_data = llm_cfg_file.get("llm", {})
    llmops_data = llm_cfg_file.get("llmops", {})
    compete_data = llm_cfg_file.get("compete", {})
    broker_data = _load_yaml(root / "broker_config.yaml").get("broker", {})
    notif_data = _load_yaml(root / "notifications.yaml").get("notifications", {})

    # Environment variable overrides for key settings
    if os.getenv("TRADING_ENABLED") is not None:
        tp_data["enabled"] = os.getenv("TRADING_ENABLED", "true").lower() == "true"
    if os.getenv("TOTAL_CAPITAL"):
        tp_data["total_capital"] = float(os.getenv("TOTAL_CAPITAL"))
    if os.getenv("DRY_RUN") is not None:
        tp_data["dry_run"] = os.getenv("DRY_RUN", "true").lower() == "true"
    # LLM env overrides
    if os.getenv("LLM_FAST_MODEL"):
        llm_data["fast_model"] = os.getenv("LLM_FAST_MODEL")
    if os.getenv("LLM_ANALYSIS_MODEL"):
        llm_data["analysis_model"] = os.getenv("LLM_ANALYSIS_MODEL")
    if os.getenv("LLM_REPORT_MODEL"):
        llm_data["report_model"] = os.getenv("LLM_REPORT_MODEL")
    # Broker env override
    if os.getenv("BROKER_PROVIDER"):
        broker_data["provider"] = os.getenv("BROKER_PROVIDER")
    # Notification env overrides
    if os.getenv("NOTIFICATIONS_ENABLED") is not None:
        notif_data["enabled"] = os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true"
    if os.getenv("NOTIFICATION_CHANNELS"):
        notif_data["channels"] = [c.strip() for c in os.getenv("NOTIFICATION_CHANNELS").split(",") if c.strip()]

    # Parse stacks list inside compete section
    compete_stacks = [
        StackConfig(**s) for s in compete_data.pop("stacks", [])
    ]
    compete_cfg = CompeteModeConfig(**compete_data, stacks=compete_stacks)

    universe_data = _load_yaml(root / "universe.yaml").get("universe", {})

    return PlatformConfig(
        trading_policy=TradingPolicy(**tp_data),
        memory_policy=MemoryPolicy(**mp_data),
        strategy_version=StrategyVersion(**sv_data),
        llm=LLMConfig(**llm_data),
        llmops=LLMOpsConfig(**llmops_data),
        broker=BrokerConfig(**broker_data),
        notifications=NotificationConfig(**notif_data),
        compete=compete_cfg,
        universe=UniverseConfig(**universe_data),
    )
