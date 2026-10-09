"""Start on request (``AUTORESEARCH_AUTONOMY``).

``always`` (the default): the agent plans its own research. ``on-request``: on hosted Softmax
any user can click Start, so the agent plans nothing (cadence, queue growth, board rebuild)
until the coach's first task arrives. That task, an inbound ``task`` whose ``author`` starts
with ``coach:``, commits ``ledger/started.json`` ``{"started_at": ISO, "by": "<inbound id>"}``;
the marker is permanent. A coach ``steer`` (what cancelling a task produces) does not start the
agent, and neither does a task from anyone else.

This is a convenience, not a security control: everything in the sandbox runs as one uid, so
any process there can write an inbound task with a ``coach:`` author (README, "Start on request").
"""

from collections.abc import Mapping
from pathlib import Path

from autoresearch.comms import delivery
from autoresearch.io.docs import write_json
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso

ENV_AUTONOMY = "AUTORESEARCH_AUTONOMY"
ALWAYS = "always"
ON_REQUEST = "on-request"
STARTED_FILE = "started.json"
COACH_AUTHOR_PREFIX = "coach:"


def autonomy_mode(env: Mapping[str, str]) -> tuple[str, str | None]:
    """(mode, problem): ``on-request`` or ``always``. An unknown value runs as ``always`` and
    comes back as the problem, which queue-keeper reports once."""
    raw = (env.get(ENV_AUTONOMY) or "").strip()
    if raw in ("", ALWAYS):
        return ALWAYS, None
    if raw == ON_REQUEST:
        return ON_REQUEST, None
    return ALWAYS, f"unknown value {raw!r} (expected {ALWAYS!r} or {ON_REQUEST!r})"


def started_path(canonical: Path) -> Path:
    return ledger_of(canonical) / STARTED_FILE


def is_started(canonical: Path) -> bool:
    """True once the coach's first task has started the agent (as committed at HEAD)."""
    return committed_json(canonical, started_path(canonical)) is not None


def start_on_first_coach_task(canonical: Path) -> str | None:
    """Commit ``started.json`` for the first coach task in the inbox; return that task's id,
    or None when there is none or the agent is already started. Idempotent. Takes the
    canonical lock: the caller must not hold it."""
    with canonical_lock(canonical):
        if is_started(canonical):
            return None
        for inbound_id, message in delivery.inbox(canonical).items():
            if message.get("kind") == "task" and str(message.get("author", "")).startswith(
                COACH_AUTHOR_PREFIX
            ):
                path = started_path(canonical)
                write_json(path, {"started_at": now_iso(), "by": inbound_id})
                commit(canonical, [path], f"started: first coach task {inbound_id}")
                return inbound_id
    return None


def may_plan(canonical: Path, mode: str) -> bool:
    """Whether the agent may plan its own research now. Every condition that stops planning
    belongs here."""
    return mode == ALWAYS or is_started(canonical)
