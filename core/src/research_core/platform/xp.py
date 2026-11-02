"""Experience request bodies and display titles.

Recovery uses the idempotency key saved with the body in the local intent, never the title.
"""

from __future__ import annotations

TITLE_PREFIX = "ide:"


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
