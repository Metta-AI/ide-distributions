#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_metrics.py
"""Paintbot PW metric library: one definition of every metric.

Computed from pw_episodes tables at three levels:
  seat_metrics(ep)    one row per (episode, seat)
  policy_metrics(ep)  one row per (episode, policy_key): the unit for A/B and mining
  team_metrics(ep)    one row per (episode, team)
`METRICS` lists every column with its unit, denominator/definition and evidence kind
(exact = from the hash-checked trace; inferred = a documented heuristic; sampled = from
state rows every `state_every` ticks). A metric is null (NaN/None) when its input is
missing, never 0. Ratios at policy/team level are recomputed from summed numerators and
denominators, never averaged. Contract: paintbot_pw_lab/docs/tools/pw_metrics.md.

CLI: uv run python paintbot_pw_lab/tools/pw_metrics.py ROOT [ROOT ...] [--policy KEY] [--csv DIR]
         [--tag TAG] [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli  # noqa: E402
import pw_episodes  # noqa: E402

# ---------------------------------------------------------------- named thresholds

ELO_MARGIN_SCALE = 1000          # ladder Elo margin_scale (docs/mechanics.md §1)
# Calibrated 2026-09-30 on 80 main-league 0.3.79 episodes (episode_data/audit-2026-09-29); evidence in
# docs/tools/pw_metrics.md § Thresholds.
TRADE_WINDOW_TICKS = 72          # killer's death hazard is ~2x its teammates' for 60-72 ticks, ~1.4x after; 60 vs 72 is noise
DISTANCE_BANDS = ((0, 1000), (1000, 3500), (3500, 5250))  # accuracy ~0.50 / flat ~0.46 / ~0.35; gun range 5,250
SPRAY_BURST_TICKS = 5            # SprayTicks: a burst deals damage on its first 5 ticks
STUCK_MAX_DISPLACEMENT = 10      # units per 6-tick sample: 92% of steps under 10 are exactly 0; slow movers start ~12
STUCK_MIN_GOAL_DISTANCE = 200    # ...while the (unchanged) goal is this far: the density minimum (see pw_flags)
VM_DISABLED_MIN_IDLE_TICKS = 240  # 10 s of empty commands while alive, to the end; league has 0 idle ticks, uncalibrated
PICKUP_KINDS = ("grenade", "spray", "medkit", "armor", "uniform", "mister", "sniper", "radar")
GLORY_KINDS = ("quiet_supplies", "friendly_fire", "glory_heart", "behind_lives", "behind_cogs")
WEAPONS = ("gun", "grenade", "spray", "self_destruct")


def _band_name(band: tuple[int, int]) -> str:
    return f"{band[0]}_{band[1]}"


# name -> (level, unit, definition / denominator, evidence kind)
METRICS: dict[str, tuple[str, str, str, str]] = {
    "elo_outcome": ("policy/team", "score in [0,1]", "clamp(0.5 + (our glory - their glory) / (2 * 1000), 0, 1); null if the policy is on both teams", "exact"),
    "result": ("policy/team", "win|draw|loss", "from the traced winner (meter race); draw = winner -2", "exact"),
    "glory_ours": ("policy/team", "glory", "our team's final (settled) glory", "exact"),
    "glory_theirs": ("policy/team", "glory", "the other team's final glory", "exact"),
    "winning_glory": ("policy/team", "glory", "our glory when we win, else null (a selected sample)", "exact"),
    "glory_initial": ("team", "glory", "glory at tick 0 (match seconds)", "exact"),
    "glory_countdown": ("team", "glory", "total -1/s countdown actually applied", "exact"),
    "glory_unsettled": ("team", "glory", "initial + awards - countdown before settleGlory", "exact"),
    "glory_settled_loss": ("team", "glory", "unsettled - final (what settleGlory zeroed)", "exact"),
    **{f"glory_{k}": ("team", "glory", f"sum of deduped '{k}' awards", "exact") for k in GLORY_KINDS},
    "meter_points": ("team", "points", "final heart meter, meter_ticks / 24", "exact"),
    "ticks": ("episode", "ticks", "match length", "exact"),
    "first_capture_tick": ("team", "tick", "first capture_complete by the team; null if never", "exact"),
    "lead_changes": ("team", "count", "sign changes of the meter difference across state samples", "sampled"),
    "heart_ticks": ("team", "heart-ticks", "sum over hearts of ticks owned (from t=0 owners + capture_complete)", "exact"),
    "hearts_held_mean": ("team", "hearts", "heart_ticks / ticks", "exact"),
    "hearts_held_end": ("team", "hearts", "hearts owned at the end", "exact"),
    "captures_completed": ("seat/team", "count", "heart captures credited (cogs.captures)", "exact"),
    "capture_starts": ("team", "count", "capture_start events", "exact"),
    "capture_resets": ("team", "count", "captures abandoned before completion (capture_reset)", "exact"),
    "contests": ("team", "count", "contest_start events: both teams in reach of one heart, whoever (if anyone) "
                 "was capturing; the same on both team rows", "exact"),
    "longest_supply_gap_ticks": ("team", "ticks", "longest stretch without a team pickup (incl. start and end)", "exact"),
    "cogs_out_end": ("team", "cogs", "cogs out of the match at the end", "exact"),
    "team_lives_end": ("team", "lives", "lives left at the end", "exact"),
    "shots": ("seat", "count", "gun rays fired (observeShot)", "exact"),
    "windups": ("seat", "count", "gun windups started", "exact"),
    "windups_aborted": ("seat", "count", "windups - shots (died mid-windup)", "exact"),
    "gun_hits": ("seat", "count", "rays that damaged a cog", "exact"),
    "gun_hits_enemy": ("seat", "count", "rays that damaged an enemy", "exact"),
    "gun_hits_friendly": ("seat", "count", "rays that damaged a teammate", "exact"),
    "gun_accuracy": ("seat", "ratio", "gun_hits / shots", "exact"),
    "gun_enemy_accuracy": ("seat", "ratio", "gun_hits_enemy / shots", "exact"),
    "shots_inferred_blocked": ("seat", "count", "misses whose ray reached a shielded/just-dead cog or passed a trench cog (not counted as hits)", "inferred"),
    "shots_untargeted": ("seat", "count", "misses with no enemy within 150 units of the aim line", "inferred"),
    **{f"gun_shots_band_{_band_name(b)}": ("seat", "count", f"enemy hits at distance {b} + misses whose aim-line target was at {b}", "inferred") for b in DISTANCE_BANDS},
    **{f"gun_acc_band_{_band_name(b)}": ("seat", "ratio", f"enemy hits / gun_shots_band in {b}", "inferred") for b in DISTANCE_BANDS},
    "spray_bursts": ("seat", "count", "spray bursts", "exact"),
    "spray_bursts_effective": ("seat", "count", f"bursts that damaged >= 1 enemy within {SPRAY_BURST_TICKS} ticks", "exact"),
    "grenade_throws": ("seat", "count", "grenades thrown", "exact"),
    "grenade_blasts_effective": ("seat", "count", "blasts that damaged >= 1 enemy", "exact"),
    "dealt_hp_enemy": ("seat", "hp", "HP removed from enemies", "exact"),
    "dealt_armor_enemy": ("seat", "armor", "armor removed from enemies", "exact"),
    **{f"dealt_hp_enemy_{w}": ("seat", "hp", f"HP removed from enemies by {w}", "exact") for w in WEAPONS},
    "dealt_hp_friendly": ("seat", "hp", "HP removed from teammates", "exact"),
    "taken_hp": ("seat", "hp", "HP lost to any source", "exact"),
    "taken_armor": ("seat", "armor", "armor lost to any source", "exact"),
    "kills": ("seat", "count", "enemy kills", "exact"),
    "kills_friendly": ("seat", "count", "teammates killed", "exact"),
    "deaths": ("seat", "count", "deaths from any source", "exact"),
    "kd": ("seat", "ratio", "kills / deaths; null when deaths == 0", "exact"),
    "trade_kills": ("seat", "count", f"kills of an enemy who killed a teammate within {TRADE_WINDOW_TICKS} ticks before", "exact"),
    "deaths_traded": ("seat", "count", f"deaths by an enemy who was killed by our team within {TRADE_WINDOW_TICKS} ticks", "exact"),
    "alive_ticks": ("seat", "ticks", "ticks alive (hp > 0 after the step)", "exact"),
    "alive_share": ("seat", "ratio", "alive_ticks / (ticks * seats)", "exact"),
    "heart_reach_ticks": ("seat", "ticks", "alive ticks within 140 units + traversable of a heart (capture reach)", "exact"),
    "heart_reach_share": ("seat", "ratio", "heart_reach_ticks / alive_ticks", "exact"),
    "contested_reach_ticks": ("seat", "ticks", "alive ticks in reach of a contested heart", "exact"),
    "own_territory_share": ("seat", "ratio", "alive ticks in own territory / alive_ticks", "exact"),
    "enemy_territory_share": ("seat", "ratio", "alive ticks in enemy territory / alive_ticks", "exact"),
    "neutral_territory_share": ("seat", "ratio", "alive ticks in neutral territory / alive_ticks", "exact"),
    "water_ticks": ("seat", "ticks", "alive ticks wading (inWater)", "exact"),
    "trench_ticks": ("seat", "ticks", "alive ticks in a trench", "exact"),
    **{f"pickups_{k}": ("seat", "count", f"{k} pickups taken", "exact") for k in PICKUP_KINDS},
    "pickups_unattributed": ("seat/team", "count", "pickups whose taker could not be identified", "inferred"),
    "glory_hearts_taken": ("seat", "count", "glory hearts taken", "exact"),
    "shouts": ("seat", "count", "shout messages", "exact"),
    "shout_bytes": ("seat", "bytes", "shout text bytes", "exact"),
    "shouts_heard_by_enemy": ("seat", "count", "shouts at least one living enemy was in earshot of (1,280 units)", "exact"),
    "idle_ticks": ("seat", "ticks", "alive decision ticks with an empty command", "exact"),
    "idle_share": ("seat", "ratio", "idle_ticks / decision_ticks", "exact"),
    "stuck_ticks": ("seat", "ticks", f"sampled ticks moving < {STUCK_MAX_DISPLACEMENT} units per sample toward an unchanged goal >= {STUCK_MIN_GOAL_DISTANCE} away", "sampled"),
    "vm_disabled_suspect": ("seat", "bool", f"empty commands for the last >= {VM_DISABLED_MIN_IDLE_TICKS} alive ticks", "inferred"),
    "vm_disabled_since_tick": ("seat", "tick", "tick after the last non-empty command, when vm_disabled_suspect", "inferred"),
    "vm_errors": ("seat", "count", "'BASIC error:' lines in the seat log; null without a log", "exact"),
    "intent_lines": ("seat", "count", "intent telemetry lines in the seat log; null without a log", "exact"),
}


def elo_outcome(ours: float, theirs: float) -> float:
    return float(np.clip(0.5 + (ours - theirs) / (2 * ELO_MARGIN_SCALE), 0.0, 1.0))


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _count_by(frame: pd.DataFrame, key: str, name: str) -> pd.Series:
    return frame.groupby(key).size().rename(name) if len(frame) else pd.Series(dtype=float, name=name)


def _events(ep, kind: str) -> pd.DataFrame:
    ev = ep["events"]
    rows = ev[ev.kind == kind]
    if not len(rows):
        return rows.assign(payload=[])
    return rows.assign(payload=rows["data"].map(json.loads))


# ---------------------------------------------------------------- seat level

def trades(kills: pd.DataFrame, window: int = TRADE_WINDOW_TICKS) -> tuple[pd.Series, pd.Series]:
    """(trade_kills by seat, deaths_traded by seat) from the kills table."""
    enemy = kills[kills.seat.notna() & ~kills.friendly & ~kills["self"]].sort_values("t")
    trade_kills: dict[int, int] = {}
    deaths_traded: dict[int, int] = {}
    for row in enemy.itertuples():
        # Did the victim's team kill this killer within the window after?
        avenged = enemy[(enemy.victim == row.seat) & (enemy.t > row.t) & (enemy.t <= row.t + window) &
                        (enemy.team == row.victim_team)]
        if len(avenged):
            deaths_traded[row.victim] = deaths_traded.get(row.victim, 0) + 1
            avenger = int(avenged.iloc[0].seat)
            trade_kills[avenger] = trade_kills.get(avenger, 0) + 1
    return pd.Series(trade_kills, dtype=float), pd.Series(deaths_traded, dtype=float)


def stuck_ticks(states: pd.DataFrame) -> pd.Series:
    if not len(states):
        return pd.Series(dtype=float)
    s = states.sort_values(["seat", "t"])
    prev = s.groupby("seat").shift(1)
    moved = np.hypot(s.x - prev.x, s.z - prev.z)
    goal_far = np.hypot(s.goal_x - s.x, s.goal_z - s.z) >= STUCK_MIN_GOAL_DISTANCE
    same_goal = (s.goal_x == prev.goal_x) & (s.goal_z == prev.goal_z)
    stuck = (s.hp > 0) & (prev.hp > 0) & (moved < STUCK_MAX_DISPLACEMENT) & goal_far & same_goal
    return (s.t - prev.t).where(stuck, 0).groupby(s.seat).sum()


def seat_metrics(ep) -> pd.DataFrame:
    seats = ep["seats"].set_index("seat")
    episode = ep["episodes"].iloc[0]
    out = seats[["episode_id", "team", "policy_key", "policy_version_id", "policy_name"]].copy()
    ticks = int(episode.ticks)
    out["ticks"] = ticks

    shots = ep["shots"]
    out["shots"] = _count_by(shots, "seat", "shots")
    out["windups"] = _count_by(_events(ep, "windup"), "seat", "windups")
    out["gun_hits"] = _count_by(shots[shots.hit], "seat", "h")
    out["gun_hits_enemy"] = _count_by(shots[shots.hit & ~shots.friendly], "seat", "h")
    out["gun_hits_friendly"] = _count_by(shots[shots.hit & shots.friendly], "seat", "h")
    blocked = shots[~shots.hit & shots.apply(lambda r: len(r.inferred_shielded) + len(r.inferred_dead)
                                                 + len(r.inferred_trench_dodge) > 0, axis=1)] if len(shots) else shots
    out["shots_inferred_blocked"] = _count_by(blocked, "seat", "b")
    out["shots_untargeted"] = _count_by(shots[~shots.hit & shots.aim_target.isna()], "seat", "u")
    # Distance band: exact hit distance for enemy hits, inferred aim-line target distance for misses.
    if len(shots):
        enemy_hit = shots.hit & ~shots.friendly
        distance = shots.hit_distance.where(enemy_hit, shots.aim_target_distance.where(~shots.hit))
        for band in DISTANCE_BANDS:
            inside = (distance >= band[0]) & (distance < band[1])
            out[f"gun_shots_band_{_band_name(band)}"] = _count_by(shots[inside], "seat", "n")
            out[f"_hits_band_{_band_name(band)}"] = _count_by(shots[inside & enemy_hit], "seat", "n")

    damage = ep["damage"]
    enemy_damage = damage[damage.seat.notna() & ~damage.friendly & ~damage["self"]]
    out["dealt_hp_enemy"] = enemy_damage.groupby("seat").hp_removed.sum()
    out["dealt_armor_enemy"] = enemy_damage.groupby("seat").armor_absorbed.sum()
    for weapon in WEAPONS:
        out[f"dealt_hp_enemy_{weapon}"] = enemy_damage[enemy_damage.weapon == weapon].groupby("seat").hp_removed.sum()
    out["dealt_hp_friendly"] = damage[damage.friendly].groupby("seat").hp_removed.sum()
    out["taken_hp"] = damage.groupby("victim").hp_removed.sum()
    out["taken_armor"] = damage.groupby("victim").armor_absorbed.sum()

    # Spray bursts that hurt an enemy within the burst's damage ticks.
    sprays = _events(ep, "spray")
    spray_hits = enemy_damage[enemy_damage.weapon == "spray"]
    effective = [bool(len(spray_hits[(spray_hits.seat == r.seat) & (spray_hits.t >= r.t) &
                                     (spray_hits.t < r.t + SPRAY_BURST_TICKS)])) for r in sprays.itertuples()]
    out["spray_bursts"] = _count_by(sprays, "seat", "n")
    out["spray_bursts_effective"] = _count_by(sprays[effective] if len(sprays) else sprays, "seat", "n")
    throws = _events(ep, "grenade_throw")
    out["grenade_throws"] = _count_by(throws, "seat", "n")
    blasts = _events(ep, "grenade_blast")
    if len(blasts):
        team_of = seats.team.to_dict()
        hurt = blasts.apply(lambda r: r.seat is not None and not pd.isna(r.seat) and any(
            team_of[v] != team_of[int(r.seat)] for v in r.payload["victims"]), axis=1)
        out["grenade_blasts_effective"] = _count_by(blasts[hurt], "seat", "n")
    else:
        out["grenade_blasts_effective"] = np.nan

    kills = ep["kills"]
    enemy_kills = kills[kills.seat.notna() & ~kills.friendly & ~kills["self"]]
    out["kills"] = _count_by(enemy_kills, "seat", "n")
    out["kills_friendly"] = _count_by(kills[kills.friendly], "seat", "n")
    out["deaths"] = _count_by(kills, "victim", "n")
    trade_kills, deaths_traded = trades(kills)
    out["trade_kills"] = trade_kills
    out["deaths_traded"] = deaths_traded

    pickups = ep["pickups"]
    for kind in PICKUP_KINDS:
        out[f"pickups_{kind}"] = _count_by(pickups[pickups.pickup_kind == kind], "seat", "n")
    glory = ep["glory"]
    out["glory_hearts_taken"] = _count_by(glory[glory.seat.notna()], "seat", "n")
    shouts = ep["shouts"]
    out["shouts"] = _count_by(shouts, "seat", "n")
    out["shout_bytes"] = shouts.groupby("seat").bytes.sum() if len(shouts) else np.nan
    out["shouts_heard_by_enemy"] = _count_by(shouts[shouts.heard_by_enemy > 0], "seat", "n")

    # Zero-fill exact event counts: the trace saw the whole match, so absence means none.
    counts = [c for c, (level, unit, _, kind) in METRICS.items()
              if c in out.columns and unit in ("count", "hp", "armor", "bytes") and kind != "sampled"]
    counts += [c for c in out.columns if c.startswith("_hits_band_")]
    out[counts] = out[counts].fillna(0)

    for column in ("alive_ticks", "water_ticks", "trench_ticks", "heart_reach_ticks", "contested_reach_ticks",
                   "idle_ticks", "captures", "final_idle_run_ticks", "last_active_command_tick", "decision_ticks"):
        out[column] = seats[column]
    out["captures_completed"] = seats["captures"]
    out["windups_aborted"] = out.windups - out.shots
    alive = seats.alive_ticks.replace(0, np.nan)
    out["alive_share"] = seats.alive_ticks / ticks
    out["heart_reach_share"] = seats.heart_reach_ticks / alive
    out["own_territory_share"] = seats.own_territory_ticks / alive
    out["enemy_territory_share"] = seats.enemy_territory_ticks / alive
    out["neutral_territory_share"] = seats.neutral_territory_ticks / alive
    out["idle_share"] = seats.idle_ticks / seats.decision_ticks.replace(0, np.nan)
    out["stuck_ticks"] = stuck_ticks(ep["states"]).reindex(out.index).fillna(0)
    suspect = seats.final_idle_run_ticks >= VM_DISABLED_MIN_IDLE_TICKS
    out["vm_disabled_suspect"] = suspect
    out["vm_disabled_since_tick"] = (seats.last_active_command_tick + 1).where(suspect)

    log = ep["policy_log"]
    logged = set(log[log.line_kind == "log_present"].seat) if len(log) else set()
    for name, kind in (("vm_errors", "vm_error"), ("intent_lines", "intent")):
        counted = _count_by(log[log.line_kind == kind], "seat", "n") if len(log) else pd.Series(dtype=float)
        out[name] = [counted.get(s, 0) if s in logged else None for s in out.index]
    _finish_ratios(out)
    return out.reset_index().rename(columns={"index": "seat"})


def _finish_ratios(out: pd.DataFrame) -> None:
    """Ratios from (summed) numerators and denominators; null when the denominator is 0."""
    shots = out.shots.replace(0, np.nan)
    out["gun_accuracy"] = out.gun_hits / shots
    out["gun_enemy_accuracy"] = out.gun_hits_enemy / shots
    deaths = out.deaths.replace(0, np.nan)
    out["kd"] = out.kills / deaths
    for band in DISTANCE_BANDS:
        name = _band_name(band)
        if f"gun_shots_band_{name}" in out:
            out[f"gun_acc_band_{name}"] = out[f"_hits_band_{name}"] / out[f"gun_shots_band_{name}"].replace(0, np.nan)


# ---------------------------------------------------------------- policy level

SUMMED = [c for c, (level, unit, _, kind) in METRICS.items() if "seat" in level and unit in ("count", "hp", "armor", "bytes", "ticks")]


def policy_metrics(ep, seat_rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per (episode, policy_key). The unit of analysis: never one row per seat."""
    seats = seat_rows if seat_rows is not None else seat_metrics(ep)
    episode = ep["episodes"].iloc[0]
    glory = (int(episode.glory_0), int(episode.glory_1))
    rows = []
    for key, group in seats.groupby("policy_key", sort=True):
        teams = sorted(group.team.unique())
        summed = {c: group[c].sum(min_count=1) for c in SUMMED + [c for c in group.columns if c.startswith("_hits_band_")]
                  if c in group}
        row = {"episode_id": episode.episode_id, "policy_key": key,
               "policy_version_id": group.policy_version_id.iloc[0], "policy_name": group.policy_name.iloc[0],
               "seats": len(group), "team": teams[0] if len(teams) == 1 else None, **summed,
               "alive_share": group.alive_ticks.sum() / (len(group) * int(episode.ticks)),
               "vm_disabled_suspect_seats": int(group.vm_disabled_suspect.sum())}
        alive = group.alive_ticks.sum()
        for name, column in (("heart_reach_share", "heart_reach_ticks"),):
            row[name] = _ratio(group[column].sum(), alive)
        row["idle_share"] = _ratio(group.idle_ticks.sum(), group.decision_ticks.sum())
        row.update(_outcome(row["team"], glory, int(episode.winner)))
        rows.append(row)
    out = pd.DataFrame(rows)
    _finish_ratios(out)
    return out.drop(columns=[c for c in out.columns if c.startswith("_")])


def _outcome(team, glory: tuple[int, int], winner: int) -> dict:
    if team is None:  # a mirror match: the policy is on both sides, so it has no outcome
        return {"result": None, "glory_ours": None, "glory_theirs": None, "elo_outcome": None, "winning_glory": None}
    ours, theirs = glory[team], glory[1 - team]
    result = "draw" if winner not in (0, 1) else ("win" if winner == team else "loss")
    return {"result": result, "glory_ours": ours, "glory_theirs": theirs, "elo_outcome": elo_outcome(ours, theirs),
            "winning_glory": ours if result == "win" else None}


# ---------------------------------------------------------------- team level

def heart_ticks(ep) -> dict[int, int]:
    """Exact heart-ticks per team: t=0 owners, then ownership changes at capture_complete.

    The step that produces tick t scores the owner after that step's captures, so an owner
    from capture tick c scores ticks c .. (next capture - 1); the t=0 owner scores from 1.
    Equals the team's meter_ticks (rules >= 28) unless elimination filled the survivor's meter."""
    hs = ep["heart_states"]
    ticks = int(ep["episodes"].iloc[0].ticks)
    owners = hs[hs.t == 0].set_index("heart").owner.to_dict()
    since = {heart: 1 for heart in owners}
    total = {0: 0, 1: 0}
    caps = ep["captures"]
    for row in caps[caps.kind == "capture_complete"].sort_values("t").itertuples():
        if owners[row.heart] in total:
            total[owners[row.heart]] += row.t - since[row.heart]
        owners[row.heart], since[row.heart] = int(row.team), row.t
    for heart, owner in owners.items():
        if owner in total:
            total[owner] += ticks + 1 - since[heart]
    return total


def team_metrics(ep, seat_rows: pd.DataFrame | None = None) -> pd.DataFrame:
    seats = seat_rows if seat_rows is not None else seat_metrics(ep)
    episode = ep["episodes"].iloc[0]
    ticks = int(episode.ticks)
    glory = (int(episode.glory_0), int(episode.glory_1))
    rules = int(episode.rules)
    awards = ep["glory"]
    caps = ep["captures"]
    pickups = ep["pickups"]
    ts = ep["team_states"].sort_values("t")
    diff = np.sign(ts.meter_ticks_0 - ts.meter_ticks_1)
    diff = diff[diff != 0]
    lead_changes = int((diff != diff.shift()).sum() - 1) if len(diff) else 0
    held = heart_ticks(ep)
    rows = []
    for team in (0, 1):
        group = seats[seats.team == team]
        row = {"episode_id": episode.episode_id, "team": team, "team_name": pw_episodes.TEAM_NAMES[team],
               "policy_keys": ",".join(sorted(group.policy_key.unique())), "ticks": ticks,
               **_outcome(team, glory, int(episode.winner))}
        for part in ("initial", "countdown", "unsettled"):
            row[f"glory_{part}"] = episode[f"glory_{part}_{team}"]
        row["glory_settled_loss"] = (row["glory_unsettled"] - glory[team]) if pd.notna(row["glory_unsettled"]) else None
        for kind in GLORY_KINDS:
            row[f"glory_{kind}"] = int(awards[(awards.team == team) & (awards.glory_kind == kind)].amount.sum()) if rules >= 37 else None
        if rules >= 37 and pd.notna(row["glory_initial"]):
            identity = row["glory_initial"] + sum(row[f"glory_{k}"] for k in GLORY_KINDS) - row["glory_countdown"]
            if identity != row["glory_unsettled"]:
                raise ValueError(f"{episode.episode_id} team {team}: glory composition {identity} != {row['glory_unsettled']}")
        row["meter_points"] = episode[f"meter_ticks_{team}"] / episode.tick_rate
        meter = int(episode[f"meter_ticks_{team}"])
        filled = row["result"] == "win" and meter == int(episode.meter_target_ticks)
        if rules >= 28 and held[team] != meter and not filled:
            raise ValueError(f"{episode.episode_id} team {team}: heart-ticks {held[team]} != meter ticks {meter}")
        completes = caps[(caps.kind == "capture_complete") & (caps.team == team)]
        row["first_capture_tick"] = int(completes.t.min()) if len(completes) else None
        row["lead_changes"] = lead_changes
        row["heart_ticks"] = held[team]
        row["hearts_held_mean"] = held[team] / ticks
        row["hearts_held_end"] = int(episode[f"hearts_owned_{team}"])
        row["captures_completed"] = len(completes)
        row["capture_starts"] = int(((caps.kind == "capture_start") & (caps.team == team)).sum())
        row["capture_resets"] = int(((caps.kind == "capture_reset") & (caps.team == team)).sum())
        # A contest is both teams in reach, so it counts for both, including one that starts
        # with no capture in progress (its captures.team is null).
        row["contests"] = int((caps.kind == "contest_start").sum())
        taken = sorted(pickups[pickups.team == team].t)
        row["longest_supply_gap_ticks"] = int(np.max(np.diff([0, *taken, ticks])))
        row["cogs_out_end"] = int(episode[f"cogs_out_{team}"])
        row["team_lives_end"] = int(episode[f"team_lives_{team}"])
        for column in ("kills", "deaths", "kills_friendly", "shots", "gun_hits_enemy", "dealt_hp_enemy",
                       "trade_kills", "shouts", *[f"pickups_{k}" for k in PICKUP_KINDS]):
            row[column] = group[column].sum()
        row["pickups_unattributed"] = int(pickups.seat.isna().sum())  # team unknown: reported on both rows
        row["gun_enemy_accuracy"] = _ratio(row["gun_hits_enemy"], row["shots"])
        row["kd"] = _ratio(row["kills"], row["deaths"])
        rows.append(row)
    return pd.DataFrame(rows)


def batch_metrics(batch) -> dict[str, pd.DataFrame]:
    seats, policies, teams = [], [], []
    for ep in batch.episodes:
        s = seat_metrics(ep)
        seats.append(s)
        policies.append(policy_metrics(ep, s))
        teams.append(team_metrics(ep, s))
    cat = lambda frames: pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()  # noqa: E731
    return {"seat": cat(seats), "policy": cat(policies), "team": cat(teams)}


# ---------------------------------------------------------------- CLI

def _fmt(value, spec=".0f"):
    return "-" if value is None or pd.isna(value) else format(value, spec)


def print_summary(ep, policy: str | None = None) -> None:
    seats = seat_metrics(ep)
    teams = team_metrics(ep, seats)
    episode = ep["episodes"].iloc[0]
    print(f"\n{episode.episode_id}  rules {episode.rules}  {episode.ticks} ticks  map {episode['map'] or 'heartwick'}  "
          f"coworld {_fmt(episode.coworld_version, '')}")
    for t in teams.itertuples():
        print(f"  {t.team_name:5} {t.result:4}  glory {t.glory_ours:4} (elo outcome {t.elo_outcome:.3f})  "
              f"= {_fmt(t.glory_initial)} start - {_fmt(t.glory_countdown)} countdown + "
              + " + ".join(f"{_fmt(getattr(t, 'glory_' + k))} {k}" for k in GLORY_KINDS)
              + f" - {_fmt(t.glory_settled_loss)} settled")
        print(f"        meter {t.meter_points:.0f}  hearts mean {t.hearts_held_mean:.2f} end {t.hearts_held_end}  "
              f"captures {t.captures_completed}/{t.capture_starts} started ({t.capture_resets} reset)  "
              f"first capture {_fmt(t.first_capture_tick)}  K/D {t.kills:.0f}/{t.deaths:.0f}  "
              f"gun acc {_fmt(t.gun_enemy_accuracy, '.1%')}  trades {t.trade_kills:.0f}  "
              f"cogs out {t.cogs_out_end}  supply gap {t.longest_supply_gap_ticks}")
    for p in policy_metrics(ep, seats).itertuples():
        if policy and policy not in (p.policy_key, p.policy_name):
            continue
        print(f"  policy {p.policy_name} ({p.seats} seats, team {_fmt(p.team, '')}): {_fmt(p.result, '')}  "
              f"shots {p.shots:.0f} acc {_fmt(p.gun_enemy_accuracy, '.1%')}  kills {p.kills:.0f} deaths {p.deaths:.0f}  "
              f"alive {p.alive_share:.0%}  heart reach {_fmt(p.heart_reach_share, '.0%')}  idle {_fmt(p.idle_share, '.1%')}  "
              f"vm suspect seats {p.vm_disabled_suspect_seats}")


def build_parser() -> pw_cli.ArgumentParser:
    parser = pw_cli.ArgumentParser("pw_metrics", __doc__, examples=[
        "uv run python paintbot_pw_lab/tools/pw_metrics.py paintbot_pw_lab/episode_data/20260928T214433_* --json",
        "uv run python paintbot_pw_lab/tools/pw_metrics.py ROOT --policy james-pw --csv /tmp/metrics"])
    parser.add_argument("roots", nargs="+", type=Path, help="episode dirs, batch dirs or NAME.replay files")
    parser.add_argument("--policy", help="only this policy_key or policy_name (exit 2 listing valid values if absent)")
    parser.add_argument("--csv", type=Path, help="write seat/policy/team metric CSVs into this directory")
    parser.add_argument("--tag", help="pw_trace build (default: tools/release.env)")
    return parser


def run_cli(args, report: pw_cli.Report) -> dict:
    batch = pw_episodes.load_batch(args.roots, tag=args.tag)
    report.add_batch(batch)
    pw_cli.require_policy(batch.episodes, args.policy)
    for ep in sorted(batch.episodes, key=lambda e: e.episode_id):
        print_summary(ep, args.policy)
    print(f"\n{len(batch.episodes)} episodes, {len(batch.failures)} failed")
    for path, code, message in batch.failures:
        print(f"FAILED [{code}] {path}: {message}")
    levels = batch_metrics(batch)
    if args.csv:
        args.csv.mkdir(parents=True, exist_ok=True)
        for level, frame in levels.items():
            frame.to_csv(args.csv / f"{level}_metrics.csv", index=False)
            report.output(args.csv / f"{level}_metrics.csv")
        print(f"wrote {args.csv}/{{seat,policy,team}}_metrics.csv")
    policy = levels["policy"]
    if args.policy and len(policy):
        policy = policy[(policy.policy_key == args.policy) | (policy.policy_name == args.policy)]
    return {"team": pw_cli.records(levels["team"]), "policy": pw_cli.records(policy),
            "seat_metrics": "use --csv DIR for seat-level rows"}


def main(argv: list[str] | None = None) -> int:
    return pw_cli.run(build_parser(), run_cli, argv)


if __name__ == "__main__":
    sys.exit(main())
