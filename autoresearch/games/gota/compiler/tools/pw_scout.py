#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_scout.py
"""Paintbot PW opponent scouting and field survey from PUBLIC league data.

Three subcommands, all free (anonymous public reads, no credits, no auth):

  leaders who the division's current champions are, as policy refs for --opponent: the
          latest completed round's round_config.entrant_attributions (player -> exact
          policy_version_id), named from that round's episode rows (policy_name, version,
          owner), ranked by the public leaderboard (rank, MMR). Three reads. Unlike the
          leaderboard's policy_label (null for some champions, e.g. Alpha), every entrant of
          the round is resolved. HTTP 429 after pw_public's retries is exit 1, code rate_limited.

  fetch   list the league's recent completed rounds (/v2/rounds?league_id=...), then each
          round's episodes (/v2/rounds/<id>/episodes), and save each completed episode as
          <out>/r<round>_<ereq>/episode.json (the public row + round_id/round_number) and
          replay.gz (its public replay_url). Gentle by default: at most MAX_EPISODES_DEFAULT
          episodes; the pause, back-off and retry limit live in pw_public.py. Episodes already
          on disk are skipped without a request (re-runs are cheap). Field-study guard: the FIRST new episode is traced through pw_trace
          (pw_episodes.load_episode) before anything else is pulled; a failure stops the fetch.

  report  load the episodes (hash-checked, pw_episodes.load_batch), then write
          scout.json + scout.md (+ scout.interesting.json) with:
            standings        per policy: W-D-L, win rate with a Wilson interval, mean Elo outcome score
            matrix           policy x policy: n, W-L, win rate (Wilson), mean Elo outcome of the row
            profiles         per policy: opening first targets and first hearts reached, capture
                             order, weapon mix, grenade/spray use, pickups, fights, typical win
                             time, glory composition
            shouts           per policy: shout templates (numbers -> <n>), counts, examples with
                             tick/seat/position, and slot hints (which state field a number tracks)
            interesting      flagged episodes with an agent-written one-line reason (--reasons)
            sanity           warnings: flat 0%/100% aggregates, all-zero metrics, one side always
                             winning, mixed versions. Treat each as a tooling bug until disproved.

Identity: a side (team) belongs to one policy when all its seats share it (league episodes:
one policy per team). Key = exact policy_version_id (default) or policy_name (--by name,
pools versions: say so). Team comes from pw_episodes (public rows have no game_config.slots,
so team = seat parity, noted as team_from_parity). Heart and pickup names are given in
EMBER'S FRAME: for an Azure side, feature k is reported as its point mirror, so h0 is always
the side's own home heart and h1 the enemy's. Contract: docs/tools/pw_scout.md.

CLI:
  uv run python paintbot_pw_lab/tools/pw_scout.py leaders [--division ID] [--top N] [--json]
  uv run python paintbot_pw_lab/tools/pw_scout.py fetch [--league ID] [--max-episodes 30] [--max-rounds 6] [--out DIR]
  uv run python paintbot_pw_lab/tools/pw_scout.py report ROOT [ROOT ...] [--out DIR] [--reasons FILE]
      [--by version|name] [--ours KEY_OR_NAME] [--vis-every M]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_episodes  # noqa: E402
import pw_fights  # noqa: E402
import pw_cli  # noqa: E402
import pw_metrics  # noqa: E402
import pw_public  # noqa: E402

MAIN_LEAGUE = pw_public.MAIN_LEAGUE   # paintbot-pw teams ladder (docs/field.md)
COMPETITION_DIVISION = pw_public.COMPETITION_DIVISION
DEFAULT_OUT = pw_episodes.LAB / "episode_data" / "scout"

# fetch size (politeness itself - pause, back-off, retries - lives in pw_public.py)
MAX_EPISODES_DEFAULT = 30
MAX_EPISODES_CAP = 100
MAX_ROUNDS_DEFAULT = 6

# report thresholds (named, printed in the report)
Z_95 = 1.96
FEATURE_RADIUS = 400            # a first walk goal this close to a heart/pickup targets it
CAPTURE_REACH = 140             # the engine's capture reach (mechanics.nim): "reached a heart"
OPENING_WINDOW_TICKS = 720      # 30 s: first-heart-reached must happen within this
MIRROR_TOLERANCE = 50           # a feature's point mirror must be within this of another feature
HEART_ORDER_LENGTH = 3          # capture order: the first N captures
SLOT_MIN_SAMPLES = 5            # a shout number slot needs this many values for a hint
SLOT_POSITION_TOLERANCE = 100   # median |value - field| for a positional hint (sampled states)
SHOUT_EXAMPLES = 3
LOW_MARGIN_GLORY = 500          # winner's glory at or under this (Elo outcome <= 0.75) is a narrow win
LONG_MATCH_FACTOR = 2.0         # ticks >= factor x batch median
FAST_WIN_FACTOR = 0.5           # ticks <= factor x batch median
FRIENDLY_KILLS_FLAG = 4         # friendly kills by one side in one episode worth a look
COMEBACK_LEAD_CHANGES = 3       # meter lead changes (sampled) for a back-and-forth match
INTERESTING_MAX = 12

REPORT_THRESHOLDS = {k: v for k, v in globals().items()
                     if k in ("FEATURE_RADIUS", "CAPTURE_REACH", "OPENING_WINDOW_TICKS", "MIRROR_TOLERANCE",
                              "HEART_ORDER_LENGTH", "SLOT_MIN_SAMPLES", "SLOT_POSITION_TOLERANCE",
                              "LOW_MARGIN_GLORY", "LONG_MATCH_FACTOR", "FAST_WIN_FACTOR",
                              "FRIENDLY_KILLS_FLAG", "COMEBACK_LEAD_CHANGES")}


# ================================================================ fetch

def fetch(league: str, out: Path, max_episodes: int, max_rounds: int, tag: str | None, report=None) -> dict:
    """Save up to max_episodes completed public episodes under out/r<round>_<ereq>/ (pw_public).

    Idempotent: an episode already on disk is skipped without a request. Returns the index."""
    out.mkdir(parents=True, exist_ok=True)
    rounds = pw_public.rounds(league_id=league, limit=max_rounds + 2)
    completed = [r for r in rounds if r.get("status") == "completed"][:max_rounds]
    print(f"league {league}: {len(rounds)} recent rounds listed, {len(completed)} completed used")
    saved, skipped, excluded, index = 0, 0, Counter(), []
    checked = False
    for rnd in completed:
        if saved + skipped >= max_episodes:
            break
        for row in pw_public.round_episodes(rnd["id"], limit=None):
            if saved + skipped >= max_episodes:
                break
            reason = pw_public.skip_reason(row)
            if reason:
                excluded[reason] += 1
                continue
            directory = out / f"r{rnd['round_number']}_{row['id']}"
            index.append({"dir": directory.name, "id": row["id"], "round_number": rnd["round_number"],
                          "coworld_version": row.get("coworld_version")})
            try:
                new = pw_public.save_episode(directory, row, rnd)
            except pw_public.BadReplay as error:
                print(f"  {row['id']}: {error}; skipped")
                excluded["bad_replay"] += 1
                index.pop()
                continue
            if not new:
                skipped += 1
                continue
            saved += 1
            if not checked:
                # Field-study guard: one fresh replay through the hash-checked trace before a batch.
                try:
                    ep = pw_episodes.load_episode(directory, tag=tag)
                except pw_episodes.EpisodeError as error:
                    message = (f"GUARD FAILED on {directory.name} [{error.code}]: {error}. Stop: build the "
                               "league's tag (deployed_ref.py --write, then build_tools.sh) before pulling a batch.")
                    print(message)
                    if report is not None:
                        report.fail(directory.name, f"guard_{error.code}", message)
                    return {"league": league, "saved": saved, "skipped": skipped, "guard": "failed"}
                row0 = ep["episodes"].iloc[0]
                print(f"  guard ok: {directory.name} traced hash-exact (rules {row0.rules}, {row0.ticks} ticks, "
                      f"glory {row0.glory_0}/{row0.glory_1}, results check {row0.results_check})")
                checked = True
    index_path = out / "index.json"
    index_path.write_text(json.dumps({
        "league": league, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rounds": [r["round_number"] for r in completed], "episodes": index,
        "excluded": dict(excluded)}, indent=1) + "\n")
    print(f"saved {saved} new, {skipped} already present, excluded {dict(excluded) or 0} -> {out}")
    if report is not None:
        report.counts.update(processed=saved + skipped, excluded=sum(excluded.values()))
        report.output(out)
        report.output(index_path)
        report.suggest(f"uv run python paintbot_pw_lab/tools/pw.py scout report {out} --json")
    return {"league": league, "out": str(out), "saved": saved, "already_present": skipped,
            "excluded": dict(excluded), "rounds": [r["round_number"] for r in completed],
            "guard": "ok" if checked else "not_run (nothing new)"}


# ================================================================ leaders

def leader_rows(attributions: list[dict], episode_rows: list[dict], board: list[dict] | None) -> list[dict]:
    """One row per round entrant: {rank, player, policy_ref, policy_version_id, mmr, owner, player_id}.

    Identity comes from the round (exact policy_version_id per player); names from the episode
    participants; rank and MMR from the leaderboard when given. Sorted by rank (unranked last)."""
    named = {}
    for row in episode_rows:
        for seat in row.get("participants") or []:
            named.setdefault(seat.get("policy_version_id"), seat)
    ranked = {entry.get("player_id"): entry for entry in board or []}
    rows = []
    for entrant in attributions:
        pvid = entrant.get("policy_version_id")
        seat = named.get(pvid, {})
        entry = ranked.get(entrant.get("subject_id"), {})
        version = seat.get("version")
        score = entry.get("score")
        rows.append({
            "rank": entry.get("rank"),
            "player": seat.get("player_name") or entry.get("player_name"),
            "policy_ref": f"{seat['policy_name']}:v{version}" if seat.get("policy_name") and version is not None else None,
            "policy_version_id": pvid,
            "mmr": round(score) if isinstance(score, (int, float)) else None,
            "owner": seat.get("owner_name") or entry.get("owner_name"),
            "player_id": entrant.get("subject_id"),
        })
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, str(r["player"])))
    return rows


def leaders(division: str, top: int | None, report) -> dict:
    """Current champions of a division from public data (3 reads: rounds, episodes, leaderboard)."""
    retry = f"uv run python paintbot_pw_lab/tools/pw.py leaders --division {division} --json"
    try:
        rounds = pw_public.rounds(division_id=division, limit=6)
        latest = next((r for r in rounds if r.get("status") == "completed"), None)
        if latest is None:
            raise pw_cli.UsageError(f"division {division} has no completed round among its last {len(rounds)}; "
                                    "check the division id (default: the paintbot-pw Competition division)")
        episode_rows = pw_public.round_episodes(latest["id"])
    except pw_public.RateLimited as error:
        raise pw_cli.RateLimited(f"public API rate limit ({error})", retry) from error
    except pw_public.PublicFetchError as error:
        raise pw_cli.EnvironmentMissing(f"public API read failed ({error})", "the same command again later "
                                        "(check the network)") from error
    board, board_note = None, None
    try:
        board = pw_public.leaderboard(division)
    except pw_public.RateLimited as error:
        report.fail("leaderboard", "rate_limited", f"leaderboard rate limited ({error}); rows have no rank/MMR")
        report.suggest("wait and retry")
        report.suggest(retry)
        board_note = "rate limited"
    except pw_public.PublicFetchError as error:
        board_note = f"unavailable ({error})"
    attributions = (latest.get("round_config") or {}).get("entrant_attributions") or []
    rows = leader_rows(attributions, episode_rows, board)
    shown = rows[:top] if top else rows
    for row in shown:
        if row["policy_ref"] is None:
            report.fail(row["policy_version_id"], "unnamed_entrant",
                        f"player {row['player_id']}: policy_version_id not seen in round {latest['round_number']}'s "
                        "first episode page; pass the policy_version_id itself as --opponent")
    in_round = {row["player_id"] for row in rows}
    not_in_round = [{"rank": e.get("rank"), "player": e.get("player_name"), "owner": e.get("owner_name"),
                     "mmr": round(e["score"]) if isinstance(e.get("score"), (int, float)) else None}
                    for e in board or [] if e.get("player_id") not in in_round]
    print(f"division {division}: round {latest['round_number']} (completed {latest.get('completed_at')}), "
          f"{len(rows)} entrants; leaderboard {board_note or 'ok'}")
    for row in shown:
        rank = "-" if row["rank"] is None else row["rank"]
        mmr = "-" if row["mmr"] is None else row["mmr"]
        print(f"  #{rank:<3} {str(row['player']):<26} {str(row['policy_ref']):<32} MMR {mmr:<5} "
              f"{row['owner']}  {row['policy_version_id']}")
    for entry in not_in_round:
        print(f"  (on the leaderboard, not in round {latest['round_number']}: #{entry['rank']} {entry['player']})")
    report.counts["processed"] = len(shown)
    refs = [row["policy_ref"] or row["policy_version_id"] for row in shown]
    if refs:
        report.suggest("uv run python paintbot_pw_lab/tools/pw.py ab-requests --design paired --baseline NAME:vN "
                       "--candidate NAME:vM " + " ".join(f"--opponent {ref}" for ref in refs)
                       + f" --seeds 1-10 --league-id {MAIN_LEAGUE}")
    return {"division": division, "round_id": latest["id"], "round_number": latest["round_number"],
            "completed_at": latest.get("completed_at"), "leaderboard": board_note or "ok",
            "leaders": shown, "not_in_round": not_in_round}


# ================================================================ statistics

def wilson(k: int, n: int, z: float = Z_95) -> tuple[float | None, float | None]:
    """Wilson score interval for k successes in n trials; (None, None) when n == 0."""
    if n == 0:
        return None, None
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def mean_interval(values: list[float]) -> tuple[float | None, float | None]:
    """Mean and a normal-approximation 95% half-width (None with fewer than 2 values)."""
    if not values:
        return None, None
    mean = float(np.mean(values))
    half = Z_95 * float(np.std(values, ddof=1)) / math.sqrt(len(values)) if len(values) > 1 else None
    return mean, half


# ================================================================ per-episode sides

def _label(seats: pd.DataFrame) -> str:
    row = seats.iloc[0]
    version = row.policy_version
    return f"{row.policy_name}:v{int(version)}" if pd.notna(version) else str(row.policy_name)


def sides(ep, by: str) -> tuple[list[dict], str | None]:
    """One dict per team with its single policy identity, or (rows, exclusion reason)."""
    seats = ep["seats"]
    column = "policy_key" if by == "version" else "policy_name"
    out = []
    for team in (0, 1):
        mine = seats[seats.team == team]
        keys = mine[column].unique()
        if len(keys) != 1:
            return [], "mixed_team"
        out.append({"team": team, "key": str(keys[0]), "label": _label(mine) if by == "version" else str(keys[0]),
                    "seats": sorted(map(int, mine.seat))})
    if out[0]["key"] == out[1]["key"]:
        return [], "mirror"
    return out, None


# ================================================================ map frame (Ember's view)

def mirror_maps(meta: dict) -> tuple[dict[int, int] | None, dict[int, int] | None]:
    """Heart and pickup index -> index of its point mirror through the homes' midpoint."""
    homes = meta.get("homes") or []
    if len(homes) != 2:
        return None, None
    centre = np.mean(np.array(homes, float), axis=0)

    def mapping(features):
        positions = np.array([f["pos"] for f in features], float)
        result = {}
        for i, pos in enumerate(positions):
            distance = np.hypot(*(positions - (2 * centre - pos)).T)
            j = int(distance.argmin())
            if distance[j] > MIRROR_TOLERANCE:
                return None
            result[i] = j
        return result

    return mapping(meta["hearts"]), mapping(meta.get("pickups", []))


def frame_heart(index, team: int, hearts_mirror) -> str:
    if index is None or pd.isna(index):
        return "-"
    index = int(index)
    if team == 1:
        return f"h{hearts_mirror[index]}" if hearts_mirror else f"h{index}(raw)"
    return f"h{index}"


def _nearest_feature(x: float, z: float, meta: dict, team: int, hearts_mirror, pickups_mirror) -> str:
    best, label = FEATURE_RADIUS + 1, "field"
    for heart in meta["hearts"]:
        d = math.dist((x, z), heart["pos"])
        if d < best:
            best, label = d, frame_heart(heart["idx"], team, hearts_mirror)
    for i, pickup in enumerate(meta.get("pickups", [])):
        d = math.dist((x, z), pickup["pos"])
        if d < best:
            j = pickups_mirror[i] if team == 1 and pickups_mirror else i
            best, label = d, f"{pickup['kind']}#{j}"
    return label


def opening(ep, side: dict) -> dict:
    """First walk target per seat, first heart reached per seat, the side's capture order."""
    meta = ep.meta
    hearts_mirror, pickups_mirror = mirror_maps(meta)
    team = side["team"]
    states = ep["states"]
    targets, reached = Counter(), Counter()
    for seat in side["seats"]:
        s = states[(states.seat == seat) & (states.t > 0)].sort_values("t")
        moving = s[np.hypot(s.goal_x - s.x, s.goal_z - s.z) > 50]
        if len(moving):
            first = moving.iloc[0]
            targets[_nearest_feature(first.goal_x, first.goal_z, meta, team, hearts_mirror, pickups_mirror)] += 1
        early = s[(s.t <= OPENING_WINDOW_TICKS) & (s.hp > 0)]
        hit = None
        for row in early.itertuples():
            for heart in meta["hearts"]:
                if math.dist((row.x, row.z), heart["pos"]) <= CAPTURE_REACH:
                    hit = heart["idx"]
                    break
            if hit is not None:
                break
        reached[frame_heart(hit, team, hearts_mirror) if hit is not None else "none"] += 1
    caps = ep["captures"]
    mine = caps[(caps.team == team)].sort_values("t")
    completes = mine[mine.kind == "capture_complete"]
    starts = mine[mine.kind == "capture_start"]
    return {"first_targets": targets, "first_hearts_reached": reached,
            "first_capture_start": frame_heart(starts.heart.iloc[0], team, hearts_mirror) if len(starts) else "none",
            "capture_order": " > ".join(frame_heart(h, team, hearts_mirror)
                                        for h in completes.heart.head(HEART_ORDER_LENGTH)) or "none"}


# ================================================================ shout decoding

NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")
WORD_NUMBER = re.compile(r"^([A-Za-z_!?:.]+?)([+-]?\d+)$")


def shout_template(text: str) -> tuple[str, list[float]]:
    """'FIRE22 1137 1763' -> ('FIRE<n> <n> <n>', [22, 1137, 1763]). Words are kept as-is."""
    parts, numbers = [], []
    for token in text.split():
        if NUMBER.match(token):
            parts.append("<n>")
            numbers.append(float(token))
            continue
        match = WORD_NUMBER.match(token)
        if match:
            parts.append(match.group(1) + "<n>")
            numbers.append(float(match.group(2)))
        else:
            parts.append(token)
    return " ".join(parts), numbers


def _speaker_context(ep, shouts: pd.DataFrame) -> pd.DataFrame:
    """Shouts joined to the speaker's sampled state: x/z interpolated between samples; goal,
    aim, hp and the nearest living enemy's position from the last sample at or before t."""
    states = ep["states"].sort_values("t")
    rows = []
    team_of = ep["seats"].set_index("seat").team.to_dict()
    for shout in shouts.itertuples():
        own = states[states.seat == shout.seat]
        before, after = own[own.t <= shout.t], own[own.t >= shout.t]
        if not len(before):
            continue
        b = before.iloc[-1]
        a = after.iloc[0] if len(after) else b
        w = 0.0 if a.t == b.t else (shout.t - b.t) / (a.t - b.t)
        x, z = b.x + w * (a.x - b.x), b.z + w * (a.z - b.z)
        at = states[(states.t == b.t) & (states.hp > 0)]
        enemies = at[at.seat.map(team_of) != team_of[int(shout.seat)]]
        if len(enemies):
            nearest = enemies.iloc[int(np.hypot(enemies.x - x, enemies.z - z).argmin())]
            ex, ez = float(nearest.x), float(nearest.z)
        else:
            ex = ez = np.nan
        rows.append({"t": int(shout.t), "seat": int(shout.seat), "text": shout.text,
                     "pickup_near_goal": _nearest_index(ep.meta.get("pickups", []), b.goal_x, b.goal_z),
                     "pickup_near_speaker": _nearest_index(ep.meta.get("pickups", []), x, z),
                     "heart_near_goal": _nearest_index(ep.meta["hearts"], b.goal_x, b.goal_z),
                     "heard_by_enemy": int(shout.heard_by_enemy), "x": float(x), "z": float(z),
                     "goal_x": float(b.goal_x), "goal_z": float(b.goal_z), "aim_x": float(b.aim_x),
                     "aim_z": float(b.aim_z), "hp": float(b.hp), "lives": float(b.lives),
                     "enemy_x": ex, "enemy_z": ez, "episode_id": ep.episode_id})
    return pd.DataFrame(rows)


SLOT_CANDIDATES = {"x": SLOT_POSITION_TOLERANCE, "z": SLOT_POSITION_TOLERANCE,
                   "goal_x": SLOT_POSITION_TOLERANCE, "goal_z": SLOT_POSITION_TOLERANCE,
                   "aim_x": SLOT_POSITION_TOLERANCE, "aim_z": SLOT_POSITION_TOLERANCE,
                   "enemy_x": SLOT_POSITION_TOLERANCE, "enemy_z": SLOT_POSITION_TOLERANCE,
                   "t": 2, "seat": 0, "hp": 0, "lives": 0,
                   "pickup_near_goal": 0, "pickup_near_speaker": 0, "heart_near_goal": 0}


def _nearest_index(features: list[dict], x: float, z: float) -> float:
    """Raw engine index of the feature within FEATURE_RADIUS of (x, z), else NaN."""
    best, index = FEATURE_RADIUS, np.nan
    for i, feature in enumerate(features):
        d = math.dist((x, z), feature["pos"])
        if d <= best:
            best, index = d, float(i)
    return index


def slot_hints(context: pd.DataFrame, numbers: list[list[float]]) -> list[dict]:
    """Per numeric slot: range, distinct values and the state field it tracks (inferred)."""
    hints = []
    width = min(len(n) for n in numbers)
    for slot in range(width):
        values = np.array([n[slot] for n in numbers])
        hint = {"slot": slot, "min": float(values.min()), "max": float(values.max()),
                "distinct": len(set(values)), "tracks": None, "median_error": None}
        if len(values) >= SLOT_MIN_SAMPLES and len(set(values)) > 1:
            best = None
            for field, tolerance in SLOT_CANDIDATES.items():
                candidate = context[field].to_numpy(float)
                ok = ~np.isnan(candidate)
                if ok.sum() < SLOT_MIN_SAMPLES:
                    continue
                error = float(np.median(np.abs(values[ok] - candidate[ok])))
                if error <= tolerance and (best is None or error < best[1]):
                    best = (field, error)
            if best:
                hint["tracks"], hint["median_error"] = best[0], round(best[1], 1)
        hints.append(hint)
    return hints


def decode_shouts(contexts: pd.DataFrame) -> list[dict]:
    """Templates for one policy's shouts, most frequent first."""
    if not len(contexts):
        return []
    parsed = [shout_template(t) for t in contexts.text]
    contexts = contexts.assign(template=[p[0] for p in parsed], numbers=[p[1] for p in parsed])
    out = []
    for template, group in contexts.groupby("template"):
        examples = group.drop_duplicates("text")
        picks = examples.iloc[np.unique(np.linspace(0, len(examples) - 1, min(SHOUT_EXAMPLES, len(examples))).astype(int))]
        out.append({
            "template": template, "count": len(group), "episodes": int(group.episode_id.nunique()),
            "distinct_texts": int(group.text.nunique()),
            "heard_by_enemy_share": float((group.heard_by_enemy > 0).mean()),
            "first_t_median": float(group.t.median()),
            "examples": [{"text": r.text, "episode_id": r.episode_id, "t": r.t, "seat": r.seat,
                          "pos": [round(r.x), round(r.z)]} for r in picks.itertuples()],
            "slots": slot_hints(group, list(group.numbers)) if group.numbers.map(len).min() > 0 else [],
        })
    return sorted(out, key=lambda r: -r["count"])


# ================================================================ report assembly

def _episode_json(ep) -> dict:
    path = ep.source.episode_json
    return json.loads(path.read_text()) if path else {}


def collect(batch, by: str) -> dict:
    """Per-side rows, per-policy accumulators and exclusions from verified episodes."""
    side_rows, profile_parts, shouts, exclusions = [], defaultdict(list), defaultdict(list), Counter()
    for ep in sorted(batch.episodes, key=lambda e: e.episode_id):
        teams_sides, reason = sides(ep, by)
        if not reason and any(ep["seats"][ep["seats"].team == t].policy_key.nunique() != 1 for t in (0, 1)):
            reason = "mixed_team"   # --by name: two versions of one name on a team
        if reason:
            exclusions[reason] += 1
            continue
        seat_rows = pw_metrics.seat_metrics(ep)
        policy_rows = pw_metrics.policy_metrics(ep, seat_rows).set_index("policy_key")
        team_rows = pw_metrics.team_metrics(ep, seat_rows).set_index("team")
        fights = pw_fights.fight_policy_metrics(ep).set_index("policy_key")
        episode = ep["episodes"].iloc[0]
        row_json = _episode_json(ep)
        for side in teams_sides:
            team = side["team"]
            other = teams_sides[1 - team]
            seat_keys = ep["seats"][ep["seats"].team == team].policy_key.unique()
            prow = policy_rows.loc[seat_keys[0]]  # exactly one policy_key per team (checked above)
            trow = team_rows.loc[team]
            frow = fights.loc[seat_keys[0]]
            side_rows.append({
                "episode_id": ep.episode_id, "key": side["key"], "label": side["label"], "team": team,
                "opponent_key": other["key"], "opponent_label": other["label"], "result": trow.result,
                "elo_outcome": trow.elo_outcome, "glory_ours": int(trow.glory_ours), "glory_theirs": int(trow.glory_theirs),
                "ticks": int(episode.ticks), "rules": int(episode.rules), "coworld_version": episode.coworld_version,
                "round_number": row_json.get("round_number"), "lead_changes": int(trow.lead_changes),
                "vm_disabled_suspect_seats": int(prow.vm_disabled_suspect_seats),
                "kills_friendly": int(prow.kills_friendly), "notes": episode.notes,
                "detail_url": f"https://softmax.com/observatory/v2?detail=episode-request:{ep.episode_id}"
                if str(ep.episode_id).startswith("ereq_") else None,
                "replay_viewer_url": (f"https://softmax.com/observatory/coworld-replays/{row_json['coworld_id']}"
                                      f"?replay_uri={urllib.parse.quote(row_json['replay_url'], safe='')}")
                if row_json.get("coworld_id") and row_json.get("replay_url") else None,
            })
            profile_parts[side["key"]].append({"label": side["label"], "team": team, "result": trow.result,
                                               "ticks": int(episode.ticks), "policy": prow, "team_row": trow,
                                               "fights": frow, "opening": opening(ep, side),
                                               "seat_count": len(side["seats"])})
            own_shouts = ep["shouts"][ep["shouts"].seat.isin(side["seats"])]
            if len(own_shouts):
                shouts[side["key"]].append(_speaker_context(ep, own_shouts))
    return {"sides": pd.DataFrame(side_rows), "profiles": profile_parts, "shouts": shouts, "exclusions": exclusions}


def standings(sides_frame: pd.DataFrame) -> list[dict]:
    out = []
    for key, group in sides_frame.groupby("key"):
        wins, draws, losses = (int((group.result == r).sum()) for r in ("win", "draw", "loss"))
        low, high = wilson(wins, len(group))
        mean, half = mean_interval(list(group.elo_outcome))
        won = group[group.result == "win"]
        out.append({"key": key, "label": group.label.iloc[0], "episodes": len(group), "wins": wins, "draws": draws,
                    "losses": losses, "win_rate": wins / len(group), "win_rate_low": low, "win_rate_high": high,
                    "elo_outcome_mean": mean, "elo_outcome_half_width": half,
                    "winning_glory_mean": float(won.glory_ours.mean()) if len(won) else None,
                    "win_ticks_median": float(won.ticks.median()) if len(won) else None,
                    "sides": {"ember": int((group.team == 0).sum()), "azure": int((group.team == 1).sum())}})
    return sorted(out, key=lambda r: -(r["elo_outcome_mean"] or 0))


def matrix(sides_frame: pd.DataFrame) -> list[dict]:
    out = []
    for (key, opponent), group in sides_frame.groupby(["key", "opponent_key"]):
        wins = int((group.result == "win").sum())
        low, high = wilson(wins, len(group))
        out.append({"key": key, "label": group.label.iloc[0], "opponent_key": opponent,
                    "opponent_label": group.opponent_label.iloc[0], "episodes": len(group), "wins": wins,
                    "losses": int((group.result == "loss").sum()), "win_rate": wins / len(group),
                    "win_rate_low": low, "win_rate_high": high,
                    "elo_outcome_mean": float(group.elo_outcome.mean())})
    return out


def _sum(parts, column, source="policy"):
    values = [p[source][column] for p in parts if pd.notna(p[source][column])]
    return float(np.sum(values)) if values else None


def _per_episode(parts, column, source="policy"):
    total = _sum(parts, column, source)
    return total / len(parts) if total is not None else None


def profile(parts: list[dict]) -> dict:
    n = len(parts)
    targets, reached, first_start, order = Counter(), Counter(), Counter(), Counter()
    for p in parts:
        targets.update(p["opening"]["first_targets"])
        reached.update(p["opening"]["first_hearts_reached"])
        first_start[p["opening"]["first_capture_start"]] += 1
        order[p["opening"]["capture_order"]] += 1
    hp = {w: _sum(parts, f"dealt_hp_enemy_{w}") or 0.0 for w in pw_metrics.WEAPONS}
    hp_total = sum(hp.values())
    wins = [p["ticks"] for p in parts if p["result"] == "win"]
    losses = [p["ticks"] for p in parts if p["result"] == "loss"]
    shots = _sum(parts, "shots") or 0
    kills, deaths = _sum(parts, "kills") or 0, _sum(parts, "deaths") or 0
    alive = _sum(parts, "alive_ticks") or 0
    engagements = _sum(parts, "engagements", "fights") or 0
    first_hit = _sum(parts, "engagements_first_hit", "fights") or 0
    duels = _sum(parts, "opening_duels", "fights") or 0
    seat_minutes = sum(p["seat_count"] * p["ticks"] for p in parts) / (24 * 60)
    glory = {k: _per_episode(parts, f"glory_{k}", "team_row") for k in
             ("initial", "countdown", *pw_metrics.GLORY_KINDS, "settled_loss", "ours")}
    return {
        "episodes": n, "sides": dict(Counter(pw_episodes.TEAM_NAMES[p["team"]] for p in parts)),
        "opening_first_targets_per_episode": {k: round(v / n, 2) for k, v in targets.most_common(6)},
        "first_hearts_reached_per_episode": {k: round(v / n, 2) for k, v in reached.most_common(6)},
        "first_capture_start": dict(first_start.most_common(4)),
        "capture_order": dict(order.most_common(4)),
        "weapon_hp_share": {w: round(hp[w] / hp_total, 3) if hp_total else None for w in pw_metrics.WEAPONS},
        "gun_enemy_accuracy": (_sum(parts, "gun_hits_enemy") or 0) / shots if shots else None,
        "shots_per_episode": shots / n,
        "grenades_per_episode": _per_episode(parts, "grenade_throws"),
        "grenade_effective_share": (_sum(parts, "grenade_blasts_effective") or 0) / _sum(parts, "grenade_throws")
        if _sum(parts, "grenade_throws") else None,
        "spray_bursts_per_episode": _per_episode(parts, "spray_bursts"),
        "spray_effective_share": (_sum(parts, "spray_bursts_effective") or 0) / _sum(parts, "spray_bursts")
        if _sum(parts, "spray_bursts") else None,
        "pickups_per_episode": {k: _per_episode(parts, f"pickups_{k}") for k in pw_metrics.PICKUP_KINDS},
        "kills_per_episode": kills / n, "deaths_per_episode": deaths / n, "kd": kills / deaths if deaths else None,
        "friendly_kills_per_episode": _per_episode(parts, "kills_friendly"),
        "heart_reach_share": (_sum(parts, "heart_reach_ticks") or 0) / alive if alive else None,
        "idle_share": float(np.nanmean([p["policy"]["idle_share"] for p in parts])),  # mean of per-episode shares
        "engagements_per_episode": engagements / n,
        "engagement_win_share": (_sum(parts, "engagements_won", "fights") or 0) / engagements if engagements else None,
        "first_hit_share": first_hit / engagements if engagements else None,
        "won_when_first_hit_share": (_sum(parts, "won_when_first_hit", "fights") or 0) / first_hit if first_hit else None,
        "opening_duel_win_share": (_sum(parts, "opening_duels_won", "fights") or 0) / duels if duels else None,
        "trade_kills_per_episode": _per_episode(parts, "trade_kills"),
        "win_seconds_median": float(np.median(wins)) / 24 if wins else None,
        "loss_seconds_median": float(np.median(losses)) / 24 if losses else None,
        "first_capture_seconds_median": (float(np.nanmedian([p["team_row"]["first_capture_tick"] for p in parts
                                                             if pd.notna(p["team_row"]["first_capture_tick"])])) / 24)
        if any(pd.notna(p["team_row"]["first_capture_tick"]) for p in parts) else None,
        "hearts_held_mean": _per_episode(parts, "hearts_held_mean", "team_row"),
        "glory_per_episode": glory,
        "shouts_per_seat_minute": (_sum(parts, "shouts") or 0) / seat_minutes if seat_minutes else None,
        "shouts_heard_by_enemy_share": (_sum(parts, "shouts_heard_by_enemy") or 0) / _sum(parts, "shouts")
        if _sum(parts, "shouts") else None,
    }


def interesting(sides_frame: pd.DataFrame, table: list[dict], ours: str | None) -> list[dict]:
    """Flag episodes worth watching; rarer flags first, capped at INTERESTING_MAX."""
    strength = {r["key"]: r["elo_outcome_mean"] for r in table if r["episodes"] >= 3}
    median_ticks = sides_frame.drop_duplicates("episode_id").ticks.median()
    flagged = []
    for episode_id, group in sides_frame.groupby("episode_id"):
        winner = group[group.result == "win"]
        loser = group[group.result == "loss"]
        first = group.iloc[0]
        flags = []
        if len(winner) and len(loser):
            w, lo = winner.iloc[0], loser.iloc[0]
            if w.key in strength and lo.key in strength and strength[w.key] < strength[lo.key]:
                flags.append("upset")
            if w.glory_ours == 0:
                flags.append("zero_glory_win")
            elif w.glory_ours <= LOW_MARGIN_GLORY:
                flags.append("narrow_win")
        else:
            flags.append("draw")
        if first.ticks >= LONG_MATCH_FACTOR * median_ticks:
            flags.append("long_match")
        if first.ticks <= FAST_WIN_FACTOR * median_ticks:
            flags.append("fast_win")
        if group.vm_disabled_suspect_seats.sum():
            flags.append("vm_disabled_suspect")
        if group.kills_friendly.max() >= FRIENDLY_KILLS_FLAG:
            flags.append("friendly_kills")
        if first.lead_changes >= COMEBACK_LEAD_CHANGES:
            flags.append("comeback")
        if ours and (group.key.eq(ours) | group.label.str.startswith(ours + ":")).any():
            mine = group[group.key.eq(ours) | group.label.str.startswith(ours + ":")].iloc[0]
            if mine.result != "win":
                flags.append("ours_lost")
        if flags:
            ember, azure = group[group.team == 0].iloc[0], group[group.team == 1].iloc[0]
            summary = (f"{ember.label} (ember) {ember.glory_ours} vs {azure.label} (azure) {azure.glory_ours}, "
                       f"{first.ticks} ticks ({first.ticks / 24:.0f} s)")
            flagged.append({"episode_id": episode_id, "flags": flags, "summary": summary,
                            "detail_url": first.detail_url, "replay_viewer_url": first.replay_viewer_url,
                            "reason": None})
    rarity = Counter(f for item in flagged for f in item["flags"])
    flagged.sort(key=lambda item: min(rarity[f] for f in item["flags"]))
    return flagged[:INTERESTING_MAX]


def sanity(sides_frame: pd.DataFrame, table: list[dict], profiles: dict, exclusions: Counter) -> list[str]:
    warnings = []
    versions = sides_frame.coworld_version.dropna().unique()
    rules = sides_frame.rules.unique()
    if len(versions) > 1 or len(rules) > 1:
        warnings.append(f"mixed versions in one report: coworld {sorted(map(str, versions))}, rules {sorted(map(int, rules))}; "
                        "do not pool across rules versions")
    decided = sides_frame[sides_frame.result == "win"]
    if len(decided) >= 6 and decided.team.nunique() == 1:
        warnings.append(f"every decided episode was won by {pw_episodes.TEAM_NAMES[int(decided.team.iloc[0])]}: "
                        "side bias or a team/parity bug; check before believing")
    rated = [r for r in table if r["episodes"] >= 3]
    if len(rated) >= 3 and all(r["win_rate"] in (0.0, 1.0) for r in rated):
        warnings.append("every policy with >= 3 episodes has a flat 0% or 100% win rate: treat as a tooling bug until disproved")
    for field in ("shots_per_episode", "kills_per_episode", "engagements_per_episode"):
        if profiles and all(not p[field] for p in profiles.values()):
            warnings.append(f"{field} is zero for every policy: a tooling bug until disproved")
    if profiles and all(not any((p["pickups_per_episode"] or {}).values()) for p in profiles.values()):
        warnings.append("no pickups for any policy: a tooling bug until disproved")
    if exclusions:
        warnings.append(f"episodes excluded from the matrix: {dict(exclusions)}")
    return warnings


# ================================================================ rendering

def _pct(value) -> str:
    return "-" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:.0%}"


def _num(value, spec=".2f") -> str:
    return "-" if value is None or (isinstance(value, float) and math.isnan(value)) else format(value, spec)


def render_markdown(report: dict) -> str:
    lines = [f"# Paintbot PW scout: {report['title']}", "",
             f"Generated {report['generated_at']} from {report['episodes']} verified episodes "
             f"({report['failed']} failed to load, {report['excluded']} excluded). Identity: "
             f"{'exact policy version' if report['by'] == 'version' else 'policy NAME (versions pooled)'}. "
             "Elo outcome = clamp(0.5 + (our glory - their glory)/2000, 0, 1), the ladder's per-episode score.", ""]
    if report["sanity"]:
        lines += ["## Sanity warnings", ""] + [f"- {w}" for w in report["sanity"]] + [""]
    lines += ["## Standings", "", "| Policy | n | W-D-L | Win rate (95% Wilson) | Mean Elo outcome | Winning glory | Median win |",
              "| --- | ---: | ---: | --- | --- | ---: | ---: |"]
    for r in report["standings"]:
        half = f" ± {r['elo_outcome_half_width']:.2f}" if r["elo_outcome_half_width"] is not None else ""
        lines.append(f"| {r['label']} | {r['episodes']} | {r['wins']}-{r['draws']}-{r['losses']} | "
                     f"{_pct(r['win_rate'])} ({_pct(r['win_rate_low'])}-{_pct(r['win_rate_high'])}) | "
                     f"{_num(r['elo_outcome_mean'])}{half} | {_num(r['winning_glory_mean'], '.0f')} | "
                     f"{_num(r['win_ticks_median'] / 24 if r['win_ticks_median'] else None, '.0f')} s |")
    labels = [r["label"] for r in report["standings"]]
    keys = [r["key"] for r in report["standings"]]
    cells = {(m["key"], m["opponent_key"]): m for m in report["matrix"]}
    lines += ["", "## Matrix", "", "Row policy against column policy: W-L and the row's mean Elo outcome. "
              "Wilson intervals per cell are in scout.json.", "",
              "| vs | " + " | ".join(labels) + " |", "| --- |" + " ---: |" * len(labels)]
    for key, label in zip(keys, labels):
        row = []
        for other in keys:
            m = cells.get((key, other))
            row.append(f"{m['wins']}-{m['losses']} · {m['elo_outcome_mean']:.2f}" if m else "")
        lines.append(f"| {label} | " + " | ".join(row) + " |")
    lines += ["", "## Profiles", "",
              "Hearts and pickups are in Ember's frame: h0 = the side's own home heart, h1 = the enemy home. "
              "Ember-frame positions: " + ", ".join(f"{k} {v}" for k, v in report["hearts_legend"].items()) + ".", ""]
    for key, label in zip(keys, labels):
        p = report["profiles"][key]
        g = p["glory_per_episode"]
        lines += [f"### {label}", "",
                  f"- **Opening** ({p['episodes']} episodes, sides {p['sides']}): first walk targets per episode "
                  f"{p['opening_first_targets_per_episode']}; first hearts reached within 30 s "
                  f"{p['first_hearts_reached_per_episode']}; first capture started {p['first_capture_start']}; "
                  f"capture order {p['capture_order']}.",
                  f"- **Weapons**: HP share by weapon {p['weapon_hp_share']}; gun accuracy "
                  f"{_pct(p['gun_enemy_accuracy'])} on {p['shots_per_episode']:.0f} shots/ep; grenades "
                  f"{_num(p['grenades_per_episode'], '.1f')}/ep ({_pct(p['grenade_effective_share'])} hurt an enemy); "
                  f"spray bursts {_num(p['spray_bursts_per_episode'], '.1f')}/ep ({_pct(p['spray_effective_share'])} effective).",
                  f"- **Fights**: kills {p['kills_per_episode']:.1f}/ep, deaths {p['deaths_per_episode']:.1f}/ep, "
                  f"K/D {_num(p['kd'])}; engagements {p['engagements_per_episode']:.1f}/ep, won "
                  f"{_pct(p['engagement_win_share'])}, hit first {_pct(p['first_hit_share'])} (won "
                  f"{_pct(p['won_when_first_hit_share'])} of those); opening duels won {_pct(p['opening_duel_win_share'])}; "
                  f"trades {_num(p['trade_kills_per_episode'], '.1f')}/ep; friendly kills "
                  f"{_num(p['friendly_kills_per_episode'], '.1f')}/ep.",
                  f"- **Hearts**: reach share {_pct(p['heart_reach_share'])} of alive time; hearts held mean "
                  f"{_num(p['hearts_held_mean'])}; first capture median {_num(p['first_capture_seconds_median'], '.0f')} s; "
                  f"median win {_num(p['win_seconds_median'], '.0f')} s, loss {_num(p['loss_seconds_median'], '.0f')} s.",
                  "- **Pickups/ep**: " + ", ".join(f"{k} {_num(v, '.1f')}" for k, v in p["pickups_per_episode"].items()) + ".",
                  f"- **Glory/ep**: start {_num(g['initial'], '.0f')} − countdown {_num(g['countdown'], '.0f')} + "
                  + " + ".join(f"{_num(g[k], '.0f')} {k}" for k in pw_metrics.GLORY_KINDS)
                  + f" − settled {_num(g['settled_loss'], '.0f')} = {_num(g['ours'], '.0f')}.",
                  f"- **Shouts**: {_num(p['shouts_per_seat_minute'], '.1f')} per seat-minute, "
                  f"{_pct(p['shouts_heard_by_enemy_share'])} within enemy earshot; idle share {_pct(p['idle_share'])}.", ""]
        templates = report["shouts"].get(key, [])
        if templates:
            lines += ["| Shout template | Count | Eps | Enemy heard | Slot hints | Example (t, seat, pos) |",
                      "| --- | ---: | ---: | ---: | --- | --- |"]
            for s in templates[:8]:
                hints = "; ".join(f"#{h['slot']} {h['tracks'] or '?'} [{h['min']:.0f}..{h['max']:.0f}]"
                                  for h in s["slots"]) or "-"
                e = s["examples"][0]
                lines.append(f"| `{s['template']}` | {s['count']} | {s['episodes']} | {_pct(s['heard_by_enemy_share'])} | "
                             f"{hints} | `{e['text']}` (t {e['t']}, seat {e['seat']}, {e['pos']}) |")
            lines.append("")
    lines += ["## Interesting episodes", ""]
    for item in report["interesting"]:
        reason = item["reason"] or "(no reason yet: write one in reasons.json and re-render)"
        link = item["detail_url"] or item["replay_viewer_url"] or ""
        lines.append(f"- `{item['episode_id']}` [{', '.join(item['flags'])}] {item['summary']}. {reason} {link}")
    lines += ["", "## Thresholds", "", ", ".join(f"{k}={v}" for k, v in report["thresholds"].items()), ""]
    return "\n".join(lines)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(value) else float(value)
    if value is pd.NA:
        return None
    return value


def report(roots: list[Path], out: Path, reasons_path: Path | None, by: str, ours: str | None,
           vis_every: int, tag: str | None, title: str | None, envelope=None) -> dict | None:
    envelope = envelope or pw_cli.Report("pw_scout")
    options = pw_episodes.TraceOptions(vis_every=vis_every)
    batch = pw_episodes.load_batch(roots, tag=tag, options=options)
    envelope.add_batch(batch)
    for path, code, message in batch.failures:
        print(f"FAILED [{code}] {path}: {message}")
    if not batch.episodes:
        print("no verified episodes")
        envelope.exit_code = pw_cli.EXIT_PARTIAL
        return None
    if ours and ours not in pw_cli.policy_values(batch.episodes):
        raise pw_cli.UsageError(f"--ours {ours!r} is not in these episodes", pw_cli.policy_values(batch.episodes))
    data = collect(batch, by)
    frame = data["sides"]
    table = standings(frame)
    profiles = {key: profile(parts) for key, parts in data["profiles"].items()}
    shouts = {key: decode_shouts(pd.concat(parts, ignore_index=True)) for key, parts in data["shouts"].items()}
    flagged = interesting(frame, table, ours)
    reasons = json.loads(reasons_path.read_text()) if reasons_path else {}
    for item in flagged:
        item["reason"] = reasons.get(item["episode_id"])
    extra = [e for e in reasons if e not in {i["episode_id"] for i in flagged}]
    result = {
        "title": title or f"{len(batch.episodes)} public league episodes",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "roots": [str(r) for r in roots], "by": by, "episodes": len(batch.episodes),
        "failed": len(batch.failures), "failures": batch.failures,
        "excluded": sum(data["exclusions"].values()), "exclusions": dict(data["exclusions"]),
        "sanity": sanity(frame, table, profiles, data["exclusions"] + batch.exclusions),
        "standings": table, "matrix": matrix(frame), "profiles": profiles, "shouts": shouts,
        "interesting": flagged, "reasons_not_flagged": extra, "thresholds": REPORT_THRESHOLDS,
        "hearts_legend": {f"h{h['idx']}": h["pos"] for h in batch.episodes[0].meta["hearts"]},
        "fight_thresholds": pw_fights.THRESHOLDS,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "scout.json").write_text(json.dumps(_jsonable(result), indent=1) + "\n")
    (out / "scout.interesting.json").write_text(json.dumps(_jsonable(flagged), indent=1) + "\n")
    (out / "scout.md").write_text(render_markdown(_jsonable(result)))
    print(f"{len(batch.episodes)} episodes ({len(batch.failures)} failed, {result['excluded']} excluded)")
    for r in table:
        print(f"  {r['label']:32} n {r['episodes']:3}  W-D-L {r['wins']}-{r['draws']}-{r['losses']}  "
              f"win {_pct(r['win_rate'])} [{_pct(r['win_rate_low'])}-{_pct(r['win_rate_high'])}]  "
              f"elo outcome {_num(r['elo_outcome_mean'])}")
    for w in result["sanity"]:
        print(f"  SANITY: {w}")
    missing = [i["episode_id"] for i in flagged if not i["reason"]]
    print(f"{len(flagged)} interesting episodes ({len(missing)} without a reason: write reasons.json, re-run with --reasons)")
    if extra:
        print(f"  reasons for episodes not flagged (kept in scout.json): {extra}")
    print(f"wrote {out}/scout.md, scout.json, scout.interesting.json")
    for name in ("scout.json", "scout.md", "scout.interesting.json"):
        envelope.output(out / name)
    envelope.counts["excluded"] = result["excluded"]
    if missing:
        envelope.suggest(f"write {out}/reasons.json ({{episode_id: one-sentence reason}}), then rerun with "
                         f"--reasons {out}/reasons.json")
    return {"report": str(out / "scout.json"), "standings": _jsonable(table), "sanity": result["sanity"],
            "interesting_without_reason": missing}


# ================================================================ CLI

def build_parser() -> pw_cli.ArgumentParser:
    parser = pw_cli.ArgumentParser("pw_scout", __doc__, examples=[
        "uv run python paintbot_pw_lab/tools/pw_scout.py leaders --top 3 --json",
        "uv run python paintbot_pw_lab/tools/pw_scout.py fetch --max-episodes 30 --json",
        "uv run python paintbot_pw_lab/tools/pw_scout.py report paintbot_pw_lab/episode_data/scout/2026-09-29 --json",
        "uv run python paintbot_pw_lab/tools/pw_scout.py report ROOT --ours james-pw --reasons ROOT/reasons.json"])
    sub = parser.add_subparsers(dest="command", required=True)
    ld = sub.add_parser("leaders", help="current champions as policy refs (resolving --opponent for an A/B)")
    ld.add_argument("--division", default=COMPETITION_DIVISION,
                    help=f"division id (default the paintbot-pw Competition division {COMPETITION_DIVISION})")
    ld.add_argument("--top", type=int, help="only the N best-ranked entrants, e.g. --top 3")
    f = sub.add_parser("fetch", help="download recent public league episodes (anonymous, gentle)")
    f.add_argument("--league", default=MAIN_LEAGUE, help=f"league id (default the teams ladder {MAIN_LEAGUE})")
    f.add_argument("--max-episodes", type=int, default=MAX_EPISODES_DEFAULT,
                   help=f"episodes to keep, at most {MAX_EPISODES_CAP} (default %(default)s), e.g. --max-episodes 10")
    f.add_argument("--max-rounds", type=int, default=MAX_ROUNDS_DEFAULT,
                   help="most recent completed rounds to read (default %(default)s)")
    f.add_argument("--out", type=Path,
                   help=f"directory (default {DEFAULT_OUT}/<UTC date>; one dir per episode inside)")
    f.add_argument("--tag", help="pw_trace build for the guard trace (default: tools/release.env)")
    r = sub.add_parser("report", help="matrix, profiles, shout decode, flagged episodes")
    r.add_argument("roots", nargs="+", type=Path, help="episode or batch directories, e.g. the fetch --out")
    r.add_argument("--out", type=Path, help="report directory (default: the first root)")
    r.add_argument("--reasons", type=Path, help="JSON {episode_id: one-sentence reason}, e.g. ROOT/reasons.json")
    r.add_argument("--by", choices=("version", "name"), default="version",
                   help="policy identity: exact version (default) or name (pools versions)")
    r.add_argument("--ours", help="our policy_version_id or policy_name: flags our losses (exit 2 if absent)")
    r.add_argument("--vis-every", type=int, default=0, help="trace visibility every M ticks (saw-first in fights)")
    r.add_argument("--title", help="report title (default: '<n> public league episodes')")
    r.add_argument("--tag", help="pw_trace build (default: tools/release.env)")
    return parser


def run_cli(args, envelope: pw_cli.Report):
    if args.command == "leaders":
        if args.top is not None and args.top < 1:
            raise pw_cli.UsageError("--top must be at least 1")
        return leaders(args.division, args.top, envelope)
    if args.command == "fetch":
        if args.max_episodes > MAX_EPISODES_CAP:
            raise pw_cli.UsageError(f"--max-episodes above {MAX_EPISODES_CAP} is not polite to the public API; "
                                    "split the pull")
        out = args.out or DEFAULT_OUT / dt.datetime.now(dt.timezone.utc).date().isoformat()
        try:
            return fetch(args.league, out, args.max_episodes, args.max_rounds, args.tag, envelope)
        except pw_public.RateLimited as error:
            raise pw_cli.RateLimited(f"public API rate limit ({error})",
                                     "the same fetch command (episodes already saved are skipped)") from error
        except pw_public.PublicFetchError as error:
            raise pw_cli.EnvironmentMissing(f"public API read failed ({error})",
                                            "the same command again later (network or rate limit)") from error
    return report(args.roots, args.out or args.roots[0], args.reasons, args.by, args.ours, args.vis_every,
                  args.tag, args.title, envelope)


def main(argv: list[str] | None = None) -> int:
    return pw_cli.run(build_parser(), run_cli, argv)


if __name__ == "__main__":
    sys.exit(main())
