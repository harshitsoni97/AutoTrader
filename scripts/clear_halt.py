#!/usr/bin/env python3
"""Manually clear the circuit-breaker HALT flag so trading can resume.

Use only after reviewing why the breaker tripped.

    python3 scripts/clear_halt.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autotrader.safety.controls import clear_halt, HALT_FILE

if __name__ == "__main__":
    if clear_halt():
        print(f"Halt cleared ({HALT_FILE}). Trading can resume on the next run.")
    else:
        print(f"No halt flag present ({HALT_FILE}). Nothing to clear.")
