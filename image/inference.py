"""Acquire and renew the platform's spend-capped model credential inside the image."""

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from dotenv import set_key
from pydantic import BaseModel, SecretStr, TypeAdapter

# The inference state's helpers live in the release's stdlib-only core, which the image ships
# beside this folder but does not install into Hermes's interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "src"))
from research_core import inference as state  # noqa: E402

log = logging.getLogger(__name__)


@dataclass
class Availability:
    """The image's inference gate: an unexpired lease and no block in the inference state."""

    lease_file: Path | None = None
    expires_at: datetime = datetime.min.replace(tzinfo=UTC)

    def blocking(self) -> state.InferenceState | None:
        return state.blocking_state(self.lease_file)

    def available(self) -> bool:
        return datetime.now(UTC) < self.expires_at and self.blocking() is None


class Lease(BaseModel):
    key: SecretStr
    expires_at: datetime
    renew_at: datetime
    allowance_usd: float


class Unavailable(BaseModel):
    kind: Literal["budget_exhausted", "reconciliation_required", "mint_in_progress"]
    message: str
    next_refill_at: datetime
    retry_at: datetime


async def maintain(
    *,
    platform_url: str,
    workspace_id: str,
    credential: str,
    homes: tuple[Path, ...],
    availability: Availability,
    lease_file: Path,
    ready: asyncio.Event,
) -> None:
    async with httpx.AsyncClient(
        base_url=platform_url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {credential}"},
        timeout=30,
    ) as client:
        while True:
            (response,) = await asyncio.gather(
                client.put(f"v2/workspaces/{workspace_id}/inference"), return_exceptions=True
            )
            if isinstance(response, httpx.TransportError):
                log.warning("Inference lease transport unavailable")
                await asyncio.sleep(10)
                continue
            if isinstance(response, BaseException):
                raise response
            if not response.is_success:
                delay = max(1, int(response.headers.get("Retry-After", "30")))
                log.warning(
                    "Inference lease refused: HTTP %s; retry in %ss", response.status_code, delay
                )
                await asyncio.sleep(delay)
                continue
            result = TypeAdapter(Lease | Unavailable).validate_json(response.content)
            if isinstance(result, Unavailable):
                log.warning("Inference unavailable: %s", result.kind)
                state.lease_refused(
                    lease_file,
                    result.next_refill_at,
                    budget_exhausted=result.kind == "budget_exhausted",
                )
                renew_at = min(result.retry_at, result.next_refill_at)
            else:
                for home in homes:
                    pending = home / ".env.inference-pending"
                    pending.touch(mode=0o600)
                    pending.chmod(0o600)
                    pending.write_text((home / ".env").read_text())
                    set_key(pending, "OPENROUTER_API_KEY", result.key.get_secret_value())
                    pending.replace(home / ".env")
                refill_at = result.expires_at - state.RENEWAL_GRACE
                # Renew by the refill even if the platform's renew_at lands later, so a
                # workspace blocked on a spent key resumes on time.
                renew_at = min(result.renew_at, refill_at)
                availability.expires_at = result.expires_at
                pending = lease_file.with_suffix(".pending")
                pending.write_text(result.expires_at.isoformat())
                pending.replace(lease_file)
                state.lease_installed(lease_file, result.expires_at, result.key.get_secret_value())
                ready.set()
            await asyncio.sleep(max(2, (renew_at - datetime.now(UTC)).total_seconds()))
