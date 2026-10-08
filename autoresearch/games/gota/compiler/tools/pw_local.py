#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_local.py
"""Local paintbot-pw batch harness: BASIC policy A vs policy B on the native library.

Screening only. Local seeds are not the league's, the only opponents are the files we seat,
and nothing here is evidence about the field (see docs/tools/pw_local.md).

    uv run paintbot_pw_lab/tools/pw_local.py compile CANDIDATE.bas
    uv run paintbot_pw_lab/tools/pw_local.py match A.bas B.bas --seed 7 --a-side 0
    uv run paintbot_pw_lab/tools/pw_local.py screen A.bas B.bas --seeds 1-14 --out DIR
    uv run paintbot_pw_lab/tools/pw_local.py screen A.bas B.bas --seeds 1-4 --record DIR --record-seeds 1-4

--record DIR writes paintbot-headless replays (+ .meta.json sidecars) for pw_episodes: `match`
records its own seed; `screen` needs --record-seeds naming which seeds (both sides each).

The library is tools/bin/<tag>/libpw.dylib from tools/build_native.sh. Every run checks its
libpw.build.json (tag, commit, sha256) against the requested tag; `match` and `screen` also run
two matches through paintbot-headless of the same tag first and refuse to continue unless the
final state hashes are identical (one with A on the even seats, one with A on the odd seats,
which also proves the seat-to-team mapping).

Teams: seat s plays for team s % 2 (sim.nim `team`). "a_side" is the team A plays: 0 = A on
the even seats, 1 = A on the odd seats. Every seed in a screen is played with both a_side values.
When every seed's two final hashes are equal, A and B played move for move identically: the
screen reports identical_play = true and warns (its W/D/L then says nothing about A vs B).
Outcome for A per match is the Elo outcome score, clamp(0.5 + (A glory - B glory)/2000, 0, 1)
(docs/mechanics.md §1.3). pw_results glory is already settled: the loser and both sides of a
draw hold 0.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import multiprocessing
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli  # noqa: E402
import pw_release  # noqa: E402
import pw_terrain  # noqa: E402

LAB = Path(__file__).resolve().parents[1]
DEFAULT_TAG = pw_release.current_tag()  # tools/release.env, shared with build_native.sh
LEAGUE_GLORY = {"behind_lives": 5, "behind_cogs": 10}
MAX_TICKS = 14400
SEATS = 16
SEAT_STAT_FIELDS = ("damage_dealt_enemy", "damage_dealt_team", "hits_enemy", "hits_taken",
                    "kills", "deaths", "captures", "first_friendly_fire_tick")
SCRIPT_STATUS = {0: "unscripted", 1: "running", 2: "compile_failed", 3: "disabled"}


# ---------------------------------------------------------------------------------------------
# Pure helpers (tested in tools/tests/test_pw_local.py)

def parse_seeds(text: str) -> list[int]:
    """'1-5,9,12-13' -> [1, 2, 3, 4, 5, 9, 12, 13]. Order kept; duplicates rejected."""
    seeds: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(-?\d+)-(-?\d+)", part)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if high < low:
                raise ValueError(f"empty seed range {part!r}")
            seeds.extend(range(low, high + 1))
        else:
            seeds.append(int(part))
    if not seeds:
        raise ValueError("no seeds given")
    if len(set(seeds)) != len(seeds):
        raise ValueError("duplicate seeds in the list")
    for seed in seeds:
        if not -2**31 <= seed < 2**31:
            raise ValueError(f"seed {seed} is not an int32")
    return seeds


def seat_policies(a_side: int) -> list[str]:
    """Which policy ('A'/'B') sits in each of the 16 seats when A plays team a_side."""
    return ["A" if seat % 2 == a_side else "B" for seat in range(SEATS)]


def elo_outcome(own_glory: float, other_glory: float) -> float:
    """The ladder's per-episode outcome with margin_scale 1000 (docs/mechanics.md §1.3)."""
    return min(1.0, max(0.0, 0.5 + (own_glory - other_glory) / 2000.0))


def match_record(seed: int, a_side: int, results: list[float]) -> dict:
    """Turn pw_results [tick, winner, glory0, glory1, meter0, meter1, hearts0, hearts1] into
    one JSONL row from A's point of view. winner is a team index; anything else is a draw."""
    tick, winner, glory0, glory1, meter0, meter1, hearts0, hearts1 = results
    winner = int(winner)
    glory = [glory0, glory1]
    a_glory, b_glory = glory[a_side], glory[1 - a_side]
    if winner in (0, 1):
        result_a = "win" if winner == a_side else "loss"
        winner_policy = "A" if winner == a_side else "B"
    else:
        result_a, winner_policy, winner = "draw", None, None
    return {
        "seed": seed, "a_side": a_side, "a_seats": "even" if a_side == 0 else "odd",
        "tick": int(tick), "winner": winner, "winner_policy": winner_policy, "result_a": result_a,
        "glory0": glory0, "glory1": glory1, "meter0": meter0, "meter1": meter1,
        "hearts0": int(hearts0), "hearts1": int(hearts1),
        "a_glory": a_glory, "b_glory": b_glory, "a_outcome": elo_outcome(a_glory, b_glory),
    }


def summarize(rows: list[dict]) -> dict:
    """Win/draw/loss and mean Elo outcome for A, overall and per A side, with a normal-approx
    95% CI (mean +- 1.96 * sd / sqrt(n); rough below ~20 matches). 'seed_balanced' averages
    each seed's two sides first, the estimate that cancels a side advantage."""
    def block(subset: list[dict]) -> dict:
        n = len(subset)
        out = {"n": n,
               "wins": sum(r["result_a"] == "win" for r in subset),
               "draws": sum(r["result_a"] == "draw" for r in subset),
               "losses": sum(r["result_a"] == "loss" for r in subset)}
        out["win_rate"] = out["wins"] / n if n else None
        out.update(mean_ci([r["a_outcome"] for r in subset]))
        return out

    summary = {"all": block(rows),
               "a_side_0": block([r for r in rows if r["a_side"] == 0]),
               "a_side_1": block([r for r in rows if r["a_side"] == 1])}
    by_seed: dict[int, list[float]] = {}
    for r in rows:
        by_seed.setdefault(r["seed"], []).append(r["a_outcome"])
    paired = [sum(v) / 2 for v in by_seed.values() if len(v) == 2]
    summary["seed_balanced"] = {"n_seeds": len(paired), **mean_ci(paired)}
    return summary


def identical_play(rows: list[dict]) -> bool:
    """True when every seed was played with both a_side values and each seed's two final state
    hashes are equal: A-vs-B equals B-vs-A game for game, so the two files played move for
    move identically (e.g. a policy whose differences sit behind an oracle that is off locally)."""
    by_seed: dict[int, dict[int, int]] = {}
    for row in rows:
        by_seed.setdefault(row["seed"], {})[row["a_side"]] = row["final_hash"]
    return bool(by_seed) and all(len(h) == 2 and h[0] == h[1] for h in by_seed.values())


IDENTICAL_PLAY_WARNING = ("the two files play move-for-move identically here (every seed's final hash is the same "
                          "with A on either side); e.g. jev.bas without an oracle equals base.bas. This screen "
                          "says nothing about the difference between them")


def mean_ci(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"mean_outcome": None, "ci95_low": None, "ci95_high": None}
    array = np.asarray(values, dtype=float)
    mean = float(array.mean())
    if n < 2:
        return {"mean_outcome": mean, "ci95_low": None, "ci95_high": None}
    half = 1.96 * float(array.std(ddof=1)) / math.sqrt(n)
    return {"mean_outcome": mean, "ci95_low": mean - half, "ci95_high": mean + half}


# ---------------------------------------------------------------------------------------------
# Build identity

def git_commit(tree: Path, ref: str) -> str:
    return subprocess.run(["git", "-C", str(tree), "rev-parse", f"{ref}^{{commit}}"],
                          check=True, capture_output=True, text=True).stdout.strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_build(tag: str) -> dict:
    """Refuse unless tools/bin/<tag>/ holds a library and headless binary built from <tag>."""
    out = LAB / "tools" / "bin" / tag
    lib, headless, meta_path = out / "libpw.dylib", out / "paintbot-headless", out / "libpw.build.json"
    fix = pw_release.build_command("libpw.dylib", tag)
    for path in (lib, headless, meta_path):
        if not path.exists():
            raise pw_cli.EnvironmentMissing(f"missing {path}", fix)
    meta = json.loads(meta_path.read_text())
    tree = pw_release.release_tree(tag)
    if not tree.is_dir():   # e.g. PW_CACHE_DIR now points somewhere the build did not use
        raise pw_cli.EnvironmentMissing(f"missing release worktree {tree} (PW_CACHE_DIR or tools/.cache)", fix)
    expected_commit = git_commit(tree, tag)
    problems = []
    if meta.get("tag") != tag:
        problems.append(f"library built for tag {meta.get('tag')!r}, requested {tag!r}")
    if meta.get("commit") != expected_commit:
        problems.append(f"library commit {meta.get('commit')} != {tag} commit {expected_commit}")
    if meta.get("sha256") != sha256_file(lib):
        problems.append("libpw.dylib changed since build_native.sh wrote libpw.build.json")
    if problems:
        raise pw_cli.EnvironmentMissing("refusing to run: " + "; ".join(problems), fix)
    return {"tag": tag, "commit": meta["commit"], "nim": meta.get("nim"),
            "lib": str(lib), "headless": str(headless)}


# ---------------------------------------------------------------------------------------------
# One native-library handle per worker process. Rules, config and map are per handle in this
# ABI, but the interpreter and world still use module globals, so each process runs exactly one
# match at a time on its own handle.

_lib = None
_handle = None


def _load_library(lib_path: str, glory: dict, terrain_dir: str | None = None) -> int:
    global _lib, _handle
    lib = ctypes.CDLL(lib_path)
    lib.pw_create.restype = ctypes.c_void_p
    lib.pw_create.argtypes = [ctypes.c_int32, ctypes.c_int32]
    for name in ("pw_reset", "pw_set_rules", "pw_set_seat_script", "pw_step", "pw_results",
                 "pw_set_config_json", "pw_seat_script_status", "pw_seat_stats", "pw_rules"):
        getattr(lib, name).restype = ctypes.c_int
        getattr(lib, name).argtypes = None
    lib.pw_reset.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32]
    lib.pw_set_rules.argtypes = [ctypes.c_void_p, ctypes.c_int32]
    lib.pw_rules.argtypes = [ctypes.c_void_p]
    lib.pw_set_seat_script.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
    lib.pw_seat_script_status.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
    lib.pw_set_config_json.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                       ctypes.c_char_p, ctypes.c_int32]
    lib.pw_step.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lib.pw_results.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.pw_seat_stats.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.pw_rules_latest.restype = ctypes.c_int
    lib.pw_state_hash.restype = ctypes.c_uint32
    lib.pw_state_hash.argtypes = [ctypes.c_void_p]

    handle = ctypes.c_void_p(lib.pw_create(1, MAX_TICKS))
    rules = lib.pw_rules_latest()
    if lib.pw_set_rules(handle, rules) != 0:
        raise RuntimeError(f"pw_set_rules({rules}) failed")
    config = json.dumps({"glory": glory}).encode()
    error = ctypes.create_string_buffer(512)
    if lib.pw_set_config_json(handle, config, len(config), error, len(error)) != 0:
        raise RuntimeError(f"pw_set_config_json refused {config!r}: {error.value.decode()}")
    # The shared terrain file (pw_terrain.py): without it every worker computes most of the
    # island's terrain blocks itself (~20 s) on its first match.
    pw_terrain.attach_native(lib, handle, rules, terrain_dir and Path(terrain_dir))
    _lib, _handle = lib, handle
    return rules


def _init_worker(lib_path: str, glory: dict, terrain_dir: str | None) -> None:
    _load_library(lib_path, glory, terrain_dir)


def _install_scripts(sources: list[bytes]) -> list[int]:
    return [_lib.pw_set_seat_script(_handle, seat, src, len(src)) for seat, src in enumerate(sources)]


def _seat_statuses() -> list[tuple[int, str]]:
    message = ctypes.create_string_buffer(1024)
    statuses = []
    for seat in range(SEATS):
        status = _lib.pw_seat_script_status(_handle, seat, message, len(message))
        statuses.append((status, message.value.decode(errors="replace")))
    return statuses


def _play(seed: int, max_ticks: int) -> tuple[list[float], int]:
    """Reset (which also recompiles the installed scripts) and step until the match ends."""
    actions = (ctypes.c_int32 * (SEATS * 5))()
    rewards = (ctypes.c_float * SEATS)()
    terminals = (ctypes.c_float * SEATS)()
    if _lib.pw_reset(_handle, seed, max_ticks) != 0:
        raise RuntimeError(f"pw_reset(seed={seed}) failed")
    while _lib.pw_step(_handle, actions, rewards, terminals) == 0:
        pass
    results = (ctypes.c_float * 8)()
    _lib.pw_results(_handle, results)
    return [float(x) for x in results], int(_lib.pw_state_hash(_handle))


def run_match(job: dict) -> dict:
    """Worker: one full match. job = {seed, a_side, a_src, b_src, max_ticks}."""
    started = time.time()
    policies = seat_policies(job["a_side"])
    sources = [job["a_src"] if p == "A" else job["b_src"] for p in policies]
    compile_codes = _install_scripts(sources)
    results, final_hash = _play(job["seed"], job["max_ticks"])
    row = match_record(job["seed"], job["a_side"], results)
    row["final_hash"] = final_hash
    row["rules"] = _lib.pw_rules(_handle)
    # A seat whose script failed to compile idles (as hosted); one disabled at runtime issues
    # empty commands from then on. Either makes the match a poor test of that policy, so every
    # such seat is listed rather than hidden.
    row["bad_seats"] = [
        {"seat": seat, "policy": policies[seat], "status": SCRIPT_STATUS.get(status, status),
         "message": message}
        for seat, (status, message) in enumerate(_seat_statuses())
        if status != 1 or compile_codes[seat] != 0]
    stats = (ctypes.c_int32 * (SEATS * 8))()
    _lib.pw_seat_stats(_handle, stats)
    row["seat_stats"] = [
        {"seat": seat, "team": seat % 2, "policy": policies[seat],
         **{field: stats[seat * 8 + i] for i, field in enumerate(SEAT_STAT_FIELDS)}}
        for seat in range(SEATS)]
    row["seconds"] = round(time.time() - started, 2)
    return row


def compile_check(src: bytes, seed: int, ticks: int) -> list[dict]:
    """The same script in all 16 seats for `ticks` ticks; per-seat status and error text."""
    codes = _install_scripts([src] * SEATS)
    _play(seed, ticks)
    return [{"seat": seat, "team": seat % 2, "set_script": codes[seat],
             "status": SCRIPT_STATUS.get(status, status), "message": message}
            for seat, (status, message) in enumerate(_seat_statuses())]


# ---------------------------------------------------------------------------------------------
# paintbot-headless: parity reference and replay recording

def headless_command(build: dict, a_path: Path, b_path: Path, seed: int, a_side: int,
                     max_ticks: int, glory: dict, record: Path | None = None) -> list[str]:
    command = [build["headless"]]
    for policy in seat_policies(a_side):
        command += ["--bot", f"{a_path if policy == 'A' else b_path}:1"]
    command += ["--seed", str(seed), "--ticks", str(max_ticks),
                "--glory:" + json.dumps(glory, separators=(",", ":"))]
    if record is not None:
        command += ["--record", str(record)]
    return command


def run_headless(command: list[str]) -> dict:
    done = subprocess.run(command, capture_output=True, text=True)
    found = re.search(r"ticks=(\d+) captures=.* hash=(\d+)", done.stdout)
    if done.returncode != 0 or not found:
        raise RuntimeError(f"paintbot-headless failed ({done.returncode}): "
                           f"{done.stdout[-500:]} {done.stderr[-500:]}")
    return {"ticks": int(found.group(1)), "hash": int(found.group(2))}


def parity_guard(build, pool, a_path, b_path, a_src, b_src, seeds, max_ticks, glory) -> list[dict]:
    """Two matches (A even on the first seed, A odd on the second) through both engines."""
    picks = [(seeds[0], 0), (seeds[1] if len(seeds) > 1 else seeds[0], 1)]
    jobs = [{"seed": s, "a_side": side, "a_src": a_src, "b_src": b_src, "max_ticks": max_ticks}
            for s, side in picks]
    native = pool.map_async(run_match, jobs)
    with ThreadPoolExecutor(len(picks)) as threads:
        headless = list(threads.map(run_headless, [
            headless_command(build, a_path, b_path, s, side, max_ticks, glory) for s, side in picks]))
    checks = []
    for row, ref in zip(native.get(), headless):
        checks.append({"seed": row["seed"], "a_side": row["a_side"], "library_hash": row["final_hash"],
                       "headless_hash": ref["hash"], "library_tick": row["tick"],
                       "headless_ticks": ref["ticks"], "match": row["final_hash"] == ref["hash"]})
    for check in checks:
        print(f"parity seed {check['seed']} a_side {check['a_side']}: library {check['library_hash']} "
              f"headless {check['headless_hash']} -> {'ok' if check['match'] else 'MISMATCH'}",
              file=sys.stderr)
    if not all(c["match"] for c in checks):
        raise pw_cli.EnvironmentMissing("refusing to continue: native library and paintbot-headless disagree on "
                                        "final hashes (a stale or mixed build)",
                                        pw_release.build_command("libpw.dylib", build["tag"]))
    return checks


def record_matches(build, rows, a_path, b_path, seeds, max_ticks, glory, out_dir: Path, workers) -> list[dict]:
    """Re-run the chosen seeds (both sides) through paintbot-headless --record for pw_trace,
    and check each recording's final hash against the library's row."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = [r for r in rows if r["seed"] in set(seeds)]
    missing = set(seeds) - {r["seed"] for r in wanted}
    if missing:
        raise pw_cli.UsageError(f"--record-seeds {sorted(missing)} not in this batch", sorted({r["seed"] for r in rows}))
    # The stem is the local episode id in pw_episodes, so it names both files: arms recorded
    # against the same opponent on the same seeds must not collide in one batch.
    pair = f"{a_path.stem}_vs_{b_path.stem}"
    paths = [out_dir / f"{pair}_s{r['seed']}_a{r['a_seats']}.replay" for r in wanted]
    commands = [headless_command(build, a_path, b_path, r["seed"], r["a_side"], max_ticks, glory, p)
                for r, p in zip(wanted, paths)]
    with ThreadPoolExecutor(workers) as threads:
        refs = list(threads.map(run_headless, commands))
    recorded = []
    for row, path, ref in zip(wanted, paths, refs):
        # Sidecar so pw_episodes knows which file sat on each seat (tables.md "Local sidecar");
        # without it every seat is an anonymous tape name and --policy filters match nothing.
        names = {"A": a_path.name, "B": b_path.name}
        if names["A"] == names["B"]:
            names = {"A": f"A:{a_path.name}", "B": f"B:{b_path.name}"}
        meta = {"source": "pw_local", "seed": row["seed"], "glory_config": glory, "a_side": row["a_side"],
                "policies": {"A": policy_info(a_path), "B": policy_info(b_path)},
                "seats": [{"position": seat, "policy_name": names[p], "team": seat % 2}
                          for seat, p in enumerate(seat_policies(row["a_side"]))]}
        path.with_name(path.stem + ".meta.json").write_text(json.dumps(meta, indent=1))
        recorded.append({"seed": row["seed"], "a_side": row["a_side"], "replay": str(path),
                         "hash_matches_library": ref["hash"] == row["final_hash"]})
        print(f"recorded {path} (hash {'matches' if ref['hash'] == row['final_hash'] else 'DIFFERS FROM'} library)",
              file=sys.stderr)
    return recorded


# ---------------------------------------------------------------------------------------------
# Commands

def policy_info(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def batch(args, seeds: list[int], sides: list[int]) -> dict:
    build = check_build(args.tag)
    glory = json.loads(args.glory)
    a_path, b_path = Path(args.a), Path(args.b)
    a_src, b_src = a_path.read_bytes(), b_path.read_bytes()
    jobs = [{"seed": s, "a_side": side, "a_src": a_src, "b_src": b_src, "max_ticks": args.max_ticks}
            for s in seeds for side in sides]
    workers = max(1, min(args.workers, len(jobs)))
    context = multiprocessing.get_context("spawn")
    with pw_terrain.session(args.tag) as terrain_dir, \
            context.Pool(max(workers, 2), initializer=_init_worker,
                         initargs=(build["lib"], glory, terrain_dir and str(terrain_dir))) as pool:
        parity = parity_guard(build, pool, a_path, b_path, a_src, b_src, seeds, args.max_ticks, glory)
        started = time.time()
        rows = []
        out_file = None
        if args.out:
            Path(args.out).mkdir(parents=True, exist_ok=True)
            out_file = open(Path(args.out) / "matches.jsonl", "w")
        for row in pool.imap_unordered(run_match, jobs):
            rows.append(row)
            if out_file:
                out_file.write(json.dumps(row) + "\n")
                out_file.flush()
            print(f"seed {row['seed']:>6} A {row['a_seats']:<4} {row['result_a']:<4} tick {row['tick']:>5} "
                  f"glory A {row['a_glory']:>6.1f} B {row['b_glory']:>6.1f} outcome {row['a_outcome']:.3f}"
                  + (f" bad_seats {len(row['bad_seats'])}" if row["bad_seats"] else ""), file=sys.stderr)
        wall = time.time() - started
        if out_file:
            out_file.close()
    rows.sort(key=lambda r: (seeds.index(r["seed"]), r["a_side"]))
    recorded = None
    if args.record:
        recorded = record_matches(build, rows, a_path, b_path, parse_seeds(args.record_seeds or ""),
                                  args.max_ticks, glory, Path(args.record), workers)
    summary = {
        "screening_only": "local matches vs files we seat; not evidence about the league field",
        "build": {k: build[k] for k in ("tag", "commit", "nim")},
        "rules": sorted({r["rules"] for r in rows}),
        "glory": glory, "max_ticks": args.max_ticks, "map": "Heartwick (default)",
        "a": policy_info(a_path), "b": policy_info(b_path),
        "seeds": seeds, "sides": sides, "parity": parity,
        "matches_with_bad_seats": sum(bool(r["bad_seats"]) for r in rows),
        "wall_seconds": round(wall, 1), "workers": workers,
        "matches_per_second": round(len(rows) / wall, 3) if wall > 0 else None,
        "summary": summarize(rows), "recorded": recorded,
        "identical_play": identical_play(rows),
    }
    if args.out:
        (Path(args.out) / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def print_summary(summary: dict) -> None:
    print(f"build {summary['build']['tag']} ({summary['build']['commit'][:8]}), glory {summary['glory']}, "
          f"{summary['workers']} workers, {summary['wall_seconds']} s, {summary['matches_per_second']} matches/s")
    print("SCREENING ONLY: local results vs seated files, never field evidence.")
    for key in ("all", "a_side_0", "a_side_1"):
        block = summary["summary"][key]
        if not block["n"]:
            continue
        ci = (f" [{block['ci95_low']:.3f}, {block['ci95_high']:.3f}]"
              if block["ci95_low"] is not None else "")
        print(f"  {key:<9} n={block['n']:<4} W/D/L {block['wins']}/{block['draws']}/{block['losses']}"
              f"  A outcome {block['mean_outcome']:.3f}{ci}")
    balanced = summary["summary"]["seed_balanced"]
    if balanced["n_seeds"]:
        ci = (f" [{balanced['ci95_low']:.3f}, {balanced['ci95_high']:.3f}]"
              if balanced["ci95_low"] is not None else "")
        print(f"  seed-balanced over {balanced['n_seeds']} seeds: A outcome {balanced['mean_outcome']:.3f}{ci}")
    if summary["matches_with_bad_seats"]:
        print(f"  WARNING: {summary['matches_with_bad_seats']} matches had a seat that failed to "
              f"compile or was disabled at runtime (see bad_seats in matches.jsonl)")


def policy_path(text: str, flag: str) -> Path:
    path = Path(text)
    if not path.is_file():
        raise pw_cli.UsageError(f"{flag} {text}: no such .bas file")
    return path


def parse_seed_list(text: str, flag: str) -> list[int]:
    try:
        return parse_seeds(text)
    except ValueError as error:
        raise pw_cli.UsageError(f"{flag} {text!r}: {error}; e.g. 1-14 or 7,8,9") from error


def parse_glory(text: str) -> dict:
    try:
        glory = json.loads(text)
    except json.JSONDecodeError as error:
        raise pw_cli.UsageError(f"--glory is not JSON ({error}); e.g. {json.dumps(LEAGUE_GLORY)}") from error
    if not isinstance(glory, dict):
        raise pw_cli.UsageError(f"--glory must be a JSON object, e.g. {json.dumps(LEAGUE_GLORY)}")
    return glory


def report_batch(report, summary: dict, out: str | None, record: str | None) -> dict:
    """Envelope fields for a match/screen summary: outputs, counts, and one failure per bad match."""
    report.counts["processed"] = summary["summary"]["all"]["n"]
    if out:
        report.output(Path(out) / "matches.jsonl")
        report.output(Path(out) / "summary.json")
    for item in summary.get("recorded") or []:
        report.output(item["replay"])
        if not item["hash_matches_library"]:
            report.fail(item["replay"], "record_hash_mismatch", "recording's final hash differs from the library's")
    if summary["matches_with_bad_seats"]:
        report.fail("bad_seats", "bad_seats", f"{summary['matches_with_bad_seats']} matches had a seat that failed "
                                              "to compile or was disabled at runtime (bad_seats in matches.jsonl)")
    if record:
        report.suggest(f"uv run python paintbot_pw_lab/tools/pw.py episodes {record} --json")
    return summary


def cmd_compile(args, report) -> dict:
    policy = policy_path(args.policy, "policy")
    build = check_build(args.tag)
    with pw_terrain.session(args.tag) as terrain_dir:
        rules = _load_library(build["lib"], parse_glory(args.glory), terrain_dir and str(terrain_dir))
    seats = compile_check(policy.read_bytes(), args.seed, args.ticks)
    bad = [s for s in seats if s["status"] != "running" or s["set_script"] != 0]
    print(f"{args.policy}: {'OK' if not bad else 'FAILED'} ({args.ticks} ticks, seed {args.seed}, "
          f"rules {rules}, {build['tag']})")
    for seat in bad:
        print(f"  seat {seat['seat']:>2} team {seat['team']}: {seat['status']}: {seat['message']}")
        report.fail(f"seat {seat['seat']}", seat["status"], seat["message"] or f"set_script {seat['set_script']}")
    report.counts["processed"] = len(seats)
    return {"build": build["tag"], "rules": rules, "policy": policy_info(policy),
            "seed": args.seed, "ticks": args.ticks, "ok": not bad, "seats": seats}


def cmd_match(args, report) -> dict:
    policy_path(args.a, "a"), policy_path(args.b, "b")
    parse_glory(args.glory)
    summary = batch(args, [args.seed], [args.a_side])
    print_summary(summary)
    return report_batch(report, summary, args.out, args.record)


def cmd_screen(args, report) -> dict:
    if args.record and not args.record_seeds:
        raise pw_cli.UsageError("--record needs --record-seeds (which seeds to re-run with a replay)")
    policy_path(args.a, "a"), policy_path(args.b, "b")
    parse_glory(args.glory)
    summary = batch(args, parse_seed_list(args.seeds, "--seeds"), [0, 1])
    print_summary(summary)
    if summary["identical_play"]:
        print(f"WARNING: {IDENTICAL_PLAY_WARNING}", file=sys.stderr)
        report.suggest(f"WARNING: {IDENTICAL_PLAY_WARNING}")
    return report_batch(report, summary, args.out, args.record)


def main(argv: list[str] | None = None) -> int:
    parser = pw_cli.ArgumentParser("pw_local", __doc__, examples=[
        "uv run python paintbot_pw_lab/tools/pw_local.py compile CANDIDATE.bas --json",
        "uv run python paintbot_pw_lab/tools/pw_local.py match A.bas B.bas --seed 7 --a-side 0 --json",
        "uv run python paintbot_pw_lab/tools/pw_local.py screen A.bas paintbot_pw_lab/reference/base.bas "
        "--seeds 1-28 --out /tmp/screen --json",
        "uv run python paintbot_pw_lab/tools/pw_local.py match A.bas B.bas --seed 9 --record /tmp/rec",
        "uv run python paintbot_pw_lab/tools/pw_local.py screen A.bas B.bas --seeds 1-4 --record /tmp/rec "
        "--record-seeds 1-4 --json"])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tag", default=DEFAULT_TAG, help="release tag of the engine build (default %(default)s)")
    common.add_argument("--glory", default=json.dumps(LEAGUE_GLORY),
                        help="glory award JSON (default: the league's %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("compile", parents=[common], help="load one .bas into all 16 seats briefly")
    p.add_argument("policy", help="the .bas file, e.g. CANDIDATE.bas")
    p.add_argument("--seed", type=int, default=7, help="engine seed (default %(default)s)")
    p.add_argument("--ticks", type=int, default=720, help="ticks to run (default 720 = 30 s)")
    p.set_defaults(func=cmd_compile)

    batch_args = argparse.ArgumentParser(add_help=False, parents=[common])
    batch_args.add_argument("a", help="policy A .bas")
    batch_args.add_argument("b", help="policy B .bas")
    batch_args.add_argument("--max-ticks", type=int, default=MAX_TICKS, help="tick cap (default %(default)s)")
    batch_args.add_argument("--workers", type=int, default=os.cpu_count() or 1, help="processes (default: all cores)")
    batch_args.add_argument("--out", help="directory for matches.jsonl and summary.json (overwritten on re-run)")
    batch_args.add_argument("--record", help="directory for paintbot-headless replays of --record-seeds (match: defaults to --seed; screen: --record-seeds required)")
    batch_args.add_argument("--record-seeds", help="seeds to record, e.g. 7,8 (both sides each)")

    p = sub.add_parser("match", parents=[batch_args], help="one match")
    p.add_argument("--seed", type=int, required=True, help="engine seed, e.g. --seed 7")
    p.add_argument("--a-side", type=int, choices=(0, 1), default=0, help="team A plays (0 = even seats)")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("screen", parents=[batch_args], help="A vs B over seeds, both sides each")
    p.add_argument("--seeds", required=True, help="e.g. 1-14 or 7,8,9 or 1-10,20")
    p.set_defaults(func=cmd_screen)

    def run_cli(args, report):
        if args.command == "match" and args.record and not args.record_seeds:
            args.record_seeds = str(args.seed)
        return args.func(args, report)

    return pw_cli.run(parser, run_cli, argv)


if __name__ == "__main__":
    sys.exit(main())
