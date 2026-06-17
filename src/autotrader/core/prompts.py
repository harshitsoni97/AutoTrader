"""Prompt registry — loads versioned templates from config/prompts.yaml."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent.parent.parent.parent / "config" / "prompts.yaml"
_registry: dict | None = None


def _load_registry() -> dict:
    global _registry
    if _registry is not None:
        return _registry
    if not _PROMPTS_PATH.exists():
        logger.warning("prompts.yaml not found at %s; using empty registry", _PROMPTS_PATH)
        _registry = {}
        return _registry
    with open(_PROMPTS_PATH) as f:
        data = yaml.safe_load(f) or {}
    _registry = data.get("prompts", {})
    return _registry


def get_prompt(name: str, **kwargs: object) -> str:
    """Render a named prompt template with the given keyword arguments."""
    registry = _load_registry()
    entry = registry.get(name)
    if entry is None:
        raise KeyError(f"Prompt '{name}' not found in registry. Available: {list(registry)}")
    template: str = entry["template"]
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        raise ValueError(f"Missing placeholder {exc} when rendering prompt '{name}'") from exc


def get_prompt_version(name: str) -> str:
    """Return the version string of a named prompt (for tracing metadata)."""
    registry = _load_registry()
    entry = registry.get(name, {})
    return entry.get("version", "unknown")


def reload_registry() -> None:
    """Force a reload of the prompts file (useful after hot-editing prompts.yaml)."""
    global _registry
    _registry = None
    _load_registry()
