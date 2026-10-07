#!/usr/bin/env python3
# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/compare.py
"""Gods of the Arena A/B adapter for the shared `coworld-ab` engine.

Diffs a BASELINE and a CANDIDATE policy version across two matched, fresh, same-window
experience requests and delegates statistics, verdicts, and rendering to `ab_stats`.

Unit and grouping. The class per seat is fixed (seat 0 is always the Death Knight, seat
5 always the Vanguard Knight). Current evaluations use one subject seat per episode.
The unit is one observation per EPISODE per GROUP: seats of the same group in
one episode are averaged before any test, so a game is never counted twice in a group.
`--group` picks the split: `class` (ten groups, the honest split, needs large batches),
`role` (the five farm-priority tiers of docs/roles.md), `team` (red/blue), or `all`
(one group, the coarse "did it move overall" view). Under the coarser splits a rate
metric becomes the within-episode fraction of the policy's seats and is tested as a mean.

Sources. `win` and `total_xp` come from results.json for every seat. `level` comes from
the game log's `heroes:` line for every seat. Last hits, hero kills, tower kills and
deaths use verified replay counts when --baseline-replay-stats and
--candidate-replay-stats are supplied; missing replay coverage stays missing in
that mode. Without those arguments they use inferred `LH` telemetry.

Team XP leadership requires strictly more XP than all four teammates; a tie does
not count. `win_team_xp_lead_rate` measures that lead AND a team win over all games.
`team_xp_rank` uses competition ranking (ties share rank), while `team_xp_margin`
subtracts the highest teammate XP. Incomplete teammate evidence is excluded.
`winning_xp_mean` is raw XP times the win indicator, averaged over all games; it
is a diagnostic, not a claim about the platform's cumulative reward formula.

Caveat on win rate. It is a team outcome affected by the nine other seats. Historical
multi-copy episodes must not be treated as independent hero-match observations.

Usage:
  uv run python gods_of_the_arena_lab/tools/compare.py BASE_DIR CAND_DIR \
      --baseline james-botts-gota:v1 --candidate james-botts-gota:v2 \
      --target last_hits_per_1k_ticks [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gota_episodes import (
    CLASS_NAMES,
    ROLE_NAMES,
    ROLE_OF_CLASS,
    EpisodeRecord,
    Seat,
    load_batch,
    own_seats,
    parse_spec,
)

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ab_stats

# (key, higher_is_better, kind, applies_to_group). Class-grouped metrics have group None.
METRICS = [
    ("win_team_xp_lead_rate", True, "rate", None),
    ("win_rate", True, "rate", None),
    ("team_xp_lead_rate", True, "rate", None),
    ("team_xp_rank", False, "mean", None),
    ("team_xp_margin", True, "mean", None),
    ("team_total_xp", True, "mean", None),
    ("winning_xp_mean", True, "mean", None),
    ("xp_mean", True, "mean", None),
    ("xp_per_1k_ticks", True, "mean", None),
    ("level_mean", True, "mean", None),
    ("last_hits_mean", True, "mean", None),
    ("last_hits_per_1k_ticks", True, "mean", None),
    ("no_last_hit_rate", False, "rate", None),
    ("hero_kills_mean", True, "mean", None),
    ("nearby_death_share", True, "mean", None),
    ("nearby_kill_share", True, "mean", None),
    ("tower_kills_mean", True, "mean", None),
    ("deaths_mean", False, "mean", None),
    ("vm_error_rate", False, "rate", None),
    ("ops_fail_rate", False, "rate", "episodes"),
    ("draw_rate", False, "rate", "episodes"),
    # Game length: for a policy that loses quickly, a longer game means its team held
    # out longer. Reread this direction once the policy is winning games.
    ("ticks_mean", True, "mean", "episodes"),
]
def metrics_for(grouping: str, single_seat_per_group: bool = False) -> list[tuple]:
    """Seat-level rates stay Fisher-tested rates only when each episode contributes one seat
    per group (`class`); averaged over several seats they are fractions, tested as means."""
    if grouping == "class" or single_seat_per_group:
        return METRICS
    return [(key, hib, "mean" if kind == "rate" and group is None else kind, group)
            for key, hib, kind, group in METRICS]


GROUPINGS = {
    "class": (CLASS_NAMES, lambda seat: seat.hero_class),
    "role": (ROLE_NAMES, lambda seat: ROLE_OF_CLASS[seat.hero_class]),
    "team": (["red", "blue"], lambda seat: seat.team),
    "all": (["all"], lambda seat: "all"),
}


def _per_1k(value: int | None, ticks: int | None) -> float | None:
    if value is None or not ticks:
        return None
    return 1000.0 * value / ticks


def seat_value(seat: Seat, record: EpisodeRecord, key: str) -> float | None:
    """One seat's value for a metric, or None when the source is missing."""
    ticks = record.ticks
    telemetry = seat.combat or seat.telemetry
    if key == "winning_xp_mean":
        return None if seat.total_xp is None else float(seat.total_xp if seat.won else 0)
    if key in {"win_team_xp_lead_rate", "team_xp_lead_rate", "team_xp_rank", "team_xp_margin", "team_total_xp"}:
        teammates = [other for other in record.seats
                     if other.team == seat.team and other.position != seat.position]
        if (seat.total_xp is None or len(teammates) != 4
                or len({other.position for other in teammates}) != 4
                or any(other.total_xp is None for other in teammates)):
            return None
        teammate_xp = [other.total_xp for other in teammates]
        if key == "team_total_xp":
            return float(seat.total_xp + sum(teammate_xp))
        margin = seat.total_xp - max(teammate_xp)
        if key == "team_xp_margin":
            return float(margin)
        if key == "team_xp_rank":
            return float(1 + sum(xp > seat.total_xp for xp in teammate_xp))
        if key == "team_xp_lead_rate":
            return float(margin > 0)
        return float(seat.won and margin > 0)
    if key == "win_rate":
        return float(seat.won)
    if key == "xp_mean":
        return None if seat.total_xp is None else float(seat.total_xp)
    if key == "xp_per_1k_ticks":
        return _per_1k(seat.total_xp, ticks)
    if key == "level_mean":
        return None if seat.level is None else float(seat.level)
    if key == "vm_error_rate":
        return None if seat.vm_error is None else float(seat.vm_error)
    if key in {"nearby_death_share", "nearby_kill_share"}:
        return None if seat.combat is None else getattr(seat.combat, key)
    if telemetry is None:
        return None
    if key == "last_hits_mean":
        return float(telemetry.last_hits)
    if key == "last_hits_per_1k_ticks":
        return _per_1k(telemetry.last_hits, ticks)
    if key == "no_last_hit_rate":
        return float(telemetry.last_hits == 0)
    if key == "hero_kills_mean":
        return float(telemetry.hero_kills)
    if key == "tower_kills_mean":
        return float(telemetry.tower_kills)
    if key == "deaths_mean":
        return float(telemetry.deaths)
    return None


def load_arm(root: Path, policy: str, version: int, grouping: str,
             replay_stats: Path | None = None) -> tuple[dict[str, list], dict]:
    """Group one arm's seats; each group row is (seats of one episode, episode record).
    Episode-level rows go under "episodes"."""
    group_names, key_of = GROUPINGS[grouping]
    records, excluded = load_batch(root, replay_stats)
    if replay_stats is not None:
        # Exact mode never silently mixes inferred telemetry with verified counts.
        for record in records:
            for seat in record.seats:
                if seat.combat is None:
                    seat.telemetry = None
    groups: dict[str, list] = {name: [] for name in group_names}
    groups["episodes"] = []
    for record in records:
        seats = own_seats(record, policy, version)
        if not seats:
            excluded["target_absent"] += 1
            continue
        if not record.known:
            excluded["unknown_episode_outcome"] += 1
            continue
        groups["episodes"].append({"episode_id": record.episode_id, "ops_fail": record.ops_fail,
                                   "draw": record.draw, "ticks": record.ticks})
        if record.ops_fail:
            excluded["failed_episode_gameplay"] += 1
            continue
        per_group: dict[str, list[Seat]] = {}
        for seat in seats:
            per_group.setdefault(key_of(seat), []).append(seat)
        for name, group_seats in per_group.items():
            groups[name].append((group_seats, record))
    return groups, dict(excluded)


def value_fn(rows: list, key: str) -> list[float]:
    if key in {"ops_fail_rate", "draw_rate"}:
        return []
    if key == "ticks_mean":
        return [float(r["ticks"]) for r in rows if r.get("ticks")]
    out: list[float] = []
    for seats, record in rows:
        values = [v for v in (seat_value(seat, record, key) for seat in seats) if v is not None]
        if values:
            out.append(statistics.mean(values))
    return out


def metric_value(rows: list, key: str) -> tuple[float, int] | None:
    if not rows:
        return None
    if key == "ops_fail_rate":
        return sum(r["ops_fail"] for r in rows) / len(rows), len(rows)
    if key == "draw_rate":
        return sum(r["draw"] for r in rows) / len(rows), len(rows)
    values = value_fn(rows, key)
    return (statistics.mean(values), len(values)) if values else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("baseline_dir", type=Path, help="Downloaded episodes of the baseline arm.")
    parser.add_argument("candidate_dir", type=Path, help="Downloaded episodes of the candidate arm.")
    parser.add_argument("--baseline", required=True, help="Baseline policy as NAME:vN.")
    parser.add_argument("--candidate", required=True, help="Candidate policy as NAME:vN.")
    parser.add_argument("--target", choices=[m[0] for m in METRICS], default="xp_mean",
                        help="Lead metric: the axis the change was meant to move.")
    parser.add_argument("--group", choices=list(GROUPINGS), default="class",
                        help="How to split the policy's seats (see the module docstring).")
    parser.add_argument("--json", type=Path, help="Also write the structured diff here.")
    parser.add_argument("--baseline-replay-stats", type=Path, help="Verified replay_stats.py JSON for baseline.")
    parser.add_argument("--candidate-replay-stats", type=Path, help="Verified replay_stats.py JSON for candidate.")
    args = parser.parse_args()

    base_name, base_version = parse_spec(args.baseline)
    cand_name, cand_version = parse_spec(args.candidate)
    if base_version is None or cand_version is None:
        parser.error("both policies need an exact version: name:vN")
    base, excluded_base = load_arm(args.baseline_dir, base_name, base_version, args.group,
                                  args.baseline_replay_stats)
    cand, excluded_cand = load_arm(args.candidate_dir, cand_name, cand_version, args.group,
                                  args.candidate_replay_stats)
    if not base["episodes"] or not cand["episodes"]:
        parser.error(f"no known episodes for an arm; baseline {excluded_base}, candidate {excluded_cand}")
    base_ids = {r["episode_id"] for r in base["episodes"]}
    cand_ids = {r["episode_id"] for r in cand["episodes"]}
    if base_ids & cand_ids:
        parser.error("arms share episodes; use a paired analysis for within-episode comparisons")

    groups = [g for g in GROUPINGS[args.group][0] if base[g] or cand[g]]
    single_seat = all(len(seats) == 1 for arm in (base, cand) for group in groups
                      for seats, _ in arm[group])
    metrics = metrics_for(args.group, single_seat)
    deltas = ab_stats.build_deltas(base, cand, metrics, metric_value, value_fn, groups)
    print(f"Unit: one observation per episode per {args.group} group (seats averaged within an episode); "
          "verify matched roster, roles, and window separately.")
    print(f"Excluded baseline: {excluded_base}; candidate: {excluded_cand}")
    print(ab_stats.render_markdown(args.baseline, args.candidate, base, cand,
                                   deltas, args.target, groups, metrics))
    if args.json:
        report = ab_stats.emit_json(args.baseline, args.candidate, args.target, deltas)
        report.update(unit=f"episode per {args.group} group",
                      excluded_baseline=excluded_base, excluded_candidate=excluded_cand)
        args.json.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\n[wrote JSON: {args.json}]")


if __name__ == "__main__":
    main()
