"""Map league episodes (``Softmax.league_episodes``) to ``EpisodeRow``s for noise calibration.

Each episode's raw ``results`` is written to a file and read by the game's evaluate binding
(``<evaluate> --results <file> --json``, the same call xp-poll makes). The binding returns one
entry per (episode, seat) with ``metrics``; an entry names its ``seat`` (participant position),
which may be left out only when the episode has a single seat. Each entry becomes one row:

- ``episode_id``: ``<episode id>:<entry episode>:s<seat>``;
- ``policy_version``: the seat's policy label; ``seats``: ``(seat,)``; ``opponents``: the other
  seats' policy labels in position order (a human seat appears as ``human``);
- ``seed``: the entry's, else the episode's; ``game_version``: the episode's, else the
  results', else ``fallback_version`` (the league's current version), stripped of
  surrounding whitespace (versions are dotted numbers);
- ``status``: ``failed`` when the binding says so, else ``completed``.

Entries for a human seat are skipped. These rows feed ``stats ingest --league``.
"""

import json
from pathlib import Path

from ide_research_core.games.bindings import Evaluate
from ide_research_core.stats.ingest import EpisodeRow

HUMAN = "human"



def episode_rows(
    episode: dict, evaluate: Evaluate, workdir: Path, fallback_version: str
) -> list[EpisodeRow]:
    episode_id = episode["episode_id"]
    results_path = Path(workdir) / _safe_name(episode_id) / "results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(episode["results"], sort_keys=True), encoding="utf-8")
    seats = {seat["position"]: seat for seat in episode["seats"]}
    version = str(
        episode.get("game_version")
        or (episode["results"] or {}).get("game_version")
        or fallback_version
    ).strip()
    rows = []
    for index, entry in enumerate(evaluate(results_path)):
        position = entry.get("seat")
        if position is None:
            if len(seats) != 1:
                raise ValueError(
                    f"league episode {episode_id}: the evaluate binding gave no seat for entry "
                    f"{index} of a {len(seats)}-seat episode"
                )
            position = next(iter(seats))
        if position not in seats:
            raise ValueError(f"league episode {episode_id}: unknown seat {position}")
        policy = seats[position]["policy_version_id"]
        if policy is None:
            continue
        seed = entry.get("seed", episode.get("seed"))
        if seed is None:
            raise ValueError(f"league episode {episode_id} has no seed")
        rows.append(
            EpisodeRow(
                episode_id=f"{episode_id}:{entry.get('episode', index)}:s{position}",
                game_version=version,
                policy_version=policy,
                seed=int(seed),
                seats=(position,),
                opponents=tuple(
                    seats[other]["policy_version_id"] or HUMAN
                    for other in sorted(seats)
                    if other != position
                ),
                metrics={name: float(value) for name, value in entry["metrics"].items()},
                status="failed" if entry.get("status") == "failed" else "completed",
            )
        )
    return rows


def _safe_name(episode_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in episode_id)
