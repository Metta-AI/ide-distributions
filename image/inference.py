"""Acquire and renew the platform's spend-capped model credential inside the image."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from dotenv import set_key
from pydantic import BaseModel, SecretStr, TypeAdapter

log = logging.getLogger(__name__)


@dataclass
class Availability:
    expires_at: datetime = datetime.min.replace(tzinfo=UTC)

    def available(self) -> bool:
        return datetime.now(UTC) < self.expires_at


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
                log.warning("Inference lease refused: HTTP %s", response.status_code)
                await asyncio.sleep(30)
                continue
            result = TypeAdapter(Lease | Unavailable).validate_json(response.content)
            if isinstance(result, Unavailable):
                log.warning("Inference unavailable: %s", result.kind)
                renew_at = result.retry_at
            else:
                for home in homes:
                    pending = home / ".env.inference-pending"
                    pending.touch(mode=0o600)
                    pending.chmod(0o600)
                    pending.write_text((home / ".env").read_text())
                    set_key(pending, "OPENROUTER_API_KEY", result.key.get_secret_value())
                    pending.replace(home / ".env")
                renew_at = result.renew_at
                availability.expires_at = result.expires_at
                pending = lease_file.with_suffix(".pending")
                pending.write_text(result.expires_at.isoformat())
                pending.replace(lease_file)
            await asyncio.sleep(max(2, (renew_at - datetime.now(UTC)).total_seconds()))
