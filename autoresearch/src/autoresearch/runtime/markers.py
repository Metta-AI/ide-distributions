"""Card markers: the single source of the keys runtime card requests carry.

A runtime card request carries its marker in ``why``, in brackets (``routing.request_card``).
The marker is what dedupes the request across runs and crashes, and what lets different
runtime jobs (rebuild, queue-keeper, xp-poll) agree that a card for a step already exists.
Every runtime job builds markers here, never by hand.

- ``launch:<E>:<attempt>``: the XP launch about to be made; ``attempt`` is
  ``lifecycle.launch_attempts + 1`` (C12). The card runs ``xp recover`` first, so a repeated
  launch never duplicates an XP request.
- ``launch:<E>:<attempt>:after:<next_launch_after>``: xp-poll's relaunch of an
  ``awaiting-credits`` experiment (A10). A denial does not count as a launch, so the relaunch
  reuses the attempt (creating only the missing requests). The key is the denial itself:
  ``lifecycle.next_launch_after`` is set anew by every denial, so each denial gets exactly one
  relaunch. (``credit_denials`` would not do: it resets after a successful batch, so a later
  denial would repeat an old marker and never be relaunched.)
- ``review:<E>:n<count>``: review a completed evaluation; ``count`` is the number of entries
  in the experiment's ``xp_requests``, so a later batch gets its own review. The open-request
  coverage check still prevents a second card while one is open.
- ``build:<E>@<epoch>``, ``upload:<E>@<epoch>``, ``promote:<E>@<epoch>``: lifecycle steps
  that only a board rebuild requests; the epoch lets a later rebuild ask again.
- ``calibrate:<game_version>``: the C10 calibration card.
- ``repair-blocked:<task-id>``: the repair card for a card Hermes blocked twice
  (``blocked.handle_blocked``).
- ``planning:<YYYY-MM-DD-HH>`` (cadence slot start), ``planning:growth:<YYYY-MM-DD>`` (queue
  growth signal), ``planning:rebuild:<epoch>`` (after a board rebuild).
"""

import datetime
from pathlib import Path

from autoresearch.runtime.ledger import ledger_of


def launch_marker(experiment: str, next_attempt: int) -> str:
    return f"launch:{experiment}:{next_attempt}"


def relaunch_marker(experiment: str, attempt: int, next_launch_after: str) -> str:
    return f"launch:{experiment}:{attempt}:after:{next_launch_after}"


def next_launch_attempt(experiment: dict) -> int:
    """The attempt number the next launch of ``experiment`` (its record) will use."""
    return int((experiment.get("lifecycle") or {}).get("launch_attempts", 0)) + 1


def review_marker(experiment: dict) -> str:
    """The review marker of ``experiment`` (its record) for its current XP requests."""
    return f"review:{experiment['id']}:n{len(experiment.get('xp_requests') or [])}"


def build_marker(experiment: str, epoch: str) -> str:
    return f"build:{experiment}@{epoch}"


def upload_marker(experiment: str, epoch: str) -> str:
    return f"upload:{experiment}@{epoch}"


def promote_marker(experiment: str, epoch: str) -> str:
    return f"promote:{experiment}@{epoch}"


def calibrate_marker(game_version: str) -> str:
    return f"calibrate:{game_version}"


def repair_blocked_marker(task_id: str) -> str:
    return f"repair-blocked:{task_id}"


# Repairs of a blocked card's repairs (``blocked.handle_blocked``): a card this many repairs
# deep gets a help request instead of another repair. ``publisher`` follows the chain this far.
MAX_REPAIR_CHAIN = 2


def blocked_marker_path(canonical: Path, task_id: str) -> Path:
    """The committed health marker of a blocked card (``blocked.handle_blocked``): its
    ``state`` (requeued, repaired, tool-fault) and, once repaired, its ``repair_request``."""
    return ledger_of(canonical) / "health" / "blocked" / f"{task_id}.json"


def planning_marker(slot_start: datetime.datetime) -> str:
    return f"planning:{slot_start.astimezone(datetime.UTC):%Y-%m-%d-%H}"


def planning_growth_marker(now: datetime.datetime) -> str:
    return f"planning:growth:{now.astimezone(datetime.UTC):%Y-%m-%d}"


def planning_rebuild_marker(epoch: str) -> str:
    return f"planning:rebuild:{epoch}"
