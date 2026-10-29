"""Shared helpers for the watcher step tests: a TickContext on fakes, records and sessions.

``make_ctx`` builds a workspace with ``cow_fake`` registered and active, both stores on one
``FakeClock`` (so ``advance`` ages leases for every reader), the fake platform and
``FakeHermesRuns``. The record helpers go through the store library, as the tools would.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_core.layout import Workspace
from research_core.platform.fake import FakePlatform
from research_core.store import coworlds, goals, sessions, settings, tasks
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.watcher.context import TickContext
from research_core.watcher.hermes_api import FakeHermesRuns

COWORLD = "cow_fake"
HARNESS = Actor.harness()
USER = Actor.user("u1")


class FakeClock:
    """A callable clock both stores and the context read; ``advance`` moves it forward."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 10, 28, 21, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_ctx(tmp_path: Path, *, clock: FakeClock | None = None, **overrides) -> TickContext:
    clock = clock or FakeClock()
    ws = Workspace(root=tmp_path / "ws", venv=tmp_path / "ws" / "tools" / "venv")
    ws.ensure_dirs()
    wstore = Store.open(ws.store_path, kind="workspace", clock=clock)
    coworld = ws.coworld(COWORLD)
    coworld.ensure_dirs()
    coworlds.register(wstore, HARNESS, COWORLD, folder=coworld.folder_name)
    coworlds.activate(wstore, HARNESS, COWORLD)
    store = Store.open(coworld.store_path, kind="coworld", clock=clock)
    platform = FakePlatform(tmp_path / "fake-platform.json", env={}, clock=clock)
    ctx = TickContext(
        ws=ws,
        wstore=wstore,
        hermes=FakeHermesRuns(),
        platform=platform,
        env={},
        now=clock.now,
        researcher_profiles=("researcher",),
        coworld=coworld,
        store=store,
    )
    ctx.clock = clock  # the tests' handle; TickContext itself carries only ``now``
    for name, value in overrides.items():
        setattr(ctx, name, value)
    return ctx


def advance(ctx: TickContext, seconds: float) -> None:
    ctx.clock.advance(seconds)
    ctx.now = ctx.clock.now


def set_setting(ctx: TickContext, key: str, value) -> None:
    store = ctx.wstore if settings.spec(key).scope == "workspace" else ctx.store
    settings.update(store, USER, key, value, cites="msg:test")


def make_goal(ctx: TickContext, title: str = "Reach division 1") -> int:
    record = goals.create(
        ctx.store, HARNESS, title=title, end_state="the champion sits in division 1", reason="asked"
    )
    return int(record["id"].removeprefix("G-"))


def make_task(ctx: TickContext, goal: int | None = None, **fields) -> dict:
    fields.setdefault("type", "research")
    fields.setdefault("title", "Study the opening")
    fields.setdefault("description", "Read the replays and record what wins the first fight.")
    if goal is None and fields["type"] != "maintenance":
        goal = make_goal(ctx)
    return tasks.create(ctx.store, HARNESS, goal_id=goal, **fields)


def task_number(record: dict | str | int) -> int:
    if isinstance(record, dict):
        record = record["id"]
    if isinstance(record, str):
        return int(record.removeprefix("T-"))
    return int(record)


def register_session(
    ctx: TickContext, session_id: str, task: dict | int | str, *, profile: str = "researcher"
) -> dict:
    return sessions.register(
        ctx.wstore,
        HARNESS,
        session_id=session_id,
        profile=profile,
        kind="task",
        coworld_id=COWORLD,
        task_id=task_number(task),
    )


def start_run(ctx: TickContext, session_id: str, text: str = "go") -> str:
    """A run on the fake gateway recorded on the Session, as the watcher's start does."""
    session = sessions.read(ctx.wstore, session_id)
    run_id = ctx.hermes.start(profile=session["profile"], session_id=session_id, text=text)
    sessions.set_run(ctx.wstore, HARNESS, session_id, run_id)
    return run_id


def claim(ctx: TickContext, session_id: str, task: dict | int | str) -> dict:
    """The Session claims the Task as the ``task.claim`` tool would (run id from its row)."""
    n = task_number(task)
    session = sessions.read(ctx.wstore, session_id)
    return tasks.claim(
        ctx.store,
        Actor.session(session_id),
        n,
        run_id=session["run_id"] if session else None,
        worktree=str(ctx.coworld.worktree_for_task(n)),
        branch=f"task/{n}",
    )


def working_session(
    ctx: TickContext, session_id: str = "api_1761690000_aaaa0001", task: dict | None = None
) -> tuple[dict, dict, str]:
    """A Task held by a Session with a live run: ``(task, session, run_id)``."""
    task = task or make_task(ctx)
    register_session(ctx, session_id, task)
    run_id = start_run(ctx, session_id)
    task = claim(ctx, session_id, task)
    return task, sessions.read(ctx.wstore, session_id), run_id


def events_of(store: Store, kind: str) -> list[dict]:
    return [event.to_dict() for event in store.events_after(0, 10_000) if event.kind == kind]
