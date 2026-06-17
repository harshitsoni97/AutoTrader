"""LLM factory and Pydantic output schemas for structured agent enrichment.

All public functions return a LangChain chat model bound to a Pydantic schema
via `with_structured_output()`.  Every call site wraps invocations in
try/except so deterministic logic always serves as a safe fallback.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output schemas — LLM MUST return these shapes (enforced by
# with_structured_output).  A2A messages carry the enrichment as a nested
# `llm_enrichment` key so existing downstream consumers are unaffected.
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
# LLM factory helpers
# ---------------------------------------------------------------------------

def _is_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _make_llm(model: str, temperature: float, max_tokens: int) -> Any:
    from langchain_anthropic import ChatAnthropic  # lazy import
    return ChatAnthropic(model=model, temperature=temperature, max_tokens=max_tokens)


def get_fast_llm(cfg: Any) -> Any | None:
    """Return a fast/cheap LLM instance, or None if unavailable."""
    if not _is_available():
        return None
    try:
        return _make_llm(
            model=cfg.fast_model,
            temperature=cfg.fast_temperature,
            max_tokens=cfg.fast_max_tokens,
        )
    except Exception as exc:
        logger.warning("Could not initialise fast LLM: %s", exc)
        return None


def get_analysis_llm(cfg: Any) -> Any | None:
    """Return the analysis-tier LLM instance, or None if unavailable."""
    if not _is_available():
        return None
    try:
        return _make_llm(
            model=cfg.analysis_model,
            temperature=cfg.analysis_temperature,
            max_tokens=cfg.analysis_max_tokens,
        )
    except Exception as exc:
        logger.warning("Could not initialise analysis LLM: %s", exc)
        return None


def get_report_llm(cfg: Any) -> Any | None:
    """Return the report-generation LLM, optionally with extended thinking."""
    if not _is_available():
        return None
    try:
        from langchain_anthropic import ChatAnthropic
        kwargs: dict[str, Any] = {
            "model": cfg.report_model,
            "max_tokens": cfg.report_max_tokens,
        }
        budget = getattr(cfg, "report_thinking_budget", 0)
        if budget > 0:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["temperature"] = 1.0  # required for extended thinking
        else:
            kwargs["temperature"] = 0.3
        return ChatAnthropic(**kwargs)
    except Exception as exc:
        logger.warning("Could not initialise report LLM: %s", exc)
        return None


def structured(llm: Any, schema: type[BaseModel]) -> Any:
    """Bind a Pydantic schema to an LLM via with_structured_output."""
    return llm.with_structured_output(schema)
