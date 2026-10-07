#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_episodes.py
"""Paintbot PW episode reader, trace cache and tables.

Reads downloaded episodes, re-simulates each tape with the hash-checked `pw_trace`
expander, caches the result under a receipt, and turns it into per-episode Parquet
tables. Contract (tables, columns, API): paintbot_pw_lab/docs/tools/tables.md.

Episode sources:
  hosted  a directory with episode.json and/or results.json (what fetch_artifacts.py
          writes) plus the tape under any fetcher name (replay.json, replay.json.z,
          replay, replay.bin, replay.gz, *.replay). The tape may be raw, gzip or zlib.
  local   a `NAME.replay` file recorded by paintbot-headless, with an optional sidecar
          `NAME.meta.json` naming the policy on each seat (see tables.md).

Rules the reader enforces (lab doctrine):
  * every number comes from a trace whose summary says verified (hash-checked per tick,
    glory and kill identities); a failed episode is listed and the CLI exits 1;
  * seats join by `position`, never array order; team comes from game_config.slots and
    must agree with the engine's seat parity, else the episode fails;
  * policy identity is the exact policy_version_id;
  * results.json (or episode.json participant_scores) must agree with the trace.

CLI:
  uv run python paintbot_pw_lab/tools/pw_episodes.py ROOT [ROOT ...] [--tag coworld-vX.Y.Z]
      [--binary PATH] [--state-every N] [--vis-every M] [--window A:B] [--refresh]
      [--jobs J] [--sql "select ..."] [--json]
Exit codes: 0 ok; 1 some episodes failed (listed, the rest used); 2 usage (bad options, no
episode under the roots, a --sql query DuckDB rejects); 3 pw_trace not built for the tag.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli  # noqa: E402
import pw_release  # noqa: E402
import pw_terrain  # noqa: E402

TOOLS = Path(__file__).resolve().parent
LAB = TOOLS.parent
DEFAULT_TAG = pw_release.current_tag()  # tools/release.env, shared with build_tools.sh
CACHE_VERSION = 1   # bump when the receipt signature changes
TABLES_VERSION = 3  # bump when any table's columns or meaning change
REPLAY_NAMES = ("replay.json", "replay.json.z", "replay", "replay.bin", "replay.gz")
MAGIC = b"POLYWORLDREPLAY"
CACHE_DIR = "pw_cache"
TEAM_NAMES = ("ember", "azure")           # engine team 0 / 1 (seat parity)
SLOT_TEAMS = {"red": 0, "ember": 0, "blue": 1, "azure": 1}
POLICY_LOG_PATTERNS = (re.compile(r"policy_agent_(\d+)\.log$"), re.compile(r"player-(\d+)\.log$"),
                       re.compile(r"seat[-_](\d+)\.log$"))
INTENT_PREFIX = "PWI "   # intent telemetry line prefix (reference/intent_telemetry.bas) (see tables.md, policy_log)

TABLES = ("episodes", "seats", "events", "shots", "damage", "kills", "spawns", "pickups",
          "captures", "glory", "shouts", "states", "team_states", "heart_states",
          "visibility", "policy_log")


class EpisodeError(ValueError):
    """An episode that cannot be used as evidence. Always listed, never skipped silently.

    `code` is the exclusion category counted by load_batch: no_replay, bad_tape,
    trace_failed, identity (roster/team/join), results_mismatch."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- inputs and tape

def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def raw_tape(data: bytes) -> bytes:
    """Return the raw POLYWORLDREPLAY bytes from raw, gzip or zlib input."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif data[:1] == b"\x78" and not data.startswith(MAGIC):
        data = zlib.decompress(data)
    if not data.startswith(MAGIC):
        raise EpisodeError("not a POLYWORLDREPLAY tape", "bad_tape")
    return data


def tape_header(data: bytes) -> dict:
    """The common header: file format, game version (= rules; 1000+ for FFA-kin), game name."""
    offset = len(MAGIC)
    file_format, game_version, name_length = struct.unpack_from("<HHH", data, offset)
    name = data[offset + 6: offset + 6 + name_length].decode()
    return {"file_format": file_format, "game_version": game_version, "game": name,
            "rules": game_version % 1000, "ffa": game_version >= 1000}


@dataclass
class Source:
    """Where one episode's inputs live."""
    kind: str                    # "hosted" | "local"
    replay: Path | None
    episode_json: Path | None
    results_json: Path | None
    local_meta: Path | None
    cache: Path                  # directory holding trace, receipt and tables (the default variant)
    log_dir: Path | None


def in_cache(path: Path) -> bool:
    """True for a path inside any trace cache (pw_cache/, pw_cache@*/, NAME*.pw_cache/, temp dirs)."""
    return any(part == CACHE_DIR or part.startswith(CACHE_DIR + "@") or ".pw_cache" in part for part in path.parts)


def find_replay(directory: Path) -> Path | None:
    for name in REPLAY_NAMES:
        path = directory / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    replays = sorted(directory.glob("*.replay"))
    return replays[0] if replays else None


def discover(roots: list[Path]) -> list[Source]:
    """Every episode under the roots: hosted directories and local .replay files.

    Raises pw_cli.UsageError when a root is a non-episode file or nothing is found."""
    sources: dict[Path, Source] = {}
    for root in roots:
        root = root.resolve()
        if root.is_file():
            if root.suffix == ".replay":
                sources[root] = local_source(root)
                continue
            raise pw_cli.UsageError(f"not an episode directory or .replay file: {root}")
        hosted_dirs = {p.parent for name in ("episode.json", "results.json") for p in root.rglob(name)
                       if not in_cache(p)}
        for directory in hosted_dirs:
            sources[directory] = Source("hosted", find_replay(directory),
                                        _file(directory / "episode.json"), _file(directory / "results.json"),
                                        None, directory / CACHE_DIR, directory)
        for replay in root.rglob("*.replay"):
            if replay.parent in hosted_dirs or in_cache(replay):
                continue
            sources[replay] = local_source(replay)
    if not sources:   # a wrong path, not a bad episode: a usage error (CLI exit 2)
        missing = [str(r) for r in roots if not Path(r).exists()]
        raise pw_cli.UsageError(f"no episodes under {', '.join(map(str, roots))}"
                                + (f" (does not exist: {', '.join(missing)})" if missing else "")
                                + "; an episode is a directory with episode.json or results.json plus a tape, "
                                  "or a NAME.replay file")
    return [sources[key] for key in sorted(sources)]


def local_source(replay: Path) -> Source:
    meta = replay.with_name(replay.stem + ".meta.json")
    return Source("local", replay, None, None, _file(meta), replay.with_name(replay.stem + ".pw_cache"),
                  replay.parent)


def _file(path: Path) -> Path | None:
    return path if path.is_file() else None


def _read_json(path: Path | None) -> dict | None:
    return json.loads(path.read_text()) if path else None


def resolve_binary(tag: str | None, binary: Path | None) -> Path:
    """The pw_trace to use. Raises pw_release.NotBuilt (CLI exit 3, names the build command)
    when the release's binary is missing, pw_cli.UsageError (exit 2) for a bad --binary."""
    if binary is None:
        return pw_release.require_built("pw_trace", tag)
    if not binary.is_file():
        raise pw_cli.UsageError(f"--binary {binary} does not exist")
    return binary


# ---------------------------------------------------------------- trace + cache

@dataclass
class TraceOptions:
    state_every: int = 6
    vis_every: int = 0
    window: tuple[int, int] | None = None

    def args(self) -> list[str]:
        args = ["--state-every", str(self.state_every)]
        if self.vis_every:
            args += ["--vis-every", str(self.vis_every)]
        if self.window:
            args += ["--window", f"{self.window[0]}:{self.window[1]}"]
        return args

    def signature(self) -> dict:
        return {"state_every": self.state_every, "vis_every": self.vis_every,
                "window": list(self.window) if self.window else None}


def cache_variant(tag: str | None, binary: Path, options: TraceOptions) -> str:
    """'' for the default trace (the pinned release's pw_trace, default options), else a readable
    key naming what differs: the tag (or `bin-<sha>` for an explicit --binary) and the options.

    Each variant has its own cache directory, so tracing an episode with another tag or a finer
    sampling (pw_viz --fine, pw_intent's dense trace, --vis-every) never replaces another one."""
    parts = []
    tag = tag or DEFAULT_TAG
    if binary.resolve() != (pw_release.bin_dir(tag) / "pw_trace").resolve():
        parts.append("bin-" + digest(binary)[:10])
    elif tag != DEFAULT_TAG:
        parts.append(tag)
    if options != TraceOptions():
        key = f"se{options.state_every}-ve{options.vis_every}"
        if options.window:
            key += f"-w{options.window[0]}_{options.window[1]}"
        parts.append(key)
    return "+".join(parts)


def variant_cache(source: Source, variant: str) -> Path:
    """The cache directory of one variant: pw_cache/ (default) or pw_cache@<variant>/ in a
    hosted episode dir; NAME.pw_cache/ or NAME@<variant>.pw_cache/ beside a local NAME.replay."""
    if not variant:
        return source.cache
    if source.kind == "hosted":
        return source.cache.with_name(f"{CACHE_DIR}@{variant}")
    return source.replay.with_name(f"{source.replay.stem}@{variant}.pw_cache")


def signature(source: Source, binary: Path, options: TraceOptions) -> dict:
    optional = {name: digest(path) if path else None for name, path in
                (("episode", source.episode_json), ("results", source.results_json),
                 ("local_meta", source.local_meta))}
    return {"cache_version": CACHE_VERSION, "tables_version": TABLES_VERSION,
            "replay": digest(source.replay), **optional, "binary": digest(binary),
            "options": options.signature()}


def cached(source: Source, expected: dict) -> bool:
    receipt = source.cache / "receipt.json"
    try:
        saved = json.loads(receipt.read_text())
        if saved["inputs"] != expected:
            return False
        for name, sha in saved["outputs"].items():
            if digest(source.cache / name) != sha:
                return False
        return True
    except (OSError, ValueError, KeyError):
        return False  # a missing or corrupt cache is rebuilt, never used as evidence


def run_trace(source: Source, binary: Path, options: TraceOptions, work: Path, tag: str | None = None) -> Path:
    """tag: the release whose shared terrain cache the trace uses (pw_terrain.py); None = uncached."""
    tape = raw_tape(source.replay.read_bytes())
    header = tape_header(tape)
    if header["game"] != "paintbot_pw":
        raise EpisodeError(f"tape is for game {header['game']!r}", "bad_tape")
    raw = work / "tape.replay"
    raw.write_bytes(tape)
    out = work / "trace.jsonl"
    with pw_terrain.session(tag) as terrain_dir:
        run = subprocess.run([str(binary), str(raw), str(out), *options.args()], capture_output=True, text=True,
                             env=pw_terrain.env(terrain_dir))
    raw.unlink()
    if run.returncode:
        detail = run.stderr.strip() or run.stdout.strip()
        raise EpisodeError(f"pw_trace failed (rules {header['rules']}): {detail[-400:]}", "trace_failed")
    return out


# ---------------------------------------------------------------- tables

def read_trace(path: Path) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    meta = summary = None
    events, states, visibility = [], [], []
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            kind = row["type"]
            if kind == "event":
                events.append(row)
            elif kind == "state":
                states.append(row)
            elif kind == "visibility":
                visibility.append(row)
            elif kind == "meta":
                meta = row
            elif kind == "summary":
                summary = row
    if not meta or not summary or not summary.get("verified"):
        raise EpisodeError(f"trace not verified: {summary and summary.get('failure')}", "trace_failed")
    return meta, events, states, visibility, summary


def seat_identity(source: Source, meta: dict, episode: dict | None, local: dict | None) -> tuple[list[dict], list[str]]:
    """One identity row per seat, joined by position; team cross-checked against parity."""
    seats = meta["seats"]
    notes: list[str] = []
    rows = [{"seat": i, "engine_team": i % 2, "tape_name": meta["names"][i]} for i in range(seats)]
    if episode is not None:
        participants = {p["position"]: p for p in episode.get("participants", [])}
        if sorted(participants) != list(range(seats)):
            raise EpisodeError(f"participants positions {sorted(participants)} do not cover {seats} seats", "identity")
        slots = (episode.get("game_config") or {}).get("slots")
        if slots is not None and len(slots) != seats:
            raise EpisodeError(f"game_config.slots has {len(slots)} entries for {seats} seats", "identity")
        if slots is None:
            notes.append("team_from_parity: episode.json has no game_config.slots")
        for row in rows:
            p = participants[row["seat"]]
            row.update(policy_version_id=p.get("policy_version_id"), policy_id=p.get("policy_id"),
                       policy_name=p.get("policy_name"), policy_version=p.get("version"),
                       player_name=p.get("player_name"), is_filler=p.get("is_filler"),
                       participant_kind=p.get("kind"))
            row["team_source"] = "game_config" if slots is not None else "engine_parity"
            if slots is not None:
                team = SLOT_TEAMS.get(str(slots[row["seat"]].get("team", "")).lower())
                if team is None:
                    raise EpisodeError(f"unknown slot team {slots[row['seat']]!r}", "identity")
                if team != row["engine_team"]:
                    raise EpisodeError(f"seat {row['seat']}: game_config team {team} != engine parity "
                                       f"{row['engine_team']}", "identity")
    else:
        by_position = {s["position"]: s for s in (local or {}).get("seats", [])}
        if not by_position:
            notes.append("anonymous_local: no .meta.json; policy identity is the tape name")
        for row in rows:
            s = by_position.get(row["seat"], {})
            row.update(policy_version_id=s.get("policy_version_id"), policy_id=None,
                       policy_name=s.get("policy_name", row["tape_name"]),
                       policy_version=s.get("policy_version"), player_name=None,
                       is_filler=None, participant_kind="local")
            row["team_source"] = "meta" if "team" in s else "engine_parity"
            if "team" in s and int(s["team"]) != row["engine_team"]:
                raise EpisodeError(f"seat {row['seat']}: meta team {s['team']} != engine parity", "identity")
    for row in rows:
        row["team"] = row["engine_team"]
        row["team_name"] = TEAM_NAMES[row["team"]]
        # Exact identity when the platform gives one; otherwise a clearly local key.
        row["policy_key"] = row["policy_version_id"] or f"local:{row['policy_name']}"
        row["name_matches_tape"] = (None if not row.get("player_name") else
                                    re.sub(r" \(\d+\)$", "", row["tape_name"]) == row["player_name"])
    return rows, notes


def check_results(meta: dict, summary: dict, results: dict | None, episode: dict | None) -> str:
    """Cross-check the platform's numbers against the trace. Returns how it was checked."""
    team_glory = summary["glory"]
    seats = meta["seats"]
    if results is not None:
        if results.get("ticks") != summary["ticks"]:
            raise EpisodeError(f"results ticks {results.get('ticks')} != trace {summary['ticks']}", "results_mismatch")
        if results.get("seed") is not None and results["seed"] != meta["seed"]:
            raise EpisodeError(f"results seed {results['seed']} != tape seed {meta['seed']}", "results_mismatch")
        scores = results.get("scores") or []
        if len(scores) != seats or any(float(scores[i]) != team_glory[i % 2] for i in range(seats)):
            raise EpisodeError(f"results scores {scores} != trace team glory {team_glory}", "results_mismatch")
        outcome = str(results.get("outcome"))
        if summary["winner"] in (0, 1) and outcome != str(summary["winner"]):
            raise EpisodeError(f"results outcome {outcome} != trace winner {summary['winner']}", "results_mismatch")
        return "results.json"
    if episode is not None and episode.get("participant_scores"):
        for row in episode["participant_scores"]:
            if float(row["score"]) != team_glory[row["position"] % 2]:
                raise EpisodeError(f"participant_scores {row} != trace team glory {team_glory}", "results_mismatch")
        return "participant_scores"
    return "none"


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def _xz(value, index: int):
    return value[index] if value is not None else None


def build_tables(episode_id: str, source: Source, meta: dict, events: list[dict], states: list[dict],
                 visibility: list[dict], summary: dict, episode: dict | None, results: dict | None,
                 local: dict | None) -> dict[str, pd.DataFrame]:
    identity, notes = seat_identity(source, meta, episode, local)
    results_check = check_results(meta, summary, results, episode)
    team_of = {row["seat"]: row["team"] for row in identity}
    team = lambda seat: team_of.get(seat) if seat is not None else None  # noqa: E731
    e = episode or {}
    gc = e.get("game_config") or {}
    ticks = summary["ticks"]
    checks = summary["checks"]
    glory_check = checks.get("glory") or {}

    episodes = _frame([{
        "episode_id": episode_id, "source": source.kind, "path": str(source.replay.parent if source.kind == "hosted" else source.replay),
        "platform_episode_id": e.get("episode_id"), "job_id": e.get("job_id"), "round_id": e.get("round_id"),
        "coworld_version": e.get("coworld_version"), "variant_name": e.get("variant_name"),
        "status": e.get("status"), "rules": meta["rules"], "mode": meta["mode"], "map": meta["map"],
        "vision": meta["vision"], "glory_config": json.dumps(meta["glory_config"], sort_keys=True),
        "seats": meta["seats"], "engine_seed": meta["seed"], "config_seed": gc.get("seed"),
        "end_tick": meta["end_tick"], "ticks": ticks, "winner": summary["winner"],
        "outcome": (results or {}).get("outcome"),
        "glory_0": summary["glory"][0], "glory_1": summary["glory"][1],
        "glory_initial_0": _xz(glory_check.get("initial"), 0), "glory_initial_1": _xz(glory_check.get("initial"), 1),
        "glory_unsettled_0": _xz(glory_check.get("unsettled"), 0), "glory_unsettled_1": _xz(glory_check.get("unsettled"), 1),
        "glory_countdown_0": _xz(glory_check.get("countdown"), 0), "glory_countdown_1": _xz(glory_check.get("countdown"), 1),
        "meter_ticks_0": summary["meter_ticks"][0], "meter_ticks_1": summary["meter_ticks"][1],
        "meter_target_ticks": meta["meter_target_ticks"], "tick_rate": meta["tick_rate"],
        "hearts": len(meta["hearts"]), "hearts_owned_0": summary["hearts_owned"][0], "hearts_owned_1": summary["hearts_owned"][1],
        "team_lives_0": summary["team_lives"][0], "team_lives_1": summary["team_lives"][1],
        "cogs_out_0": summary["cogs_out"][0], "cogs_out_1": summary["cogs_out"][1],
        "final_hash": summary["final_hash"], "engine_release": meta["engine_release"],
        "state_every": meta["options"]["state_every"], "vis_every": meta["options"]["vis_every"],
        "results_check": results_check, "notes": ";".join(notes), "cost_usd": e.get("cost_usd"),
    }], None)

    counters = {row["seat"]: row for row in summary["seats"]}
    seat_stats = summary.get("seat_stats")
    scores = (results or {}).get("scores")
    seat_rows = []
    for row in identity:
        i = row["seat"]
        extra = {k: v for k, v in counters[i].items() if k not in ("seat", "team")}
        stats = {f"ss_{k}": v for k, v in (seat_stats[i].items() if seat_stats else [])}
        seat_rows.append({"episode_id": episode_id, **{k: v for k, v in row.items() if k != "engine_team"},
                          "score": float(scores[i]) if scores else float(summary["glory"][i % 2]),
                          **extra, **stats})
    seats = pd.DataFrame(seat_rows)

    def rows_of(kinds, build):
        return [build(ev) for ev in events if ev["kind"] in kinds]

    base = lambda ev: {"episode_id": episode_id, "t": ev["t"]}  # noqa: E731
    event_rows = [{**base(ev), "kind": ev["kind"], "seat": ev.get("seat"), "team": team(ev.get("seat")),
                   "data": json.dumps({k: v for k, v in ev.items() if k not in ("type", "t", "kind", "seat")})}
                  for ev in events]
    events_df = _frame(event_rows, ["episode_id", "t", "kind", "seat", "team", "data"])

    shots = _frame(rows_of({"fire"}, lambda ev: {
        **base(ev), "seat": ev["seat"], "team": team(ev["seat"]),
        "origin_x": ev["origin"][0], "origin_z": ev["origin"][1], "end_x": ev["end"][0], "end_z": ev["end"][1],
        "ray_length": ev["length"], "gun_aim_x": ev["gun_aim"][0], "gun_aim_z": ev["gun_aim"][1],
        "hit": ev["hit"], "victim": ev["victim"], "victim_team": team(ev["victim"]),
        "friendly": ev["victim"] is not None and team(ev["victim"]) == team(ev["seat"]),
        "aim_target": ev["aim_target"], "aim_target_distance": ev["aim_target_distance"],
        "aim_target_across": ev["aim_target_across"],
        "inferred_shielded": ev["inferred_shielded"], "inferred_dead": ev["inferred_dead"],
        "inferred_trench_dodge": ev["inferred_trench_dodge"]}),
        ["episode_id", "t", "seat", "team", "origin_x", "origin_z", "end_x", "end_z", "ray_length",
         "gun_aim_x", "gun_aim_z", "hit", "victim", "victim_team", "friendly", "aim_target",
         "aim_target_distance", "aim_target_across", "inferred_shielded", "inferred_dead", "inferred_trench_dodge"])
    # Hit distance comes from the damage event (exact positions at damage time).
    damage = _frame(rows_of({"damage"}, lambda ev: {
        **base(ev), "seat": ev["seat"], "team": team(ev["seat"]), "victim": ev["victim"],
        "victim_team": team(ev["victim"]), "weapon": ev["weapon"], "hp_removed": ev["hp_removed"],
        "armor_absorbed": ev["armor_absorbed"], "killed": ev["killed"], "friendly": ev["friendly"],
        "self": ev["self"], "distance": ev["distance"],
        "attacker_x": _xz(ev["attacker_pos"], 0), "attacker_z": _xz(ev["attacker_pos"], 1),
        "victim_x": ev["victim_pos"][0], "victim_z": ev["victim_pos"][1]}),
        ["episode_id", "t", "seat", "team", "victim", "victim_team", "weapon", "hp_removed", "armor_absorbed",
         "killed", "friendly", "self", "distance", "attacker_x", "attacker_z", "victim_x", "victim_z"])
    if len(shots):
        gun_hits = damage[damage.weapon == "gun"][["t", "seat", "victim", "distance"]]
        shots = shots.merge(gun_hits.rename(columns={"distance": "hit_distance"}),
                            on=["t", "seat", "victim"], how="left")
    else:
        shots["hit_distance"] = pd.Series(dtype="float")
    kills = _frame(rows_of({"kill"}, lambda ev: {
        **base(ev), "seat": ev["seat"], "team": team(ev["seat"]), "victim": ev["victim"],
        "victim_team": team(ev["victim"]), "weapon": ev["weapon"], "friendly": ev["friendly"],
        "self": ev["self"], "distance": ev["distance"], "victim_x": ev["victim_pos"][0], "victim_z": ev["victim_pos"][1]}),
        ["episode_id", "t", "seat", "team", "victim", "victim_team", "weapon", "friendly", "self", "distance",
         "victim_x", "victim_z"])
    spawns = _frame(rows_of({"spawn"}, lambda ev: {
        **base(ev), "seat": ev["seat"], "team": team(ev["seat"]), "x": ev["pos"][0], "z": ev["pos"][1],
        "lives": ev["lives"], "initial": ev["initial"], "near_owned_heart": ev.get("near_owned_heart")}),
        ["episode_id", "t", "seat", "team", "x", "z", "lives", "initial", "near_owned_heart"])
    pickups = _frame(rows_of({"pickup"}, lambda ev: {
        **base(ev), "seat": ev["seat"], "team": team(ev["seat"]), "pickup": ev["idx"], "pickup_kind": ev["pickup_kind"],
        "x": ev["pos"][0], "z": ev["pos"][1], "ambiguous": ev["ambiguous"]}),
        ["episode_id", "t", "seat", "team", "pickup", "pickup_kind", "x", "z", "ambiguous"])
    captures = _frame(rows_of({"capture_start", "capture_complete", "capture_reset", "contest_start", "contest_end"},
        lambda ev: {**base(ev), "kind": ev["kind"], "heart": ev["heart"],
                    "team": ev.get("team", ev.get("capture_team")), "seat": ev.get("seat"),
                    "previous_owner": ev.get("previous_owner"), "lost_ticks": ev.get("lost_ticks"),
                    "capture_ticks": ev.get("capture_ticks")}),
        ["episode_id", "t", "kind", "heart", "team", "seat", "previous_owner", "lost_ticks", "capture_ticks"])
    # Glory awards; a glory_heart award is credited to the seat that took the heart that tick.
    takers = {}
    for ev in events:
        if ev["kind"] == "glory_heart_taken":
            takers.setdefault((ev["t"], team(ev["seat"])), []).append(ev["seat"])
    glory_rows = []
    for ev in events:
        if ev["kind"] != "glory":
            continue
        seat = None
        if ev["glory_kind"] == "glory_heart":
            waiting = takers.get((ev["t"], ev["team"]), [])
            seat = waiting.pop(0) if waiting else None
        glory_rows.append({**base(ev), "team": ev["team"], "glory_kind": ev["glory_kind"],
                           "amount": ev["amount"], "engine_tick": ev["engine_tick"], "seat": seat})
    glory = _frame(glory_rows, ["episode_id", "t", "team", "glory_kind", "amount", "engine_tick", "seat"])
    shouts = _frame(rows_of({"shout"}, lambda ev: {
        **base(ev), "seat": ev["seat"], "team": team(ev["seat"]), "text": ev["text"], "bytes": ev["bytes"],
        "heard_by": ev["heard_by"],
        "heard_by_team": sum(team(j) == team(ev["seat"]) for j in ev["heard_by"]),
        "heard_by_enemy": sum(team(j) != team(ev["seat"]) for j in ev["heard_by"])}),
        ["episode_id", "t", "seat", "team", "text", "bytes", "heard_by", "heard_by_team", "heard_by_enemy"])

    columns = meta["state_columns"]
    state_rows, team_rows, heart_rows = [], [], []
    for st in states:
        for i, values in enumerate(st["seats"]):
            state_rows.append({"episode_id": episode_id, "t": st["t"], "seat": i, "team": i % 2,
                               **dict(zip(columns, values))})
        team_rows.append({"episode_id": episode_id, "t": st["t"],
                          "meter_ticks_0": st["meter_ticks"][0], "meter_ticks_1": st["meter_ticks"][1],
                          "glory_0": st["glory"][0], "glory_1": st["glory"][1],
                          "team_lives_0": st["team_lives"][0], "team_lives_1": st["team_lives"][1],
                          "cogs_out_0": st["cogs_out"][0], "cogs_out_1": st["cogs_out"][1],
                          "hearts_owned_0": sum(h[0] == 0 for h in st["hearts"]),
                          "hearts_owned_1": sum(h[0] == 1 for h in st["hearts"]), "winner": st["winner"]})
        for k, h in enumerate(st["hearts"]):
            heart_rows.append({"episode_id": episode_id, "t": st["t"], "heart": k,
                               **dict(zip(meta["heart_columns"], h))})
    states_df = pd.DataFrame(state_rows)
    if len(states_df):
        states_df["alive"] = states_df["hp"] > 0
    vis_rows = [{"episode_id": episode_id, "t": v["t"], "seat": i, "sees": seen}
                for v in visibility for i, seen in enumerate(v["sees"])]
    return {
        "episodes": episodes, "seats": seats, "events": events_df, "shots": shots, "damage": damage,
        "kills": kills, "spawns": spawns, "pickups": pickups, "captures": captures, "glory": glory,
        "shouts": shouts, "states": states_df, "team_states": pd.DataFrame(team_rows),
        "heart_states": pd.DataFrame(heart_rows),
        "visibility": _frame(vis_rows, ["episode_id", "t", "seat", "sees"]),
        "policy_log": policy_log(episode_id, source),
    }


# Stable column types across episodes (a column that is sometimes null must not flip
# between int64, float64 and object): nullable ints, booleans and strings.
INT_COLUMNS = {"t", "seat", "team", "victim", "victim_team", "aim_target", "aim_target_distance",
               "aim_target_across", "hit_distance", "near_owned_heart", "previous_owner", "lost_ticks",
               "capture_ticks", "heart", "pickup", "line_no", "engine_tick", "amount", "config_seed",
               "policy_version", "distance", "attacker_x", "attacker_z", "cmd_walk", "cmd_shoot",
               "cmd_direct", "cmd_sneak", "cmd_charge", "cmd_self_destruct", "sniper", "radar_until", "mister_until", "cmd_goal_x", "cmd_goal_z", "cmd_aim_x", "cmd_aim_z"}
BOOL_COLUMNS = {"is_filler", "name_matches_tape", "hit", "friendly", "self", "killed", "initial", "ambiguous"}
STRING_COLUMNS = {"episode_id", "kind", "data", "line_kind", "raw", "fields", "file", "policy_version_id",
                  "policy_id", "policy_name", "player_name", "outcome", "platform_episode_id", "job_id",
                  "round_id", "coworld_version", "variant_name", "status"}


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    for column in frame.columns:
        if column in INT_COLUMNS:
            frame[column] = frame[column].astype("Int64")
        elif column in BOOL_COLUMNS:
            frame[column] = frame[column].astype("boolean")
        elif column in STRING_COLUMNS:
            frame[column] = frame[column].astype("string")
    return frame


def policy_log(episode_id: str, source: Source) -> pd.DataFrame:
    """Intent telemetry (PWI lines) and VM errors from seat logs, when we have them.

    Seat logs exist only for our own seats (hosted) or local.py runs. An intent line is
    `PWI t=<tick> key=value ...` (format: reference/intent_telemetry.bas, docs/tools/pw_intent.md; unknown keys are kept). A line
    containing `BASIC error:` is a vm_error row. Each parsed log also gets one
    `log_present` row. Other prints are not stored."""
    rows = []
    if source.log_dir is not None:
        for path in sorted(source.log_dir.rglob("*.log")):
            seat = next((int(m.group(1)) for pattern in POLICY_LOG_PATTERNS if (m := pattern.search(path.name))), None)
            if seat is None or in_cache(path):
                continue
            # One marker row per log file, so "no vm_error rows" can be told apart from "no log".
            rows.append({"episode_id": episode_id, "seat": seat, "t": None, "line_kind": "log_present",
                         "raw": "", "fields": "{}", "file": path.name, "line_no": 0})
            with path.open(errors="replace") as stream:
                for number, line in enumerate(stream, 1):
                    line = line.rstrip("\n")
                    if line.startswith(INTENT_PREFIX):
                        fields = dict(item.split("=", 1) for item in line[len(INTENT_PREFIX):].split() if "=" in item)
                        tick = int(fields["t"]) if fields.get("t", "").lstrip("-").isdigit() else None
                        rows.append({"episode_id": episode_id, "seat": seat, "t": tick, "line_kind": "intent",
                                     "raw": line, "fields": json.dumps(fields), "file": path.name, "line_no": number})
                    elif "BASIC error:" in line:
                        rows.append({"episode_id": episode_id, "seat": seat, "t": None, "line_kind": "vm_error",
                                     "raw": line, "fields": "{}", "file": path.name, "line_no": number})
    return _frame(rows, ["episode_id", "seat", "t", "line_kind", "raw", "fields", "file", "line_no"])


# ---------------------------------------------------------------- episodes

@dataclass
class Episode:
    episode_id: str
    source: Source               # source.cache is the variant this load used
    meta: dict
    summary: dict
    tables: dict[str, pd.DataFrame]
    cache_hit: bool
    tag: str | None = None       # release build that traced it (None: an explicit --binary)

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]


def episode_id_of(source: Source) -> str:
    if source.kind == "hosted":
        episode = _read_json(source.episode_json)
        if episode and episode.get("id"):
            return episode["id"]
        return f"dir:{source.cache.parent.name}"
    return f"local:{source.replay.stem}"


def load_episode(source: Source | Path, binary: Path | None = None, *, tag: str | None = None,
                 options: TraceOptions | None = None, refresh: bool = False) -> Episode:
    """Trace (or reuse the cache for) one episode and return its tables. Raises EpisodeError."""
    if isinstance(source, Path):
        found = discover([source])
        if len(found) != 1:
            raise EpisodeError(f"{source} holds {len(found)} episodes; use load_batch")
        source = found[0]
    if source.replay is None:
        raise EpisodeError("no replay tape (fetch it again: a missing artifact is 'retry', not 'absent')",
                           "no_replay")
    binary = resolve_binary(tag, binary)
    options = options or TraceOptions()
    episode_id = episode_id_of(source)
    variant = cache_variant(tag, binary, options)
    source = replace(source, cache=variant_cache(source, variant))
    trace_tag = None if variant.startswith("bin-") else (tag or DEFAULT_TAG)
    expected = signature(source, binary, options)
    hit = not refresh and cached(source, expected)
    if not hit:
        source.cache.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".pw_cache-", dir=source.cache.parent))
        try:
            trace = run_trace(source, binary, options, work, trace_tag)
            meta, events, states, visibility, summary = read_trace(trace)
            tables = build_tables(episode_id, source, meta, events, states, visibility, summary,
                                  _read_json(source.episode_json), _read_json(source.results_json),
                                  _read_json(source.local_meta))
            (work / "tables").mkdir()
            for name, frame in tables.items():
                normalize(frame).to_parquet(work / "tables" / f"{name}.parquet", index=False)
            outputs = {str(p.relative_to(work)): digest(p) for p in sorted(work.rglob("*")) if p.is_file()}
            (work / "receipt.json").write_text(json.dumps({"inputs": expected, "outputs": outputs}, indent=1) + "\n")
            # Promote: the receipt is checked on read, so a crash mid-swap only forces a rebuild.
            shutil.rmtree(source.cache, ignore_errors=True)
            os.replace(work, source.cache)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    meta, _, _, _, summary = read_trace_header(source.cache / "trace.jsonl")
    tables = {name: pd.read_parquet(source.cache / "tables" / f"{name}.parquet") for name in TABLES}
    return Episode(episode_id, source, meta, summary, tables, hit, trace_tag)


def read_trace_header(path: Path) -> tuple[dict, None, None, None, dict]:
    """meta and summary rows only (first and last line)."""
    with path.open() as stream:
        meta = json.loads(stream.readline())
        last = None
        for last in stream:
            pass
    return meta, None, None, None, json.loads(last)


@dataclass
class Batch:
    episodes: list[Episode]
    failures: list[tuple[str, str, str]]       # (path, code, message) for every episode not loaded
    exclusions: Counter = field(default_factory=Counter)

    def table(self, name: str) -> pd.DataFrame:
        frames = [e.tables[name] for e in self.episodes if len(e.tables[name])]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_batch(roots: list[Path] | Path, binary: Path | None = None, *, tag: str | None = None,
               options: TraceOptions | None = None, refresh: bool = False, jobs: int | None = None) -> Batch:
    """Load every episode under the roots. Failures are collected, never dropped silently."""
    roots = [roots] if isinstance(roots, Path) else roots
    sources = discover(roots)
    binary = resolve_binary(tag, binary)
    jobs = jobs or max(1, (os.cpu_count() or 2) // 2)

    def one(source: Source):
        try:
            return load_episode(source, binary, tag=tag, options=options, refresh=refresh)
        except (EpisodeError, OSError, ValueError, KeyError) as error:
            where = source.replay or source.cache.parent
            code = error.code if isinstance(error, EpisodeError) else type(error).__name__
            return (str(where), code, str(error))

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        loaded = list(pool.map(one, sources))
    episodes = [x for x in loaded if isinstance(x, Episode)]
    failures = [x for x in loaded if not isinstance(x, Episode)]
    ids = Counter(e.episode_id for e in episodes)
    duplicates = {i for i, n in ids.items() if n > 1}
    if duplicates:
        raise EpisodeError(f"duplicate episode ids in batch: {sorted(duplicates)}")
    exclusions = Counter(code for _, code, _ in failures)
    return Batch(episodes, failures, exclusions)


def open_duckdb(where: "Batch | list[Path] | Path"):
    """A DuckDB connection with one view per table.

    Pass a loaded Batch (exactly its verified episodes, in the cache variant they were loaded
    with) or roots (the DEFAULT cache variant of every episode discover() finds that has
    tables; run load_batch first so the caches are current)."""
    import duckdb
    if isinstance(where, Batch):
        caches = [e.source.cache for e in where.episodes]
    else:
        caches = [s.cache for s in discover([where] if isinstance(where, Path) else list(where))]
    con = duckdb.connect()
    for name in TABLES:
        files = [str(c / "tables" / f"{name}.parquet") for c in caches if (c / "tables" / f"{name}.parquet").is_file()]
        if files:
            listing = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
            con.execute(f"create view {name} as select * from read_parquet([{listing}], union_by_name=true)")
    return con


# ---------------------------------------------------------------- CLI

def build_parser() -> pw_cli.ArgumentParser:
    parser = pw_cli.ArgumentParser("pw_episodes", __doc__, examples=[
        "uv run python paintbot_pw_lab/tools/pw_episodes.py paintbot_pw_lab/episode_data/20260928T214433_* --json",
        "uv run python paintbot_pw_lab/tools/pw_episodes.py EPISODE_DIR --window 1200:1500 --vis-every 24",
        "uv run python paintbot_pw_lab/tools/pw_episodes.py ROOT --sql \"select t, seat, victim from kills\" --json"])
    parser.add_argument("roots", nargs="+", type=Path,
                        help="episode dirs, batch dirs (searched recursively) or NAME.replay files")
    parser.add_argument("--tag", help=f"release build to trace with (default {DEFAULT_TAG}, from tools/release.env)")
    parser.add_argument("--binary", type=Path, help="explicit pw_trace binary (overrides --tag)")
    parser.add_argument("--state-every", type=int, default=6, help="state rows every N ticks (default %(default)s)")
    parser.add_argument("--vis-every", type=int, default=0,
                        help="visibility rows every M ticks, a multiple of --state-every (default off), e.g. 24")
    parser.add_argument("--window", help="also a state row at every tick in A:B, e.g. --window 1200:1500")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-trace")
    parser.add_argument("--jobs", type=int, help="parallel traces (default: half the cores)")
    parser.add_argument("--sql", help="run a DuckDB query over the batch's tables after loading, "
                                      "e.g. --sql \"select count(*) from kills\"")
    return parser


def parse_window(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    match = re.fullmatch(r"(\d+):(\d+)", text.strip())
    if not match or int(match[2]) < int(match[1]):
        raise pw_cli.UsageError(f"--window {text!r}: use ticks A:B with A <= B, e.g. 1200:1500")
    return int(match[1]), int(match[2])


def run_cli(args, report: pw_cli.Report):
    if args.state_every < 1 or args.vis_every < 0 or args.vis_every % args.state_every:
        raise pw_cli.UsageError(f"--state-every {args.state_every} / --vis-every {args.vis_every}: "
                                "--state-every must be >= 1 and --vis-every 0 or a multiple of it")
    options = TraceOptions(args.state_every, args.vis_every, parse_window(args.window))
    batch = load_batch(args.roots, args.binary, tag=args.tag, options=options, refresh=args.refresh, jobs=args.jobs)
    report.add_batch(batch)
    rows = []
    for e in sorted(batch.episodes, key=lambda e: e.episode_id):
        row = e.tables["episodes"].iloc[0]
        print(f"{e.episode_id}  rules {row.rules}  ticks {row.ticks}  winner {row.winner}  "
              f"glory {row.glory_0}/{row.glory_1}  results_check {row.results_check}  "
              f"{'cached' if e.cache_hit else 'traced'}{'  notes ' + row.notes if row.notes else ''}")
        rows.append({"episode_id": e.episode_id, "rules": row.rules, "ticks": row.ticks, "winner": row.winner,
                     "glory": [row.glory_0, row.glory_1], "results_check": row.results_check,
                     "cached": e.cache_hit, "notes": row.notes or None, "cache": str(e.source.cache)})
    print(f"loaded {len(batch.episodes)} episodes, {len(batch.failures)} failed")
    for path, code, message in batch.failures:
        print(f"FAILED [{code}] {path}: {message}")
    if batch.exclusions:
        print("exclusions:", dict(batch.exclusions))
    result = {"episodes": rows}
    if args.sql:
        import duckdb
        try:
            frame = open_duckdb(batch).execute(args.sql).df()
        except duckdb.Error as error:   # a bad query is the caller's usage error, not a tool crash
            raise pw_cli.UsageError(f"--sql failed: {error}", list(TABLES)) from error
        print(frame.to_string())
        result["sql"] = pw_cli.records(frame)
    if batch.episodes:
        roots = " ".join(str(r) for r in args.roots)
        report.suggest(f"uv run python paintbot_pw_lab/tools/pw.py metrics {roots} --json")
    return result


def main(argv: list[str] | None = None) -> int:
    return pw_cli.run(build_parser(), run_cli, argv)


if __name__ == "__main__":
    sys.exit(main())
