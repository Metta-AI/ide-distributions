"""Who writes: ``session:<hermes id>``, ``user:<id>`` or ``harness`` (design B.2).

Every store write records ``str(actor)`` in ``created_by`` and in its ``events`` row. A session
actor may carry the lease epoch its run was started with, which the Task lease check compares
with the Task's ``lease_epoch`` (B.13).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

KINDS = ("session", "user", "harness")
HARNESS_ID = "harness"

_ACTOR = re.compile(r"^(session|user):(\S+)$")


@dataclass(frozen=True)
class Actor:
    kind: Literal["session", "user", "harness"]
    id: str
    epoch: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"not an actor kind: {self.kind!r}")
        if not self.id:
            raise ValueError("an actor needs an id")
        if self.kind == "harness" and self.id != HARNESS_ID:
            raise ValueError("the harness actor has no id of its own")

    def __str__(self) -> str:
        if self.kind == "harness":
            return HARNESS_ID
        return f"{self.kind}:{self.id}"

    @property
    def is_session(self) -> bool:
        return self.kind == "session"

    @property
    def is_user(self) -> bool:
        return self.kind == "user"

    @property
    def is_harness(self) -> bool:
        return self.kind == "harness"

    @classmethod
    def harness(cls) -> Actor:
        return cls(kind="harness", id=HARNESS_ID)

    @classmethod
    def session(cls, session_id: str, epoch: int | None = None) -> Actor:
        return cls(kind="session", id=session_id, epoch=epoch)

    @classmethod
    def user(cls, user_id: str) -> Actor:
        return cls(kind="user", id=user_id)

    @classmethod
    def parse(cls, text: str) -> Actor:
        """The inverse of ``str(actor)``. The epoch is not part of the text."""
        if text == HARNESS_ID:
            return cls.harness()
        match = _ACTOR.match(text)
        if match is None:
            raise ValueError(f"not an actor: {text!r} (session:<id>, user:<id> or harness)")
        kind, ident = match.groups()
        return cls(kind=kind, id=ident)  # type: ignore[arg-type]

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Actor:
        """``IDE_ACTOR`` when set; else a session actor from ``IDE_SESSION_ID`` or
        ``HERMES_SESSION_ID``; else the harness. ``IDE_LEASE_EPOCH`` sets a session's epoch."""
        if env.get("IDE_ACTOR"):
            actor = cls.parse(env["IDE_ACTOR"])
        else:
            session_id = env.get("IDE_SESSION_ID") or env.get("HERMES_SESSION_ID")
            actor = cls.session(session_id) if session_id else cls.harness()
        if actor.is_session and env.get("IDE_LEASE_EPOCH"):
            actor = replace(actor, epoch=int(env["IDE_LEASE_EPOCH"]))
        return actor
