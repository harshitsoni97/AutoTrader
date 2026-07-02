"""Central logging configuration.

By default only INFO and above are shown — this hides the per-symbol DEBUG spam
(corporate actions, adx_gate lines, etc.) without deleting those log statements.
Set LOG_LEVEL=DEBUG in the environment to see them again when diagnosing.
"""

from __future__ import annotations

import logging
import os

import structlog


def configure_logging(default_level: str = "INFO") -> None:
    level_name = os.getenv("LOG_LEVEL", default_level).upper()
    level = getattr(logging, level_name, logging.INFO)

    # Drop log calls below `level` before any processing (fast + quiet).
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )

    # Quiet chatty third-party libraries that log independently of structlog.
    for noisy in ("yfinance", "urllib3", "httpx", "httpcore", "peewee", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
