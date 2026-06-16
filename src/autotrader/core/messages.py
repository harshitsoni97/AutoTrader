"""A2A (Agent-to-Agent) communication schema and helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class A2AMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_agent: str
    target_agent: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    symbol: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.model_dump()


def create_message(
    source: str,
    target: str,
    payload: dict[str, Any],
    symbol: str = "",
) -> dict:
    return A2AMessage(
        source_agent=source,
        target_agent=target,
        symbol=symbol,
        payload=payload,
    ).to_dict()


def audit_entry(agent: str, action: str, data: dict[str, Any] | None = None) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": action,
        "data": data or {},
    }
