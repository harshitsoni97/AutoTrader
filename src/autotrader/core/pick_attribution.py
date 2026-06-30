"""Pick-attribution journal — measure the LLM review's value vs the scorer.

Each trading day records, side by side:
  - the DETERMINISTIC top pick (scored_opportunities[0]) and its day return
  - each LLM stack's pick and its day return
  - the consensus LLM pick
  - who actually won, and whether the LLM override beat the deterministic pick

This is the evidence needed to decide whether the LLM "review" step adds value
or should be demoted to advisory. One JSON line per day in
reports/pick_attribution.jsonl.
"""

from __future__ import annotations

import json
import os
import structlog
from collections import Counter

logger = structlog.get_logger()

_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../reports/pick_attribution.jsonl")
)


def append(run_date: str, regime: str, confidence: float,
           deterministic: dict | None, llm_picks: list[dict]) -> bool:
    """Append one day's attribution row.

    deterministic: {"symbol", "return_pct"} or None
    llm_picks:     [{"stack", "symbol", "return_pct"}]
    """
    try:
        # Consensus LLM pick = most common symbol across stacks.
        syms = [p["symbol"] for p in llm_picks if p.get("symbol")]
        consensus = Counter(syms).most_common(1)[0][0] if syms else None
        consensus_ret = next((p["return_pct"] for p in llm_picks
                              if p.get("symbol") == consensus and p.get("return_pct") is not None), None)
        det_sym = deterministic.get("symbol") if deterministic else None
        det_ret = deterministic.get("return_pct") if deterministic else None

        # Did the LLM override help? Only meaningful when picks differ and both priced.
        override_delta = None
        override_helped = None
        if consensus and det_sym and consensus != det_sym \
                and consensus_ret is not None and det_ret is not None:
            override_delta = round(consensus_ret - det_ret, 3)
            override_helped = override_delta > 0

        row = {
            "run_date": run_date,
            "regime": regime,
            "confidence": confidence,
            "deterministic_pick": det_sym,
            "deterministic_return_pct": det_ret,
            "llm_consensus_pick": consensus,
            "llm_consensus_return_pct": consensus_ret,
            "llm_picks": llm_picks,
            "picks_differed": bool(consensus and det_sym and consensus != det_sym),
            "override_delta_pct": override_delta,   # LLM minus deterministic
            "override_helped": override_helped,     # True/False/None(=same pick or unpriced)
        }
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "a") as f:
            f.write(json.dumps(row) + "\n")
        logger.info("pick_attribution_appended", deterministic=det_sym,
                    consensus=consensus, override_helped=override_helped)
        return True
    except Exception as exc:
        logger.warning("pick_attribution_failed", error=str(exc))
        return False
