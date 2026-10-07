"""Start-of-run checks; a failed preflight pauses the agent (C6)."""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from autoresearch.softmax_tool.identity import identity_problems
from autoresearch.softmax_tool.protocol import Softmax

EXPIRY_WARNING = timedelta(hours=2)
MIN_FREE_BYTES = 1024**3


@dataclass
class PreflightResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    credits: int | None = None


def preflight(
    env: Mapping[str, str],
    sm: Softmax,
    canonical: Path,
    now: datetime,
    disk_free: Callable = shutil.disk_usage,
) -> PreflightResult:
    result = PreflightResult(ok=True)
    expected_player = env.get("AUTORESEARCH_PLAYER_ID", "")

    try:
        identity = sm.whoami()
    except Exception as error:  # noqa: BLE001 - any failure means the credential cannot be trusted
        result.reasons.append(f"whoami failed: {error}")
        result.ok = False
        return result

    result.reasons.extend(identity_problems(identity, expected_player, now))
    if now < identity.expires_at and identity.expires_at - now < EXPIRY_WARNING:
        result.warnings.append(f"credential expires at {identity.expires_at.isoformat()}")

    try:
        free_bytes = disk_free(canonical).free
    except OSError as error:
        result.reasons.append(f"cannot check disk space under {canonical}: {error}")
        free_bytes = MIN_FREE_BYTES
    if free_bytes < MIN_FREE_BYTES:
        result.reasons.append(
            f"only {free_bytes // 1024**2} MB free under {canonical}; need 1024 MB"
        )

    try:
        result.credits = sm.credits()
    except Exception as error:  # noqa: BLE001 - credit lookup failure is non-fatal
        result.warnings.append(f"credits unavailable: {error}")
    else:
        if result.credits == 0:
            result.warnings.append("no credits remaining")

    result.ok = not result.reasons
    return result
