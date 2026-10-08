"""Card markers: the single source of the keys runtime card requests carry.

A runtime card request carries its marker in ``why``, in brackets (``routing.request_card``).
The marker is what dedupes the request across runs and crashes, and what lets different
runtime jobs (rebuild, queue-keeper, xp-poll) agree that a card for a step already exists.
Every runtime job builds markers here, never by hand.

- ``launch:<E>:<attempt>``: the XP launch about to be made; ``attempt`` is
  ``lifecycle.launch_attempts + 1`` (C12). The card runs ``xp recover`` first, so a repeated
  launch never duplicates an XP request.
- ``launch:<E>:<attempt>:r<denials>``: xp-poll's relaunch of an ``awaiting-credits``
  experiment after its ``denials``-th consecutive credit denial (A10). A denial does not
  count as a launch, so the relaunch reuses the attempt (creating only the missing requests);
  the denial count keeps each relaunch's marker distinct.
- ``review:<E>``: review a completed evaluation. No epoch: the open-request coverage check
  prevents a second card while one is open.
- ``build:<E>@<epoch>``, ``upload:<E>@<epoch>``, ``promote:<E>@<epoch>``: lifecycle steps
  that only a board rebuild requests; the epoch lets a later rebuild ask again.
- ``calibrate:<game_version>``: the C10 calibration card.
- ``planning:<YYYY-MM-DD-HH>`` (cadence slot start), ``planning:growth:<YYYY-MM-DD>`` (queue
  growth signal), ``planning:rebuild:<epoch>`` (after a board rebuild).
"""

import datetime


def launch_marker(experiment: str, next_attempt: int) -> str:
    return f"launch:{experiment}:{next_attempt}"


def relaunch_marker(experiment: str, attempt: int, denials: int) -> str:
    return f"launch:{experiment}:{attempt}:r{denials}"


def next_launch_attempt(experiment: dict) -> int:
    """The attempt number the next launch of ``experiment`` (its record) will use."""
    return int((experiment.get("lifecycle") or {}).get("launch_attempts", 0)) + 1


def review_marker(experiment: str) -> str:
    return f"review:{experiment}"


def build_marker(experiment: str, epoch: str) -> str:
    return f"build:{experiment}@{epoch}"


def upload_marker(experiment: str, epoch: str) -> str:
    return f"upload:{experiment}@{epoch}"


def promote_marker(experiment: str, epoch: str) -> str:
    return f"promote:{experiment}@{epoch}"


def calibrate_marker(game_version: str) -> str:
    return f"calibrate:{game_version}"


def planning_marker(slot_start: datetime.datetime) -> str:
    return f"planning:{slot_start.astimezone(datetime.UTC):%Y-%m-%d-%H}"


def planning_growth_marker(now: datetime.datetime) -> str:
    return f"planning:growth:{now.astimezone(datetime.UTC):%Y-%m-%d}"


def planning_rebuild_marker(epoch: str) -> str:
    return f"planning:rebuild:{epoch}"
