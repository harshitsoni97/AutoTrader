"""LLMOps tracing setup — configures LangSmith or MLflow for LLM call tracing."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autotrader.core.config import PlatformConfig

logger = logging.getLogger(__name__)


def setup_tracing(cfg: "PlatformConfig") -> None:
    """Configure LLM call tracing based on llmops config. Always a no-op on failure."""
    ops = cfg.llmops
    backend = ops.backend.lower()

    if backend == "langsmith":
        _setup_langsmith(ops)
    elif backend == "mlflow":
        _setup_mlflow(ops)
    elif backend == "none":
        logger.debug("LLMOps tracing disabled (backend=none)")
    else:
        logger.warning("Unknown llmops.backend '%s'; tracing disabled", backend)


def _setup_langsmith(ops: object) -> None:
    api_key = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        logger.info(
            "LangSmith tracing skipped — set LANGCHAIN_API_KEY or LANGSMITH_API_KEY to enable"
        )
        return
    os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = ops.project_name
    os.environ["LANGCHAIN_ENDPOINT"] = ops.langsmith_endpoint
    if ops.tags:
        os.environ.setdefault("LANGCHAIN_TAGS", ",".join(ops.tags))
    logger.info("LangSmith tracing enabled — project=%s", ops.project_name)


def _setup_mlflow(ops: object) -> None:
    try:
        import mlflow
        import mlflow.langchain as mlflow_lc
    except ImportError:
        logger.warning("mlflow not installed; run 'pip install mlflow'. Tracing disabled.")
        return
    try:
        mlflow.set_tracking_uri(ops.mlflow_tracking_uri)
        mlflow.set_experiment(ops.project_name)
        mlflow_lc.autolog()
        logger.info(
            "MLflow tracing enabled — uri=%s experiment=%s",
            ops.mlflow_tracking_uri,
            ops.project_name,
        )
    except Exception as exc:
        logger.warning("MLflow setup failed: %s — tracing disabled", exc)


def log_run_metadata(cfg: "PlatformConfig", metadata: dict) -> None:
    """Log run-level tags/metadata to the active tracing backend (best-effort)."""
    ops = cfg.llmops
    backend = ops.backend.lower()

    if backend == "langsmith":
        try:
            from langsmith import Client

            client = Client()
            # LangSmith doesn't have a direct "run metadata" API outside of traces;
            # log as a structured note using a dummy run if needed.
            logger.debug("LangSmith run metadata: %s", metadata)
        except Exception:
            pass

    elif backend == "mlflow":
        try:
            import mlflow

            with mlflow.start_run(nested=True):
                mlflow.log_params(
                    {k: str(v) for k, v in metadata.items() if isinstance(v, (str, int, float, bool))}
                )
        except Exception as exc:
            logger.debug("MLflow log_run_metadata failed: %s", exc)
