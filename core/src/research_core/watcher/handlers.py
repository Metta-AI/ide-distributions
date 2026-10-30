"""The event-ownership map: how the watcher answers each event kind (design B.13, Appendix H).

Every kind in :data:`research_core.store.events.KINDS` is in exactly one of two sets.
:data:`HANDLERS` names the kinds the watcher acts on, either with a *model wake* (an
Orchestrator Session started through the runs API with a prompt from ``wakes/<kind>.md``) or
with a *function without a model*. :data:`UNWATCHED_KINDS` names every other kind: clients,
the briefing and the away summary read them, the watcher does not. ``test_event_ownership``
asserts the two sets cover ``KINDS`` with no overlap, so adding an event kind forces the
decision (amendment A5.4).

The map is data. A no-model handler is the dotted path
``research_core.watcher.<module>:<function>``, imported only when an event of that kind is
handled, so this module imports no step module and the map can land before every step exists.
The function signature is ``handle(ctx: TickContext, event: Event, store: Store) -> None``,
where ``store`` is the store the event lives in; returning acknowledges the event, raising
records the error and leaves the event for the next tick.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass

WAKE_KINDS = ("setup", "review", "management", "anti_stall")
HANDLER_PACKAGE = "research_core.watcher"
# The step modules a no-model handler may name. The ownership test checks a handler's module
# against this set instead of importing it, so a step still being built does not fail the test.
STEP_MODULES = frozenset(
    {
        "champion_sync",
        "checks",
        "context_panel",
        "dispatch",
        "drain",
        "goals",
        "heartbeat",
        "merge",
        "messages",
        "recovery",
        "resume",
        "store_checks",
        "waits",
        "wakes",
    }
)


@dataclass(frozen=True)
class Handler:
    """How one event kind is handled: by a function (``handler``) or a model wake (``wake``)."""

    kind: str
    handler: str | None
    wake: str | None
    model: bool

    @property
    def module_name(self) -> str | None:
        return None if self.handler is None else self.handler.partition(":")[0]

    @property
    def function_name(self) -> str | None:
        return None if self.handler is None else self.handler.partition(":")[2]

    def resolve(self) -> Callable[..., None]:
        """Import and return the handler function; a model wake has none to return."""
        if self.handler is None:
            raise ValueError(f"{self.kind} is handled by a {self.wake} wake, not a function")
        module = importlib.import_module(self.module_name)
        return getattr(module, self.function_name)


def _wake(kind: str, wake: str) -> Handler:
    if wake not in WAKE_KINDS:
        raise ValueError(f"{kind}: not a wake kind: {wake!r}")
    return Handler(kind=kind, handler=None, wake=wake, model=True)


def _function(kind: str, path: str) -> Handler:
    module_name, _, function_name = path.partition(":")
    if not module_name.startswith(HANDLER_PACKAGE + ".") or not function_name:
        raise ValueError(f"{kind}: a handler is {HANDLER_PACKAGE}.<module>:<function>")
    return Handler(kind=kind, handler=path, wake=None, model=False)


HANDLERS: dict[str, Handler] = {
    entry.kind: entry
    for entry in (
        _wake("coworld.setup_needed", "setup"),
        _wake("task.completed", "review"),
        # A rejected review resumes the Researcher with the reason; no model is needed for that.
        _function("task.reviewed", "research_core.watcher.messages:handle_reviewed"),
        _wake("task.asked", "management"),
        _wake("task.proposed", "management"),
        _wake("task.proposed_submission", "management"),
        _wake("merge.failed", "management"),
        # A merged Version the Researcher marked as a submission candidate: the Orchestrator
        # decides under the submissions settings and the quality bar (now met on the merge).
        _wake("submission.candidate", "management"),
        _function("task.wait_resolved", "research_core.watcher.resume:handle_wait_resolved"),
        _function("capability.failed", "research_core.watcher.checks:handle_capability_failed"),
        # After the second failed repair the user hears about it through a notice.
        _function("repair.failed_twice", "research_core.watcher.checks:handle_repair_failed_twice"),
        _wake("goal.needs_ideation", "management"),
        _wake("goal.park", "management"),
        _function("session.dead", "research_core.watcher.recovery:handle_dead"),
        _function("task.stuck", "research_core.watcher.recovery:handle_stuck"),
        _function("workspace.resumed", "research_core.watcher.dispatch:handle_resumed"),
        # Abandoning a Task stops its run.
        _function("task.abandoned", "research_core.watcher.messages:handle_abandoned"),
        # A failed Task feeds the repair counter of its capability.
        _function("task.failed", "research_core.watcher.checks:handle_task_failed"),
        _wake("champion.changed", "management"),
        _wake("store.unmarked_write", "management"),
        _wake("store.schema_rebuilt", "management"),
        # A store a newer release wrote: the watcher paused the workspace and left it alone.
        _wake("store.schema_newer", "management"),
        _wake("recovery.failed", "management"),
        _wake("wake.failed", "management"),
        _wake("anti_stall", "anti_stall"),
    )
}

# Emitted kinds the watcher ignores. Listed by hand rather than computed from KINDS, so a new
# event kind fails the ownership test until someone decides which set it belongs in.
UNWATCHED_KINDS: frozenset[str] = frozenset(
    {
        "goal.created",
        "goal.updated",
        "goal.status",
        "task.created",
        "task.updated",
        "task.approved",
        "task.claimed",
        "task.released",
        "task.waiting",
        "task.answered",
        "task.message",
        "task.reopened",
        "task.merged",
        "claim.created",
        "claim.updated",
        "claim.retired",
        "experiment.created",
        "experiment.updated",
        "experiment.started",
        "experiment.run",
        "experiment.closed",
        "experiment.abandoned",
        "version.created",
        "version.uploaded",
        "version.submitted",
        "directive.added",
        "directive.updated",
        "directive.retired",
        "settings.updated",
        "capability.repaired",
        "intent.pending",
        "intent.done",
        "intent.failed",
        "session.started",
        "session.ended",
        "notice",
        "ui.patch",
        "coworld.registered",
        "coworld.switched",
        "coworld.set_up",
        "platform.request",
        "platform.forum_post",
        "platform.wiki_edit",
        "turn.rendered",
        "watcher.heartbeat",
        "checks.ran",
        "drain.done",
    }
)

MODEL_KINDS: frozenset[str] = frozenset(k for k, entry in HANDLERS.items() if entry.model)
FUNCTION_KINDS: frozenset[str] = frozenset(k for k, entry in HANDLERS.items() if not entry.model)
