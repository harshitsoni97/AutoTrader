"""RL Parameter Tuning Agent — Q-learning walk-forward optimizer for strategy parameters.

After each post-market run the agent:
  1. Computes a reward signal from today's trade outcomes.
  2. Identifies the (market_regime, performance_bucket) state.
  3. Selects an action (adjust one parameter) via ε-greedy from the Q-table.
  4. Applies the action and writes updated params to config/strategy_params.json.
  5. Updates Q-values for the (prev_state, prev_action) → reward transition.
  6. Persists the Q-table to config/rl_q_table.json.

Q-table key: "{regime}|{perf_bucket}" → {"param:direction": [q_value, visit_count], ...}
"""

from __future__ import annotations

import json
import math
import os
import random
from typing import Any

import structlog

from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "RLTuningAgent"

_CONFIG_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../../config")
)
_PARAMS_PATH = os.path.join(_CONFIG_DIR, "strategy_params.json")
_QTABLE_PATH = os.path.join(_CONFIG_DIR, "rl_q_table.json")

# Parameters, their defaults, ranges, and step sizes
PARAM_SPACE: dict[str, dict] = {
    "adx_threshold":  {"default": 20,  "min": 15,   "max": 30,  "step": 2},
    "rsi_min":        {"default": 50,  "min": 45,   "max": 62,  "step": 2},
    "stop_multiplier":{"default": 1.0, "min": 0.75, "max": 2.0, "step": 0.25},
    "min_score":      {"default": 60,  "min": 50,   "max": 80,  "step": 5},
    "target_rr_min":  {"default": 1.0, "min": 0.75, "max": 2.0, "step": 0.25},
}

# Minimum trades needed before any adjustment is made
MIN_TRADES = 3

# ε-greedy exploration: decays from 0.30 → 0.05 over 60 trading days
EPSILON_START = 0.30
EPSILON_MIN   = 0.05
EPSILON_DECAY = 60

# Q-learning rate and discount (discount near 0 — each day is semi-independent)
ALPHA = 0.20
GAMMA = 0.10


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_params() -> dict:
    try:
        with open(_PARAMS_PATH) as f:
            p = json.load(f)
        # Backfill any missing keys with defaults
        for name, spec in PARAM_SPACE.items():
            p.setdefault(name, spec["default"])
        return p
    except Exception:
        return {name: spec["default"] for name, spec in PARAM_SPACE.items()}


def _save_params(params: dict) -> None:
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    out = {k: v for k, v in params.items() if not k.startswith("_")}
    out["_comment"] = (
        "Auto-tuned by RLTuningAgent. Do NOT edit manually — values will be overwritten post-market."
    )
    out["_version"] = params.get("_version", 1)
    with open(_PARAMS_PATH, "w") as f:
        json.dump(out, f, indent=2)


def _load_qtable() -> dict:
    try:
        with open(_QTABLE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_qtable(qtable: dict) -> None:
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    with open(_QTABLE_PATH, "w") as f:
        json.dump(qtable, f, indent=2)


# ---------------------------------------------------------------------------
# State and reward computation
# ---------------------------------------------------------------------------

def _performance_bucket(win_rate: float, avg_rr: float) -> str:
    """Discretize recent performance into 3 buckets."""
    if win_rate >= 55 and avg_rr >= 1.2:
        return "winning"
    if win_rate <= 35 or avg_rr < 0.8:
        return "losing"
    return "neutral"


def _compute_reward(outcomes: list[dict], daily_pnl: float) -> float:
    """Composite reward: win_rate × avg_rr × pnl_sign, bounded [-1, 1]."""
    if not outcomes:
        return 0.0
    wins = sum(1 for o in outcomes if o.get("pnl", 0) > 0)
    win_rate = wins / len(outcomes)
    rrs = [o["rr"] for o in outcomes if o.get("rr", 0) > 0]
    avg_rr = sum(rrs) / len(rrs) if rrs else 0.5
    pnl_sign = math.copysign(1, daily_pnl) if daily_pnl != 0 else 0.0
    raw = (win_rate - 0.5) * 2 * avg_rr * (0.5 + 0.5 * pnl_sign)
    return max(-1.0, min(1.0, round(raw, 4)))


# ---------------------------------------------------------------------------
# Action selection
# ---------------------------------------------------------------------------

def _build_actions() -> list[tuple[str, int]]:
    """All (param_name, direction) pairs where direction ∈ {+1, -1}."""
    actions = []
    for name in PARAM_SPACE:
        actions.append((name, +1))
        actions.append((name, -1))
    actions.append(("_hold", 0))  # no-op
    return actions


ACTIONS = _build_actions()


def _action_key(param: str, direction: int) -> str:
    return f"{param}:{'+' if direction > 0 else '-' if direction < 0 else '0'}"


def _epsilon(total_days: int) -> float:
    decay = math.exp(-total_days / EPSILON_DECAY)
    return EPSILON_MIN + (EPSILON_START - EPSILON_MIN) * decay


def _select_action(state_key: str, qtable: dict, total_days: int) -> tuple[str, int]:
    eps = _epsilon(total_days)
    if random.random() < eps:
        return random.choice(ACTIONS)

    q_row = qtable.get(state_key, {})
    if not q_row:
        return ("_hold", 0)

    best_action = max(ACTIONS, key=lambda a: q_row.get(_action_key(*a), (0.0, 0))[0])
    return best_action


def _update_q(
    qtable: dict,
    state_key: str,
    action: tuple[str, int],
    reward: float,
    next_state_key: str,
) -> None:
    """In-place Q-update: Q(s,a) += alpha * (R + gamma * max_a' Q(s',a') - Q(s,a))."""
    row = qtable.setdefault(state_key, {})
    akey = _action_key(*action)
    q_val, visits = row.get(akey, [0.0, 0])

    next_row = qtable.get(next_state_key, {})
    max_next = max((v[0] for v in next_row.values()), default=0.0)

    new_q = q_val + ALPHA * (reward + GAMMA * max_next - q_val)
    row[akey] = [round(new_q, 6), visits + 1]


def _apply_action(params: dict, param: str, direction: int) -> dict:
    """Clamp-safe parameter adjustment, returns updated params dict."""
    if param == "_hold" or direction == 0:
        return params
    spec = PARAM_SPACE.get(param)
    if not spec:
        return params
    new_val = params.get(param, spec["default"]) + direction * spec["step"]
    new_val = max(spec["min"], min(spec["max"], new_val))
    # Round to avoid float drift
    if isinstance(spec["default"], int):
        new_val = int(round(new_val))
    else:
        new_val = round(new_val, 4)
    return {**params, param: new_val}


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

def rl_tuning_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Starting RL parameter tuning", AGENT_NAME)

    outcomes = state.get("trade_outcomes", [])
    daily_pnl = state.get("daily_pnl", 0.0)
    market_regime = state.get("market_regime", "unknown")

    params = _load_params()
    qtable = _load_qtable()
    total_days = qtable.get("__meta__", {}).get("total_days", 0)

    if len(outcomes) < MIN_TRADES:
        logger.info(
            "[%s] Only %d outcome(s) — need at least %d before tuning. Skipping.",
            AGENT_NAME, len(outcomes), MIN_TRADES,
        )
        entry = audit_entry(
            agent=AGENT_NAME,
            action="rl_skipped",
            data={"reason": "insufficient_trades", "count": len(outcomes)},
        )
        return {"strategy_params": params, "audit_trail": [entry]}

    wins = sum(1 for o in outcomes if o.get("pnl", 0) > 0)
    win_rate = wins / len(outcomes) * 100
    rrs = [o["rr"] for o in outcomes if o.get("rr", 0) > 0]
    avg_rr = sum(rrs) / len(rrs) if rrs else 0.0

    perf_bucket = _performance_bucket(win_rate, avg_rr)
    state_key = f"{market_regime}|{perf_bucket}"
    reward = _compute_reward(outcomes, daily_pnl)

    # Recover previous (state, action) from Q-table meta for the Q-update
    meta = qtable.get("__meta__", {})
    prev_state_key = meta.get("prev_state_key")
    prev_action_raw = meta.get("prev_action")
    if prev_state_key and prev_action_raw:
        prev_action = (prev_action_raw[0], prev_action_raw[1])
        _update_q(qtable, prev_state_key, prev_action, reward, state_key)

    # Select action for today (will be evaluated tomorrow)
    action = _select_action(state_key, qtable, total_days)
    new_params = _apply_action(params, action[0], action[1])

    # Persist Q-table and updated params
    total_days += 1
    qtable["__meta__"] = {
        "total_days": total_days,
        "prev_state_key": state_key,
        "prev_action": list(action),
        "last_reward": reward,
        "epsilon": round(_epsilon(total_days), 4),
    }
    _save_qtable(qtable)
    _save_params(new_params)

    action_desc = f"{action[0]}:{'+' if action[1] > 0 else '-'}{PARAM_SPACE.get(action[0], {}).get('step', 0) if action[0] != '_hold' else 0}"
    logger.info(
        "[%s] day=%d regime=%s perf=%s win=%.0f%% rr=%.2f reward=%.3f action=%s eps=%.2f",
        AGENT_NAME, total_days, market_regime, perf_bucket,
        win_rate, avg_rr, reward, action_desc, _epsilon(total_days),
    )
    logger.info("[%s] New params: %s", AGENT_NAME, {k: v for k, v in new_params.items() if not k.startswith("_")})

    entry = audit_entry(
        agent=AGENT_NAME,
        action="rl_tuning_complete",
        data={
            "day": total_days,
            "regime": market_regime,
            "perf_bucket": perf_bucket,
            "win_rate": round(win_rate, 1),
            "avg_rr": round(avg_rr, 2),
            "reward": reward,
            "action": action_desc,
            "epsilon": round(_epsilon(total_days), 4),
            "new_params": {k: v for k, v in new_params.items() if not k.startswith("_")},
        },
    )

    return {
        "strategy_params": new_params,
        "audit_trail": [entry],
    }
