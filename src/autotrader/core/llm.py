"""LLM factory and Pydantic output schemas for structured agent enrichment.

All public functions return a LangChain chat model bound to a Pydantic schema
via `with_structured_output()`. The provider is selected per tier from
llm_config.yaml — any tier can use a different vendor with no code changes.

Supported providers (set via fast_provider / analysis_provider / report_provider):
  anthropic    — ChatAnthropic            — env: ANTHROPIC_API_KEY
  openai       — ChatOpenAI (Responses API) — env: OPENAI_API_KEY
  openai_o     — ChatOpenAI (o-series)   — env: OPENAI_API_KEY  (o1/o3/o4-mini — no temperature)
  google       — ChatGoogleGenerativeAI  — env: GOOGLE_API_KEY
  mistral      — ChatMistralAI           — env: MISTRAL_API_KEY
  groq         — ChatGroq                — env: GROQ_API_KEY
  ollama       — ChatOllama              — no key (local server at OLLAMA_BASE_URL)
  azure_openai — AzureChatOpenAI         — env: AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT

Thinking / reasoning support per provider:
  anthropic  — report_thinking_budget > 0 enables extended thinking (budget_tokens)
  openai_o   — reasoning built-in; report_reasoning_effort = low|medium|high
  google     — thinking automatic on gemini-2.5-* models; report_thinking_budget maps
               to thinking_budget parameter in ChatGoogleGenerativeAI

Every call site wraps invocations in try/except so deterministic logic always
serves as a safe fallback when a provider is unavailable or misconfigured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output schemas — LLM MUST return these shapes (enforced by
# with_structured_output). A2A messages carry enrichment as `llm_enrichment`.
# ---------------------------------------------------------------------------

class CatalystEnrichment(BaseModel):
    """LLM assessment of a catalyst's market significance."""
    symbol: str = Field(description="NSE ticker symbol")
    adjusted_score: float = Field(
        ge=0, le=100,
        description="Revised catalyst score (0-100). Stay within ±10 of the deterministic base score unless signal is clearly stronger/weaker.",
    )
    impact: str = Field(
        description="One of: 'high', 'medium', 'low'",
        pattern="^(high|medium|low)$",
    )
    narrative: str = Field(
        max_length=200,
        description="1-2 sentence plain-English explanation of why this catalyst matters today.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="LLM confidence in this assessment (0-1).",
    )


class RegimeEnrichment(BaseModel):
    """LLM synthesis of current market regime context."""
    regime_label: str = Field(
        description="Confirmed or refined regime label (e.g. 'bullish', 'risk_off', 'range_bound').",
    )
    adjusted_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Refined confidence in the regime (0-1). Stay within ±0.10 of the quantitative score.",
    )
    key_factors: list[str] = Field(
        max_length=5,
        description="Up to 3 bullet-point factors driving the regime today.",
    )
    trading_implication: str = Field(
        max_length=200,
        description="Single sentence: what this regime means for intraday momentum trades.",
    )


class ScoringReview(BaseModel):
    """LLM holistic review of top scored opportunities."""
    top_symbol: str = Field(description="Symbol LLM agrees is the strongest setup today.")
    score_adjustment: float = Field(
        ge=-5.0, le=5.0,
        description="Points to add/subtract from the deterministic composite score. Range ±5 only.",
    )
    rationale: str = Field(
        max_length=300,
        description="Why this symbol is the best setup — mention regime, sector, and technicals.",
    )
    concerns: list[str] = Field(
        description="Up to 2 risk concerns for this trade. Empty list if none.",
    )
    pass_review: bool = Field(
        description="False if LLM believes the setup should NOT proceed despite clearing deterministic gates.",
    )


class ReportInsights(BaseModel):
    """LLM-generated narrative sections for the daily learning report."""
    executive_summary: str = Field(
        max_length=500,
        description="3-4 sentence summary of today's session: regime, what happened, and key outcome.",
    )
    pattern_insights: list[str] = Field(
        description="2-4 bullet observations about which patterns/setups worked or failed today.",
    )
    recommendations: list[str] = Field(
        description="2-3 concrete, actionable items for tomorrow's session (no strategy-code changes).",
    )


# ---------------------------------------------------------------------------
# Provider registry — maps provider name → (env_var_check, factory_fn)
# ---------------------------------------------------------------------------

# Each entry: (env_var_to_check_for_availability, lazy_factory)
# Factories receive (model, temperature, max_tokens, **extra) and return a
# LangChain BaseChatModel — all implement .invoke() and .with_structured_output()
# identically, so the rest of the codebase is provider-agnostic.

def _make_anthropic(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, temperature=temperature, max_tokens=max_tokens, **kw)


def _make_openai(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    """OpenAI GPT-5.x reasoning models via the Responses API.

    All GPT-5.x (gpt-5.4-mini, gpt-5.4, gpt-5.5, gpt-5.5-pro) are reasoning
    models. Reasoning is controlled via reasoning={"effort": ...} in the request
    body. Supported effort levels: none | minimal | low | medium | high | xhigh.
    Empty string disables reasoning (no reasoning tokens).

    use_responses_api=True routes ChatOpenAI through OpenAI's Responses API
    instead of Chat Completions — recommended for reasoning models (interleaved
    thinking, better intelligence, streaming support).

    o-series models (o1/o3/o4-mini) use openai_o provider — they reject temperature.
    """
    from langchain_openai import ChatOpenAI
    effort = kw.pop("reasoning_effort", "")
    extra_body: dict[str, Any] = {}
    if effort:
        extra_body["reasoning"] = {"effort": effort}
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        use_responses_api=True,
        extra_body=extra_body or None,  # type: ignore[arg-type]
        **kw,
    )


def _make_openai_o(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    """OpenAI o-series (o1/o3/o4-mini) — rejects temperature, reasoning built-in."""
    from langchain_openai import ChatOpenAI
    kw.pop("temperature", None)
    effort = kw.pop("reasoning_effort", "medium")
    return ChatOpenAI(
        model=model,
        max_completion_tokens=max_tokens,
        extra_body={"reasoning": {"effort": effort}},
        **kw,
    )


def _make_google(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    """Google Gemini. Gemini 2.5 models support thinking via thinking_budget param."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    thinking_budget = kw.pop("thinking_budget", None)
    init_kw: dict[str, Any] = dict(model=model, temperature=temperature, max_output_tokens=max_tokens, **kw)
    if thinking_budget is not None:
        # Gemini 2.5: thinking_config controls how many tokens the model may think.
        # Pass 0 to disable, positive int to enable.
        init_kw["thinking_config"] = {"thinking_budget": thinking_budget}
    return ChatGoogleGenerativeAI(**init_kw)


def _make_mistral(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    from langchain_mistralai import ChatMistralAI
    return ChatMistralAI(model=model, temperature=temperature, max_tokens=max_tokens, **kw)


def _make_groq(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    from langchain_groq import ChatGroq
    return ChatGroq(model=model, temperature=temperature, max_tokens=max_tokens, **kw)


def _make_ollama(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    from langchain_ollama import ChatOllama
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    return ChatOllama(model=model, temperature=temperature, num_predict=max_tokens, base_url=base_url, **kw)


def _make_azure_openai(model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    from langchain_openai import AzureChatOpenAI
    return AzureChatOpenAI(
        azure_deployment=model,
        temperature=temperature,
        max_tokens=max_tokens,
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        **kw,
    )


# provider_name → (required_env_var | None, factory_fn)
# None env var = always available (e.g. Ollama runs locally)
_PROVIDERS: dict[str, tuple[str | None, Any]] = {
    "anthropic":    ("ANTHROPIC_API_KEY",    _make_anthropic),
    "openai":       ("OPENAI_API_KEY",       _make_openai),
    "openai_o":     ("OPENAI_API_KEY",       _make_openai_o),   # o1/o3/o4-mini
    "google":       ("GOOGLE_API_KEY",       _make_google),
    "mistral":      ("MISTRAL_API_KEY",      _make_mistral),
    "groq":         ("GROQ_API_KEY",         _make_groq),
    "ollama":       (None,                   _make_ollama),
    "azure_openai": ("AZURE_OPENAI_API_KEY", _make_azure_openai),
}


def _is_available(provider: str) -> bool:
    entry = _PROVIDERS.get(provider)
    if entry is None:
        logger.warning("Unknown LLM provider: %s", provider)
        return False
    env_var, _ = entry
    if env_var is None:
        return True  # local provider (Ollama)
    return bool(os.getenv(env_var))


def _make_llm(provider: str, model: str, temperature: float, max_tokens: int, **kw: Any) -> Any:
    entry = _PROVIDERS.get(provider)
    if entry is None:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Supported: {list(_PROVIDERS)}")
    _, factory = entry
    return factory(model, temperature, max_tokens, **kw)


# ---------------------------------------------------------------------------
# Public factory helpers — used by agents
# ---------------------------------------------------------------------------

def get_fast_llm(cfg: Any) -> Any | None:
    """Return a fast/cheap LLM instance, or None if unavailable."""
    provider = getattr(cfg, "fast_provider", "anthropic")
    if not _is_available(provider):
        return None
    try:
        return _make_llm(provider, cfg.fast_model, cfg.fast_temperature, cfg.fast_max_tokens)
    except Exception as exc:
        logger.warning("Could not initialise fast LLM (%s): %s", provider, exc)
        return None


def get_analysis_llm(cfg: Any) -> Any | None:
    """Return the analysis-tier LLM instance, or None if unavailable."""
    provider = getattr(cfg, "analysis_provider", "anthropic")
    if not _is_available(provider):
        return None
    try:
        return _make_llm(provider, cfg.analysis_model, cfg.analysis_temperature, cfg.analysis_max_tokens)
    except Exception as exc:
        logger.warning("Could not initialise analysis LLM (%s): %s", provider, exc)
        return None


def get_report_llm(cfg: Any) -> Any | None:
    """Return the report-generation LLM with thinking/reasoning where supported.

    Reasoning support per provider:
      anthropic  — report_thinking_budget > 0 enables extended thinking (budget_tokens)
      openai     — report_reasoning_effort = low|medium|high adds reasoning tokens
                   (gpt-5.4-mini/5.4/5.5 all support reasoning; temperature still accepted)
      openai_o   — report_reasoning_effort sets depth; temperature rejected by API
      google     — report_thinking_budget > 0 sets Gemini thinking_budget (tokens)
    """
    provider = getattr(cfg, "report_provider", "anthropic")
    if not _is_available(provider):
        return None
    try:
        budget = getattr(cfg, "report_thinking_budget", 0)
        effort = getattr(cfg, "report_reasoning_effort", "")
        extra: dict[str, Any] = {}

        if provider == "anthropic":
            if budget > 0:
                extra["thinking"] = {"type": "adaptive"}
            extra["temperature"] = 0.3

        elif provider == "openai":
            # GPT-5.x: supports reasoning_effort alongside temperature.
            # Empty string disables reasoning (standard completion).
            extra["temperature"] = 0.3
            if effort:
                extra["reasoning_effort"] = effort

        elif provider == "openai_o":
            # o-series: reasoning built-in, temperature rejected by API.
            extra["reasoning_effort"] = effort or "medium"

        elif provider == "google":
            if budget > 0:
                extra["thinking_budget"] = budget
            extra["temperature"] = 0.3

        else:
            extra["temperature"] = 0.3

        # Pull temperature out of extra (if set) to pass as positional arg
        temperature = extra.pop("temperature", 0.3)
        return _make_llm(provider, cfg.report_model, temperature, cfg.report_max_tokens, **extra)
    except Exception as exc:
        logger.warning("Could not initialise report LLM (%s): %s", provider, exc)
        return None


def make_stack_llms(stack: Any) -> tuple[Any | None, Any | None]:
    """Build (fast_llm, analysis_llm) for a compete stack.

    Returns (None, None) when API keys are missing so the coordinator
    can record the stack as unavailable without crashing.
    """
    fast_llm = None
    analysis_llm = None

    if _is_available(stack.fast_provider):
        try:
            fast_llm = _make_llm(stack.fast_provider, stack.fast_model, stack.fast_temperature, stack.fast_max_tokens)
        except Exception as exc:
            logger.warning("Stack %r fast LLM (%s/%s) failed: %s", stack.name, stack.fast_provider, stack.fast_model, exc)

    if _is_available(stack.analysis_provider):
        try:
            analysis_llm = _make_llm(stack.analysis_provider, stack.analysis_model, stack.analysis_temperature, stack.analysis_max_tokens)
        except Exception as exc:
            logger.warning("Stack %r analysis LLM (%s/%s) failed: %s", stack.name, stack.analysis_provider, stack.analysis_model, exc)

    return fast_llm, analysis_llm


def make_competitor_llm(competitor: Any) -> Any | None:
    """Build an LLM instance from a CompetitorConfig for compete mode.

    Handles provider-specific reasoning/thinking config so each competitor
    can use extended thinking or reasoning effort independently.
    """
    provider = competitor.provider
    if not _is_available(provider):
        logger.warning("Competitor %r provider %r unavailable (no API key)", competitor.name, provider)
        return None
    try:
        extra: dict[str, Any] = {}
        if competitor.reasoning_effort:
            extra["reasoning_effort"] = competitor.reasoning_effort
        if competitor.thinking_budget > 0:
            if provider == "anthropic":
                extra["thinking"] = {"type": "enabled", "budget_tokens": competitor.thinking_budget}
                extra["temperature"] = 1.0   # required for extended thinking
            elif provider == "google":
                extra["thinking_budget"] = competitor.thinking_budget
        return _make_llm(provider, competitor.model, competitor.temperature, competitor.max_tokens, **extra)
    except Exception as exc:
        logger.warning("Could not initialise competitor LLM %r (%s/%s): %s", competitor.name, provider, competitor.model, exc)
        return None


def structured(llm: Any, schema: type[BaseModel]) -> Any:
    """Bind a Pydantic schema to an LLM via with_structured_output."""
    return llm.with_structured_output(schema)
