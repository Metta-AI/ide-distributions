"""The Softmax platform boundary: the only way core code touches Softmax."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class Identity:
    subject_type: str  # "player" or "user"; only "player" credentials are allowed (C6)
    player_id: str
    expires_at: datetime


@dataclass(frozen=True)
class XpHandle:
    request_id: str
    experiment: str
    batch: int
    attempt: int


@dataclass(frozen=True)
class CreditDenied:
    reason: str


@dataclass(frozen=True)
class XpStatus:
    state: Literal["pending", "running", "completed", "failed"]
    episodes_completed: int
    episodes_total: int
    raw_status: str | None = None  # the platform's own status string, when there is one
    warning: str | None = None  # set when the platform status was not recognised


class Softmax(Protocol):
    def whoami(self) -> Identity: ...
    def credits(self) -> int: ...
    def game_version(self, league_id: str) -> str: ...
    def xp_create(
        self, experiment: str, batch: int, attempt: int, spec: dict
    ) -> XpHandle | CreditDenied: ...
    def xp_status(self, handle: XpHandle) -> XpStatus: ...
    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]: ...
    def xp_recover(self, experiment: str) -> list[XpHandle]: ...
    def upload(self, artifact: Path, name: str) -> str: ...
    def submit(self, policy_version_id: str, league_id: str) -> str: ...
    def submissions(self, player_id: str) -> list[dict]: ...
    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None: ...


def from_env(env: Mapping[str, str], canonical: Path) -> Softmax:
    """AUTORESEARCH_SOFTMAX=fake selects the persistent fake; anything else is the real client."""
    if env.get("AUTORESEARCH_SOFTMAX") == "fake":
        from autoresearch.softmax_tool.fake import FakeSoftmax

        return FakeSoftmax(canonical, env)
    from autoresearch.softmax_tool.client import SoftmaxClient

    return SoftmaxClient(env)
