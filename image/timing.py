"""Startup telemetry shared by the runtime and packaged log verification."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StartupTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["workspace_startup"] = "workspace_startup"
    outcome: Literal["started", "passed"]
    workspace_id: str
    boot_id: str
    phase: str
    elapsed_seconds: float
    timestamp: datetime
    role: str | None = None
