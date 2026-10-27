#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_fights.py
"""Paintbot PW fights: engagements, trades and opening duels.

Reads pw_episodes tables (never the tape) and segments combat into engagements:

  engagements(ep)     one row per engagement: enemy damage events (a kill is a damage event
                      with killed=True) join one engagement when they are within
                      ENGAGEMENT_JOIN_TICKS of each other AND share a seat or have a
                      participant within ENGAGEMENT_JOIN_DISTANCE (single linkage in
                      space-time). Per engagement: N-vs-M, who hit first, who saw first
                      (needs visibility sampling), kills and HP per team, winner, hearts
                      affected, and time from first sighting to first hit.
  trade_events(kills) one row per death that was traded (killer killed by the victim's team
                      within TRADE_WINDOW_TICKS); same definition as pw_metrics.trades.
  opening_duels(ep)   one row per heart contest (a capture attempt: capture_start .. its
                      complete/reset): the first enemy kill near the heart and whether that
                      team won the contest.
  fight_policy_metrics(ep)  one row per (episode, policy_key): the A/B and miner unit.

Evidence kinds: every event is exact (hash-checked trace); the grouping thresholds are
choices (named constants below, printed with the output); "saw first" is sampled at the
trace's --vis-every; positions for heart distance come from the damage rows (exact).
Contract: paintbot_pw_lab/docs/tools/pw_fights.md.

CLI: uv run python paintbot_pw_lab/tools/pw_fights.py ROOT [ROOT ...] [--policy KEY] [--list] [--csv DIR]
     [--vis-every M] [--tag TAG] [--json]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli  # noqa: E402
import pw_episodes  # noqa: E402
import pw_metrics  # noqa: E402

# ---------------------------------------------------------------- named thresholds
# Calibrated 2026-09-30 on 80 hash-verified main-league episodes at 0.3.79 (episode_data/audit-2026-09-29);
# evidence per threshold in docs/tools/pw_fights.md § Thresholds.

ENGAGEMENT_JOIN_TICKS = 48        # same-pair hit gaps peak at 24-30 (gun cooldown) and 48-60 (one miss); 60-72 is a trough
ENGAGEMENT_JOIN_DISTANCE = 500    # 1,500 linked 56% of concurrent unrelated events (76% of events in 12+ cog blobs); 500: 20%
TRADE_WINDOW_TICKS = pw_metrics.TRADE_WINDOW_TICKS  # 72 ticks = 3 s (one definition, shared)
SIGHTING_LOOKBACK_TICKS = 240     # p95 of sight-to-hit with a 480 lookback is 212; doubling adds 4% known sightings
HEART_AFFECT_RADIUS = 400         # participant-to-heart distance of capture events: dense < 400, flat background beyond
HEART_AFFECT_AFTER_TICKS = 72     # one capture time: a capture completing this soon after was underway during the fight
OPENING_DUEL_RADIUS = 250         # kill victims near an attempted heart: 247 within 250, then ~30 per 250 units (background)
OPENING_DUEL_LEAD_TICKS = 48      # the opening kill may come this long before the capture attempt starts; not calibrated

THRESHOLDS = {name: value for name, value in globals().items() if name.isupper() and isinstance(value, int)}

DRAW = -2


def thresholds_line() -> str:
    return "thresholds: " + ", ".join(f"{k}={v}" for k, v in THRESHOLDS.items())


# ---------------------------------------------------------------- engagements

def _enemy_damage(damage: pd.DataFrame) -> pd.DataFrame:
    rows = damage[damage.seat.notna() & ~damage.friendly.astype(bool) & ~damage["self"].astype(bool)]
    return rows.sort_values(["t", "seat", "victim"]).reset_index(drop=True)


def _linked(a, b) -> bool:
    """Two damage events belong together: shared seat, or participants within the join distance."""
    if {a.seat, a.victim} & {b.seat, b.victim}:
        return True
    points_a = ((a.attacker_x, a.attacker_z), (a.victim_x, a.victim_z))
    points_b = ((b.attacker_x, b.attacker_z), (b.victim_x, b.victim_z))
    return any(math.dist(p, q) <= ENGAGEMENT_JOIN_DISTANCE for p in points_a for q in points_b
               if not (pd.isna(p[0]) or pd.isna(q[0])))


def cluster_events(events: pd.DataFrame) -> list[int]:
    """Engagement label per row (rows sorted by t): single linkage within the time window."""
    parent = list(range(len(events)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    rows = list(events.itertuples(index=False))
    for i, row in enumerate(rows):
        j = i - 1
        while j >= 0 and row.t - rows[j].t <= ENGAGEMENT_JOIN_TICKS:
            if _linked(row, rows[j]):
                parent[find(i)] = find(j)
            j -= 1
    roots = [find(i) for i in range(len(rows))]
    order = {root: n for n, root in enumerate(dict.fromkeys(roots))}
    return [order[root] for root in roots]


def sighting_streak_start(sees_by_tick: dict[int, bool], t_first_hit: int) -> tuple[int | None, bool]:
    """Start of the uninterrupted run of visibility samples, ending at the last sample at or
    before the first hit, in which the team saw an opposing participant.

    Returns (start tick or None if the team did not see one at that last sample, censored),
    where censored means the run reaches back to SIGHTING_LOOKBACK_TICKS (or tick 0), so the
    true start is earlier than we looked."""
    ticks = sorted((t for t in sees_by_tick if t_first_hit - SIGHTING_LOOKBACK_TICKS <= t <= t_first_hit), reverse=True)
    start = None
    for t in ticks:
        if not sees_by_tick[t]:
            return start, False
        start = t
    return start, start is not None


def _first_sighting(visibility: pd.DataFrame, teams: dict[int, list[int]], t_first_hit: int, team_of: dict):
    """(first sighting tick, team that saw first or DRAW) from sampled visibility.

    Each team's sighting is the start of its current streak of seeing an opposing
    participant (sighting_streak_start). The earlier uncensored streak saw first; when both
    streaks are censored (the teams saw each other for the whole lookback, common with an
    unlimited-range cone) it is a DRAW and the sighting tick is None (unknown, not 0)."""
    if not len(visibility):
        return None, None
    window = visibility[(visibility.t >= t_first_hit - SIGHTING_LOOKBACK_TICKS) & (visibility.t <= t_first_hit)]
    sees = {0: {}, 1: {}}
    for row in window.itertuples():
        team = team_of.get(int(row.seat))
        if team is None:
            continue
        t = int(row.t)
        seen = int(row.seat) in teams[team] and any(int(v) in teams[1 - team] for v in row.sees)
        sees[team][t] = sees[team].get(t, False) or seen
    streaks = {team: sighting_streak_start(sees[team], t_first_hit) for team in (0, 1)}
    started = {team: start for team, (start, _) in streaks.items() if start is not None}
    if not started:
        return None, None
    if all(streaks[team][1] for team in started) and len(started) == 2:
        return None, DRAW
    t_min = min(started.values())
    seers = [team for team, t in started.items() if t == t_min]
    censored = any(streaks[team][1] for team in seers)
    return (None if censored else t_min), (seers[0] if len(seers) == 1 else DRAW)


def _hearts_affected(captures: pd.DataFrame, hearts: list[dict], points: list[tuple], t0: int, t1: int) -> list[dict]:
    affected = []
    window = captures[(captures.t >= t0) & (captures.t <= t1 + HEART_AFFECT_AFTER_TICKS) &
                      captures.kind.isin(["capture_start", "capture_complete", "capture_reset", "contest_start"])]
    for row in window.itertuples():
        heart = hearts[int(row.heart)]["pos"]
        if any(math.dist(heart, p) <= HEART_AFFECT_RADIUS for p in points):
            affected.append({"heart": int(row.heart), "kind": row.kind, "t": int(row.t),
                             "team": None if pd.isna(row.team) else int(row.team)})
    return affected


def engagements(ep) -> pd.DataFrame:
    """One row per engagement. Winner: more kills, then more enemy HP removed, else DRAW (-2)."""
    events = _enemy_damage(ep["damage"])
    columns = ["episode_id", "engagement", "t_start", "t_end", "duration_ticks", "events", "x", "z",
               "seats_0", "seats_1", "n_0", "n_1", "first_hit_t", "first_hit_seat", "first_hit_team",
               "kills_0", "kills_1", "hp_0", "hp_1", "winner", "first_sight_t", "saw_first_team",
               "sight_to_hit_ticks", "vision_sampled", "hearts", "heart_events"]
    episode_id = ep["episodes"].iloc[0].episode_id
    if not len(events):
        return pd.DataFrame(columns=columns)
    events = events.assign(engagement=cluster_events(events))
    team_of = ep["seats"].set_index("seat").team.to_dict()
    hearts = ep.meta["hearts"] if hasattr(ep, "meta") else []
    visibility = ep["visibility"]
    captures = ep["captures"]
    rows = []
    for number, group in events.groupby("engagement", sort=True):
        seats = set(group.seat.astype(int)) | set(group.victim.astype(int))
        teams = {0: sorted(s for s in seats if team_of[s] == 0), 1: sorted(s for s in seats if team_of[s] == 1)}
        first = group[group.t == group.t.min()]
        first_teams = set(first.team.astype(int))
        kills = group[group.killed.astype(bool)]
        k = [int((kills.team == team).sum()) for team in (0, 1)]
        hp = [int(group[group.team == team].hp_removed.sum()) for team in (0, 1)]
        if k[0] != k[1]:
            winner = 0 if k[0] > k[1] else 1
        elif hp[0] != hp[1]:
            winner = 0 if hp[0] > hp[1] else 1
        else:
            winner = DRAW
        t_start, t_end = int(group.t.min()), int(group.t.max())
        sight_t, saw_first = _first_sighting(visibility, teams, t_start, team_of)
        points = [(r.attacker_x, r.attacker_z) for r in group.itertuples() if not pd.isna(r.attacker_x)]
        points += [(r.victim_x, r.victim_z) for r in group.itertuples()]
        affected = _hearts_affected(captures, hearts, points, t_start, t_end) if hearts else []
        rows.append({
            "episode_id": episode_id, "engagement": int(number), "t_start": t_start, "t_end": t_end,
            "duration_ticks": t_end - t_start, "events": len(group),
            "x": float(np.mean([p[0] for p in points])), "z": float(np.mean([p[1] for p in points])),
            "seats_0": teams[0], "seats_1": teams[1], "n_0": len(teams[0]), "n_1": len(teams[1]),
            "first_hit_t": t_start, "first_hit_seat": int(first.iloc[0].seat) if len(first) == 1 else None,
            "first_hit_team": first_teams.pop() if len(first_teams) == 1 else DRAW,
            "kills_0": k[0], "kills_1": k[1], "hp_0": hp[0], "hp_1": hp[1], "winner": winner,
            "first_sight_t": sight_t, "saw_first_team": saw_first,
            "sight_to_hit_ticks": (t_start - sight_t) if sight_t is not None else None,
            "vision_sampled": bool(len(visibility)),
            "hearts": sorted({a["heart"] for a in affected}), "heart_events": json.dumps(affected),
        })
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------- trades

def trade_events(kills: pd.DataFrame, window: int = TRADE_WINDOW_TICKS) -> pd.DataFrame:
    """One row per traded death. Same rule as pw_metrics.trades (first avenger in time)."""
    enemy = kills[kills.seat.notna() & ~kills.friendly.astype(bool) & ~kills["self"].astype(bool)].sort_values("t")
    rows = []
    for row in enemy.itertuples():
        avenged = enemy[(enemy.victim == row.seat) & (enemy.t > row.t) & (enemy.t <= row.t + window) &
                        (enemy.team == row.victim_team)]
        if len(avenged):
            revenge = avenged.iloc[0]
            rows.append({"t_death": int(row.t), "victim": int(row.victim), "victim_team": int(row.victim_team),
                         "killer": int(row.seat), "t_trade": int(revenge.t), "avenger": int(revenge.seat),
                         "trade_ticks": int(revenge.t - row.t)})
    return pd.DataFrame(rows, columns=["t_death", "victim", "victim_team", "killer", "t_trade", "avenger", "trade_ticks"])


# ---------------------------------------------------------------- opening duels

def _owner_at(heart_states: pd.DataFrame, heart: int, t: int):
    rows = heart_states[(heart_states.heart == heart) & (heart_states.t <= t)]
    return int(rows.sort_values("t").iloc[-1].owner) if len(rows) else None


def opening_duels(ep) -> pd.DataFrame:
    """One row per heart contest: the first enemy kill near the heart and who won the contest.

    A contest is one capture attempt: a capture_start at a heart, ending at that heart's next
    capture event (capture_complete, capture_reset, or a capture_start by the other team) or
    the match end. The engine's own "contested" state (both teams within 140 units) is rare
    because the gun reaches 5,250 units, so it is not used here (pw_metrics counts it as
    `contests`). Contest winner: the attacker if the attempt completed, else the other team.
    The opening duel is the first enemy kill whose victim is within OPENING_DUEL_RADIUS of the
    heart, from OPENING_DUEL_LEAD_TICKS before the attempt to its end."""
    columns = ["episode_id", "heart", "t_start", "t_end", "attacker", "owner_before", "end_kind", "opening_t",
               "opening_killer", "opening_victim", "opening_team", "contest_winner", "opening_team_won"]
    caps = ep["captures"].sort_values(["t", "kind"])
    kills = ep["kills"]
    enemy_kills = kills[kills.seat.notna() & ~kills.friendly.astype(bool) & ~kills["self"].astype(bool)].sort_values("t")
    episode = ep["episodes"].iloc[0]
    hearts = ep.meta["hearts"]
    ticks = int(episode.ticks)
    attempts = caps[caps.kind.isin(["capture_start", "capture_complete", "capture_reset"])]
    rows = []
    for start in attempts[attempts.kind == "capture_start"].itertuples():
        heart, attacker = int(start.heart), int(start.team)
        later = attempts[(attempts.heart == heart) & (attempts.t >= start.t) & (attempts.index != start.Index)]
        later = later[(later.kind != "capture_start") | (later.team != attacker)]
        end = later.iloc[0] if len(later) else None
        t_end = int(end.t) if end is not None else ticks
        end_kind = end.kind if end is not None else "match_end"
        pos = hearts[heart]["pos"]
        near = enemy_kills[(enemy_kills.t >= start.t - OPENING_DUEL_LEAD_TICKS) & (enemy_kills.t <= t_end)]
        near = near[np.hypot(near.victim_x - pos[0], near.victim_z - pos[1]) <= OPENING_DUEL_RADIUS]
        opening = near.iloc[0] if len(near) else None
        winner = attacker if end_kind == "capture_complete" else 1 - attacker
        opening_team = int(opening.team) if opening is not None else None
        rows.append({
            "episode_id": episode.episode_id, "heart": heart, "t_start": int(start.t), "t_end": t_end,
            "attacker": attacker, "owner_before": _owner_at(ep["heart_states"], heart, int(start.t)),
            "end_kind": end_kind, "opening_t": int(opening.t) if opening is not None else None,
            "opening_killer": int(opening.seat) if opening is not None else None,
            "opening_victim": int(opening.victim) if opening is not None else None,
            "opening_team": opening_team, "contest_winner": winner,
            "opening_team_won": (opening_team == winner) if opening_team is not None else None,
        })
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------- policy level

FIGHT_METRICS = {
    "engagements": "engagements with >= 1 of the policy's seats",
    "engagements_won": "of those, won by the policy's team (kills, then HP)",
    "engagements_drawn": "of those, drawn",
    "engagements_first_hit": "of those, the policy's team hit first (alone on that tick)",
    "won_when_first_hit": "won among first-hit engagements",
    "engagements_outnumbered": "our participants < theirs",
    "won_when_outnumbered": "won among outnumbered engagements",
    "engagements_outnumbering": "our participants > theirs",
    "engagements_saw_first": "our team saw first (null without visibility sampling)",
    "sight_to_hit_ticks_median": "median ticks from first sighting to first hit (sampled; null without visibility)",
    "opening_duels": "heart contests (capture attempts) with an opening kill near the heart",
    "opening_duels_won": "opening kills by the policy's team",
    "contests_won_after_opening_win": "contests won after winning the opening duel",
    "trade_kills": "kills of a teammate's killer within TRADE_WINDOW_TICKS",
    "deaths_traded": "our deaths avenged within TRADE_WINDOW_TICKS",
}


def fight_policy_metrics(ep, fights: pd.DataFrame | None = None, duels: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per (episode, policy_key). A policy on both teams (mirror) gets null outcomes."""
    fights = engagements(ep) if fights is None else fights
    duels = opening_duels(ep) if duels is None else duels
    seats = ep["seats"]
    trades = trade_events(ep["kills"])
    rows = []
    for key, group in seats.groupby("policy_key", sort=True):
        teams = sorted(group.team.unique())
        team = int(teams[0]) if len(teams) == 1 else None
        own = set(group.seat.astype(int))
        row = {"episode_id": ep["episodes"].iloc[0].episode_id, "policy_key": key,
               "policy_name": group.policy_name.iloc[0], "team": team}
        mine = fights[[bool(own & set(a) | own & set(b)) for a, b in zip(fights.seats_0, fights.seats_1)]] \
            if len(fights) else fights
        row["engagements"] = len(mine)
        row["trade_kills"] = int(trades.avenger.isin(own).sum())
        row["deaths_traded"] = int(trades.victim.isin(own).sum())
        if team is None:
            rows.append(row)
            continue
        ours = mine[f"n_{team}"] if len(mine) else pd.Series(dtype=int)
        theirs = mine[f"n_{1 - team}"] if len(mine) else pd.Series(dtype=int)
        won = mine.winner == team if len(mine) else pd.Series(dtype=bool)
        first = mine.first_hit_team == team if len(mine) else pd.Series(dtype=bool)
        outnumbered = ours < theirs
        row.update({
            "engagements_won": int(won.sum()), "engagements_drawn": int((mine.winner == DRAW).sum()) if len(mine) else 0,
            "engagements_first_hit": int(first.sum()), "won_when_first_hit": int((won & first).sum()),
            "engagements_outnumbered": int(outnumbered.sum()), "won_when_outnumbered": int((won & outnumbered).sum()),
            "engagements_outnumbering": int((ours > theirs).sum()),
        })
        sampled = len(mine) and bool(mine.vision_sampled.iloc[0])
        row["engagements_saw_first"] = int((mine.saw_first_team == team).sum()) if sampled else None
        gaps = mine.sight_to_hit_ticks.dropna() if sampled else pd.Series(dtype=float)
        row["sight_to_hit_ticks_median"] = float(gaps.median()) if len(gaps) else None
        with_opening = duels[duels.opening_team.notna()] if len(duels) else duels
        row["opening_duels"] = len(with_opening)
        row["opening_duels_won"] = int((with_opening.opening_team == team).sum()) if len(with_opening) else 0
        row["contests_won_after_opening_win"] = int(((with_opening.opening_team == team) &
                                                     (with_opening.contest_winner == team)).sum()) if len(with_opening) else 0
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- CLI

def build_parser() -> pw_cli.ArgumentParser:
    parser = pw_cli.ArgumentParser("pw_fights", __doc__, examples=[
        "uv run python paintbot_pw_lab/tools/pw_fights.py paintbot_pw_lab/episode_data/20260928T214433_* --json",
        "uv run python paintbot_pw_lab/tools/pw_fights.py ROOT --policy james-pw --vis-every 24 --csv /tmp/fights"])
    parser.add_argument("roots", nargs="+", type=Path, help="episode dirs, batch dirs or NAME.replay files")
    parser.add_argument("--policy", help="only summarize this policy_key or policy_name (exit 2 if absent)")
    parser.add_argument("--list", action="store_true", help="print every engagement and contest")
    parser.add_argument("--csv", type=Path, help="write engagements/duels/trades/fight_policy CSVs into this directory")
    parser.add_argument("--vis-every", type=int, default=0,
                        help="trace visibility every M ticks, e.g. 24; needed for saw-first (traced once into its own "
                             "cache variant, the default cache is untouched)")
    parser.add_argument("--tag", help="pw_trace build (default: tools/release.env)")
    return parser


def run_cli(args, report: pw_cli.Report) -> dict:
    batch = pw_episodes.load_batch(args.roots, tag=args.tag, options=pw_episodes.TraceOptions(vis_every=args.vis_every))
    report.add_batch(batch)
    pw_cli.require_policy(batch.episodes, args.policy)
    print(thresholds_line())
    all_fights, all_duels, all_trades, all_policy = [], [], [], []
    for ep in sorted(batch.episodes, key=lambda e: e.episode_id):
        fights, duels = engagements(ep), opening_duels(ep)
        trades = trade_events(ep["kills"]).assign(episode_id=ep.episode_id)
        policy = fight_policy_metrics(ep, fights, duels)
        all_fights.append(fights), all_duels.append(duels), all_trades.append(trades), all_policy.append(policy)
        sizes = (fights.n_0 + fights.n_1) if len(fights) else pd.Series(dtype=int)
        vision = "vision sampled" if len(ep["visibility"]) else "no visibility (saw-first null; use --vis-every)"
        print(f"\n{ep.episode_id}  {len(fights)} engagements (median {sizes.median() if len(sizes) else 0:.0f} "
              f"cogs, longest {fights.duration_ticks.max() if len(fights) else 0} ticks), {len(duels)} contests, "
              f"{len(trades)} trades; {vision}")
        for p in policy.itertuples():
            if args.policy and args.policy not in (p.policy_key, p.policy_name):
                continue
            if pd.isna(p.team):
                print(f"  {p.policy_name}: mirror (both teams), {p.engagements} engagements")
                continue
            print(f"  {p.policy_name} ({pw_episodes.TEAM_NAMES[int(p.team)]}): won {p.engagements_won}/{p.engagements} "
                  f"engagements, first hit {p.engagements_first_hit} (won {p.won_when_first_hit}), outnumbered "
                  f"{p.engagements_outnumbered} (won {p.won_when_outnumbered}), opening duels "
                  f"{p.opening_duels_won}/{p.opening_duels} (contest then won {p.contests_won_after_opening_win}), "
                  f"trades {p.trade_kills}")
        if args.list:
            print(fights.drop(columns=["episode_id", "heart_events"]).to_string(index=False))
            print(duels.drop(columns=["episode_id"]).to_string(index=False))
    print(f"\n{len(batch.episodes)} episodes, {len(batch.failures)} failed")
    for path, code, message in batch.failures:
        print(f"FAILED [{code}] {path}: {message}")
    if args.csv and all_fights:
        args.csv.mkdir(parents=True, exist_ok=True)
        for name, frames in (("engagements", all_fights), ("opening_duels", all_duels), ("trades", all_trades),
                             ("fight_policy", all_policy)):
            pd.concat(frames, ignore_index=True).to_csv(args.csv / f"{name}.csv", index=False)
            report.output(args.csv / f"{name}.csv")
        print(f"wrote {args.csv}/{{engagements,opening_duels,trades,fight_policy}}.csv")
    policy = pd.concat(all_policy, ignore_index=True) if all_policy else pd.DataFrame()
    if args.policy and len(policy):
        policy = policy[(policy.policy_key == args.policy) | (policy.policy_name == args.policy)]
    return {"thresholds": THRESHOLDS, "fight_policy": pw_cli.records(policy),
            "engagements": sum(len(f) for f in all_fights), "opening_duels": sum(len(d) for d in all_duels),
            "trades": sum(len(t) for t in all_trades),
            "rows": "use --csv DIR for engagement/duel/trade rows"}


def main(argv: list[str] | None = None) -> int:
    return pw_cli.run(build_parser(), run_cli, argv)


if __name__ == "__main__":
    sys.exit(main())
