#!/usr/bin/env python3
"""Quick test to verify all LLM providers and compete stacks are reachable."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from pathlib import Path
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        for line in _env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

from autotrader.core.config import load_config
from autotrader.core.llm import get_fast_llm, get_analysis_llm, get_report_llm, _make_llm

cfg = load_config()

def test_llm(label, llm):
    if llm is None:
        print(f"  {label}: FAIL — could not initialise")
        return False
    try:
        r = llm.invoke("Reply with just the word OK.")
        print(f"  {label}: PASS — {r.content[:40].strip()!r}")
        return True
    except Exception as e:
        print(f"  {label}: FAIL — {str(e)[:80]}")
        return False

print("\n=== Main Pipeline ===")
test_llm(f"Fast     ({cfg.llm.fast_provider}/{cfg.llm.fast_model})", get_fast_llm(cfg.llm))
test_llm(f"Analysis ({cfg.llm.analysis_provider}/{cfg.llm.analysis_model})", get_analysis_llm(cfg.llm))
test_llm(f"Report   ({cfg.llm.report_provider}/{cfg.llm.report_model})", get_report_llm(cfg.llm))

if cfg.compete.enabled:
    print("\n=== Compete Stacks ===")
    for stack in cfg.compete.stacks:
        print(f"\n  [{stack.name}]")
        for tier, provider, model, temp, tokens in [
            ("fast",     stack.fast_provider,     stack.fast_model,     stack.fast_temperature,     stack.fast_max_tokens),
            ("analysis", stack.analysis_provider, stack.analysis_model, stack.analysis_temperature, stack.analysis_max_tokens),
            ("report",   stack.report_provider,   stack.report_model,   0.3,                        stack.report_max_tokens),
        ]:
            try:
                llm = _make_llm(provider, model, temp, tokens)
                test_llm(f"  {tier:<8} ({provider}/{model})", llm)
            except Exception as e:
                print(f"  {tier:<8} ({provider}/{model}): FAIL — {str(e)[:80]}")

print()
