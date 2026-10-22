"""DailyBias — the ONLY thing the strategist agent is allowed to output.

The hybrid-agent safety invariant lives here: an agent (LLM or rules) can *bias* and
*tighten* the deterministic pipeline, but it can NEVER loosen a limit or bypass a guard.
Every field is clamped to a safe range relative to the base config before the pipeline
reads it — so even a hallucinating LLM cannot widen risk, disable the throttle, or raise
position limits. It can only:
  - favor/avoid known strategies (from a fixed set),
  - make the market throttle TIGHTER (never looser),
  - REDUCE position sizing (risk_mult <= 1.0),
  - REDUCE max positions (never raise).

The guards (sector gate, throttle, Time Machine, overbought ceiling) still fire
independently regardless of any bias — the bias only shapes what's proposed, not whether
the rails apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KNOWN_STRATEGIES = {"ORB", "COMPOSITE", "MEAN_REVERSION"}

RISK_MULT_FLOOR = 0.3      # never size below 30% of normal
RISK_MULT_CEIL = 1.0       # never size ABOVE normal (agent can't lever up)


@dataclass
class DailyBias:
    favor: list[str] = field(default_factory=list)          # strategies to prefer
    avoid: list[str] = field(default_factory=list)          # strategies to stand down
    throttle_min_nifty_pct: float | None = None             # None=use config; else TIGHTER only
    risk_mult: float = 1.0                                   # [0.3, 1.0] — reduce-only
    max_positions: int | None = None                        # None=use config; else LOWER only
    rationale: str = ""

    def as_dict(self) -> dict:
        return {
            "favor": self.favor, "avoid": self.avoid,
            "throttle_min_nifty_pct": self.throttle_min_nifty_pct,
            "risk_mult": self.risk_mult, "max_positions": self.max_positions,
            "rationale": self.rationale,
        }


def clamp_bias(raw: DailyBias, base_throttle_pct: float, base_max_positions: int) -> DailyBias:
    """Enforce the safety invariant: tighten/reduce only, never loosen. Pure.

    base_throttle_pct: the configured market_throttle.min_nifty_pct (e.g. -0.5).
    base_max_positions: the configured cap. The agent may lower both, never raise.
    """
    favor = [s for s in (raw.favor or []) if s in KNOWN_STRATEGIES]
    avoid = [s for s in (raw.avoid or []) if s in KNOWN_STRATEGIES]
    # A strategy can't be both favored and avoided; avoid wins (the safer choice).
    favor = [s for s in favor if s not in avoid]

    # risk_mult: clamp to [0.3, 1.0] — reduce-only (can never size above normal).
    rm = raw.risk_mult if raw.risk_mult is not None else 1.0
    rm = max(RISK_MULT_FLOOR, min(RISK_MULT_CEIL, rm))

    # throttle: only TIGHTER. Tighter = throttle on milder dips = value closer to 0.
    # Allowed range [base, 0.0]; a looser (more negative) proposal clamps up to base.
    thr = raw.throttle_min_nifty_pct
    if thr is not None:
        thr = max(base_throttle_pct, min(0.0, thr))

    # max_positions: only LOWER, floor 1.
    mp = raw.max_positions
    if mp is not None:
        mp = max(1, min(base_max_positions, int(mp)))

    return DailyBias(favor=favor, avoid=avoid, throttle_min_nifty_pct=thr,
                     risk_mult=rm, max_positions=mp, rationale=raw.rationale or "")
