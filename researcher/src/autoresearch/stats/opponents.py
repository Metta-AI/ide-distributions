"""League-like opponent line-ups for paired XP evaluations (amendment A12).

An evaluation should face the opponents the league would give us, so ``stats design`` samples
them from the current league when the design request asks for ``opponent_count`` opponents.

League snapshot. A Player credential cannot list other players' league memberships (``coworld
memberships --league L`` returns only our own, verified live 2026-10-09), so the snapshot is
built from what it can read: recent completed league episodes (``Softmax.league_episodes``:
every seat's policy and player) and the standings (``Softmax.standings``: each player's
division and rank).

Weights ("roughly in proportion to how often we'd meet them"):

- When some of the episodes include us (``basis: episodes_with_us``), a policy's weight is the
  number of seats it filled in those episodes: that is how often we actually met it.
- Otherwise (``basis: league_episodes``, for example before our first league round), it is the
  number of seats it filled in all the episodes read, times ``OUT_OF_DIVISION_WEIGHT`` when the
  standings place its player outside our division (only when we have a division).

Our own seats (our player, or our champion policy) and filler seats are never opponents.

Line-ups. Each seed gets its own line-up of ``count`` policies, drawn by weight without
replacement while distinct policies remain (a line-up longer than the pool repeats policies
only after using every one). The draw is seeded by ``(salt, seed)``, so freezing the same
request again gives the same line-ups. Both arms of a seed's pair use that seed's line-up
(``softmax_tool.cli._roster``); different seeds get different samples.

How many opponents a line-up has, and which seats they fill, is game knowledge: the agent reads
it in the game's ``GAME.md`` (``## Seats and opponents``) and passes ``opponent_count`` and
``seats``. This module never names a game.
"""

import random
from collections import Counter
from collections.abc import Iterable

from autoresearch.stats.design import DesignError

OUT_OF_DIVISION_WEIGHT = 0.1
DEFAULT_EPISODES = 100  # league episodes read per snapshot (each costs two CLI calls)


def league_snapshot(
    episodes: Iterable[dict],
    standings: list[dict],
    league_id: str,
    our_player: str,
    our_policy: str | None,
    taken_at: str,
) -> dict:
    """The league as the sample saw it (module docstring)::

        {league_id, taken_at, basis, episodes_read, our_division,
         entries: [{policy, player_id, division_id, appearances, weight}],
         standings: [{player_id, division_id, rank}]}
    """
    episodes = list(episodes)

    def ours(seat: dict) -> bool:
        return seat.get("player_id") == our_player or (
            our_policy is not None and seat.get("policy_version_id") == our_policy
        )

    with_us = [e for e in episodes if any(ours(seat) for seat in e["seats"])]
    divisions = {row["player_id"]: row["division_id"] for row in standings}
    our_division = divisions.get(our_player)
    appearances: Counter = Counter()
    players: dict[str, str | None] = {}
    for episode in with_us or episodes:
        for seat in episode["seats"]:
            policy = seat.get("policy_version_id")
            if not policy or seat.get("is_filler") or ours(seat):
                continue
            appearances[policy] += 1
            players[policy] = seat.get("player_id")
    entries = []
    for policy, count in appearances.items():
        division = divisions.get(players[policy])
        far = not with_us and our_division is not None and division != our_division
        entries.append(
            {
                "policy": policy,
                "player_id": players[policy],
                "division_id": division,
                "appearances": count,
                "weight": count * (OUT_OF_DIVISION_WEIGHT if far else 1.0),
            }
        )
    entries.sort(key=lambda entry: (-entry["weight"], entry["policy"]))
    return {
        "league_id": league_id,
        "taken_at": taken_at,
        "basis": "episodes_with_us" if with_us else "league_episodes",
        "episodes_read": len(episodes),
        "our_division": our_division,
        "entries": entries,
        "standings": [
            {"player_id": row["player_id"], "division_id": row["division_id"], "rank": row["rank"]}
            for row in standings
        ],
    }


def _draw(rng: random.Random, pool: list[tuple[str, float]], count: int) -> list[str]:
    lineup: list[str] = []
    remaining: list[tuple[str, float]] = []
    while len(lineup) < count:
        if not remaining:
            remaining = list(pool)
        weights = [weight for _, weight in remaining]
        [choice] = rng.choices(range(len(remaining)), weights=weights)
        lineup.append(remaining.pop(choice)[0])
    return lineup


def sample_lineups(
    snapshot: dict, seeds: Iterable[int], count: int, salt: str
) -> dict[str, list[str]]:
    """One weighted line-up of ``count`` opponent policies per seed (module docstring), keyed
    by the seed as a string (JSON object keys)."""
    if count < 1:
        raise DesignError(f"opponent_count must be at least 1, got {count}")
    pool = [(entry["policy"], float(entry["weight"])) for entry in snapshot["entries"]]
    if not pool:
        raise DesignError(
            f"the {snapshot['league_id']} league snapshot has no opponent policies to sample "
            f"({snapshot['episodes_read']} episodes read)"
        )
    return {str(seed): _draw(random.Random(f"{salt}:{seed}"), pool, count) for seed in seeds}
