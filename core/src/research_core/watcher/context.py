"""What one watcher tick works with (design §8, Appendix H "Watcher tick order").

A tick sees the workspace store, the active Coworld's store (if any), the platform, the Hermes
runs client and a clock. Every step receives the same :class:`TickContext`; steps never build
their own. The watcher has no model: it reads records, calls the platform and the runs API, and
starts wake Sessions for the events a model must judge.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_core import layout
from research_core.layout import Coworld, Workspace
from research_core.store.actors import Actor
from research_core.store.db import Store

WATCHER_ACTOR = Actor.parse("harness")
DEFAULT_RESEARCHER_PROFILES = ("researcher",)
ORCHESTRATOR_PROFILE = "orchestrator"


def researcher_profiles(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("IDE_RESEARCHER_PROFILES", "")
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    return names or DEFAULT_RESEARCHER_PROFILES


@dataclass
class TickContext:
    ws: Workspace
    wstore: Store
    hermes: Any  # research_core.watcher.hermes_api.HermesRuns or FakeHermesRuns
    platform: Any  # research_core.platform.protocol.Platform
    env: Mapping[str, str]
    now: datetime
    profile_home: Path | None = None
    orchestrator_profile: str = ORCHESTRATOR_PROFILE
    researcher_profiles: tuple[str, ...] = DEFAULT_RESEARCHER_PROFILES
    actor: Actor = WATCHER_ACTOR
    coworld: Coworld | None = None
    store: Store | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def stores(self) -> list[Store]:
        """The stores a tick scans: the workspace store first, then the active Coworld's."""
        return [self.wstore, self.store] if self.store is not None else [self.wstore]

    def meta(self, key: str, default: str | None = None) -> str | None:
        value = self.wstore.get_meta(key)
        return default if value is None else value

    def set_meta(self, key: str, value: str) -> None:
        self.wstore.set_meta(key, value)

    def coworld_store(self) -> Store:
        if self.store is None:
            raise RuntimeError("no active Coworld")
        return self.store

    def note(self, text: str) -> None:
        self.notes.append(text)


def build(
    env: Mapping[str, str] | None = None,
    *,
    hermes: Any = None,
    platform: Any = None,
    now: datetime | None = None,
) -> TickContext:
    """A context from the environment: the stores, the active Coworld, Hermes and the platform."""
    from research_core.store import coworlds

    env = dict(os.environ if env is None else env)
    ws = layout.from_env(env)
    ws.ensure_dirs()
    wstore = Store.open(ws.store_path, kind="workspace")
    active = coworlds.active(wstore)
    coworld = ws.coworld(active["coworld_id"]) if active else None
    store = Store.open(coworld.store_path, kind="coworld") if coworld else None
    profile_home = Path(env["HERMES_HOME"]) if env.get("HERMES_HOME") else None
    if hermes is None:
        from research_core.watcher.hermes_api import HermesRuns

        if profile_home is None:
            raise RuntimeError("HERMES_HOME is not set; the watcher needs the profile home")
        hermes = HermesRuns.from_profile(profile_home, researcher_profiles=researcher_profiles(env))
    if platform is None:
        from research_core.platform import from_env as platform_from_env

        platform = platform_from_env(env, ws)
    return TickContext(
        ws=ws,
        wstore=wstore,
        hermes=hermes,
        platform=platform,
        env=env,
        now=now or datetime.now(UTC),
        profile_home=profile_home,
        researcher_profiles=researcher_profiles(env),
        coworld=coworld,
        store=store,
    )
