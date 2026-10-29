"""Experience (XP) requests: the create body, the title that identifies one, and the lookup.

One XP request plays one Version for a number of episodes on one seed; an Experiment's
evaluation is a set of them, one per (seed, arm). The title ``ide:X-<n>:<seed>:<arm>`` names
the Experiment, the seed and the arm, so a request the platform accepted but this side never
recorded (a crash between the create and the store write, B.13) is found again by listing the
Player's requests: :func:`find_by_title` is the reconciliation the ``xp`` tool runs before any
retry. Stdlib only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from research_core.platform.protocol import Platform, XpRequest

TITLE_PREFIX = "ide:"
# How many of the Player's newest requests a reconciliation looks through.
RECONCILE_LIMIT = 200


def title_for(experiment_n: int, seed: int, arm: str) -> str:
    """``ide:X-<n>:<seed>:<arm>``, the title an Experiment's request carries (A4.4)."""
    return f"{TITLE_PREFIX}X-{experiment_n}:{seed}:{arm}"


def request_body(
    *,
    coworld_id: str,
    policy_version_id: str,
    seed: int | None,
    episodes: int,
    title: str,
    league_id: str | None = None,
    opponents: list[str] | None = None,
) -> dict:
    """The ``POST /v2/experience-requests`` body (the V2 create shape, reads/1.md §2.10).

    Our policy sits in slot 0; each opponent policy version fills the next slot. A league
    makes the request's ``target``, so the platform picks the other seats from it.
    ``game_config_overrides.seed`` pins the seed when one is given.
    """
    roster = [{"player": {"policy_ref": policy_version_id}, "slot": 0}]
    for slot, opponent in enumerate(opponents or (), start=1):
        roster.append({"player": {"policy_ref": opponent}, "slot": slot})
    body: dict = {
        "coworld_id": coworld_id,
        "roster": roster,
        "num_episodes": episodes,
        "title": title,
    }
    if league_id is not None:
        body["target"] = {"league_id": league_id}
    if seed is not None:
        body["game_config_overrides"] = {"seed": seed}
    return body


def find_by_title(platform: Platform, title: str) -> XpRequest | None:
    """The Player's newest request carrying ``title``, or ``None``."""
    for request in platform.xp_list(mine=True, limit=RECONCILE_LIMIT):
        if request.title == title:
            return request
    return None
