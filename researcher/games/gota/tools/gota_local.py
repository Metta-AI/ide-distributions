#!/usr/bin/env python3
# Source: personal_labs_gota@09d3ed28:gods_of_the_arena_lab/tools/gota_local.py
"""Pinned GotA host, resource statistics, local matches, screens and recordings.

Local screens diagnose execution and parity; they do not measure field strength.
Build first: games/gota/tools/build_gota_match.sh
"""
from __future__ import annotations

import concurrent.futures
import fcntl
import hashlib
import json
import math
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

import gota_cli

TOOLS = Path(__file__).resolve().parent
DEFAULT_TICKS = 28_800
BUILD_COMMAND = "games/gota/tools/build_gota_match.sh"


def binary_path(commit: str | None = None) -> Path:
    """The cached match executable for `commit` (default: the engine.env pin), unvalidated."""
    commit = commit or gota_cli.engine()['POLYWORLD_COMMIT']
    return TOOLS / 'bin' / f'gota_match-{commit[:8]}'


def binary_identity(commit: str | None = None) -> dict:
    """Validate the full native input/output receipt before accepting this cache.

    `commit` defaults to the engine.env pin; callers holding a resolved engine pass it.
    """
    import native_build
    commit = commit or gota_cli.engine()['POLYWORLD_COMMIT']
    return native_build.validate(binary_path(commit), 'gota_match', commit)


def binary(commit: str | None = None) -> Path:
    binary_identity(commit)
    return binary_path(commit)


class EngineError(RuntimeError):
    """The pinned engine refused an input or could not verify its replay."""


def _invoke(*arguments: str, commit: str | None = None, timeout: float | None = None,
            env: dict | None = None, executable: Path | None = None) -> dict:
    """Run the match binary; `executable` skips re-validating a receipt the caller checked.

    The binary runs in its own process group so a timeout kills everything it started.
    """
    command = [str(executable or binary(commit)), *map(str, arguments)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=env, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise EngineError(f"gota_match timed out after {timeout}s") from None
    if stderr:
        print(stderr, end="", file=sys.stderr)
    if process.returncode:
        raise EngineError(stderr.strip() or f"Engine exited {process.returncode}")
    return json.loads(stdout)


def host() -> dict:
    """Return the real pinned host registry and compiled structured declarations."""
    return _invoke("host")


def stats(path: str | Path) -> dict:
    """Measure a policy even when its strict structural compile fails."""
    return _invoke("stats", str(Path(path).resolve()))


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(seed: int, ticks: int, draft: str) -> None:
    if not -(2**31) <= seed < 2**31:
        raise gota_cli.UsageError("seed must fit int32")
    if not 0 < ticks <= 2**31 - 2401:
        raise gota_cli.UsageError("ticks must be positive and leave room for draft ticks")
    if draft not in ("open", "team", "unique"):
        raise gota_cli.UsageError("unknown draft mode", ("open", "team", "unique"))


def match(seats: list[str | Path], *, seed: int = 1, ticks: int = DEFAULT_TICKS,
          draft: str = "open", out: str | Path, tables: bool = False,
          replay: bool = False, roster: list[int] | None = None,
          commit: str | None = None, timeout: float | None = None,
          env: dict | None = None, executable: Path | None = None) -> dict:
    """Run ten seats; failed policies remain data in heroes[].vm_failed.

    `commit` selects the engine's match binary; it defaults to the engine.env pin.
    `executable` is that binary when the caller already validated its receipt.
    `timeout` (seconds) and `env` apply to the binary's process.
    """
    _validate(seed, ticks, draft)
    if len(seats) != 10:
        raise gota_cli.UsageError("match requires exactly ten --seat paths")
    if roster is not None:
        if len(roster) != 10 or any(value not in range(10) for value in roster):
            raise gota_cli.UsageError("roster requires ten class IDs from 0 through 9")
        if draft == "unique" and len(set(roster)) != 10:
            raise gota_cli.UsageError("unique draft requires ten distinct classes")
        if draft == "team" and any(len(set(roster[start:start + 5])) != 5 for start in (0, 5)):
            raise gota_cli.UsageError("team draft requires distinct classes on each team")
    commit = commit or gota_cli.engine()["POLYWORLD_COMMIT"]
    executable = executable or binary(commit)
    paths = [Path(path).resolve() for path in seats]
    output = Path(out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    job = {"seats": list(map(str, paths)), "seed": seed, "ticks": ticks,
           "draft": draft, "out": str(output), "tables": tables, "replay": replay}
    if roster is not None:
        job["roster"] = list(roster)
    _write_json(output / "job.json", job)
    # Missing/unreadable policy files are seat failures, just like compile errors.
    meta = {"seed": seed, "ticks": ticks, "draft": draft,
            "engine": commit,
            "seats": [{"path": str(path), "sha256": _sha256(path) if path.is_file() else None}
                      for path in paths]}
    if roster is not None:
        meta["roster"] = list(roster)
    _write_json(output / "meta.json", meta)
    result = _invoke("match", str(output / "job.json"), commit=commit, timeout=timeout,
                     env=env, executable=executable)
    for seat, hero in zip(meta['seats'], result['heroes']):
        seat.update({field: hero[field] for field in ('vm_failed', 'vm_error', 'vm_routine', 'vm_pc')})
    _write_json(output / 'meta.json', meta)
    return result


def episode_contract(episode: dict) -> None:
    """Reject missing or different hosted release identity; names are not identity."""
    pin = gota_cli.engine()
    if episode.get("coworld_id") != pin["COWORLD_ID"] or episode.get("coworld_version") != pin["COWORLD_VERSION"]:
        raise ValueError(f"episode engine differs from pin {pin['COWORLD_VERSION']} / {pin['POLYWORLD_COMMIT']}")
    commit = episode.get("polyworld_commit") or episode.get("engine_commit")
    if commit is not None and commit != pin["POLYWORLD_COMMIT"]:
        raise ValueError("episode commit differs from pinned polyworld commit")


def replay(path: str | Path, *, out: str | Path, episode: str | Path | None = None,
           compact: bool = False) -> dict:
    """Re-simulate every recorded tick, refusing other versions and any hash mismatch.

    A bare tape has no git commit field; verified hashes establish compatibility,
    while provided episode/local metadata must also match the exact recording pin.
    """
    source, output = Path(path).resolve(), Path(out).resolve()
    binary()
    if not source.is_file():
        raise gota_cli.UsageError(f"replay does not exist: {source}")
    if source.parent == output:
        raise gota_cli.UsageError("replay --out must differ from the input recording directory")
    # A failed refresh must not leave an old success receipt consumable by audit.
    for name in ("result.json", "meta.json"):
        (output / name).unlink(missing_ok=True)
    episode_path = Path(episode).resolve() if episode else None
    if episode_path is None and (source.parent / "episode.json").is_file():
        episode_path = source.parent / "episode.json"
    metadata = {}
    if episode_path is not None:
        try:
            episode_contract(json.loads(episode_path.read_text()))
        except ValueError as error:
            raise EngineError(str(error)) from error
    elif (source.parent / "meta.json").is_file():
        metadata = json.loads((source.parent / "meta.json").read_text())
        if metadata.get("engine") != gota_cli.engine()["POLYWORLD_COMMIT"]:
            raise EngineError("recording commit differs from pinned polyworld commit")
    output.mkdir(parents=True, exist_ok=True)
    job = {"replay": str(source), "out": str(output), "fixed_roster": bool(metadata.get("roster"))}
    _write_json(output / "replay-job.json", job)
    result = _invoke("anatomy" if compact else "replay", str(output / "replay-job.json"))
    result["replay_sha256"] = _sha256(source)
    result["tables_sha256"] = None if compact else _sha256(output / "tables.jsonl")
    result["recording_identity"] = "episode" if episode_path else "local_meta" if metadata else "version_and_hashes_only"
    meta = {**metadata, "mode": "anatomy" if compact else "replay", "engine": gota_cli.engine()["POLYWORLD_COMMIT"],
            "replay": str(source), "replay_sha256": result["replay_sha256"],
            "tables_sha256": result["tables_sha256"], "verified": True}
    if episode_path:
        meta.update(episode=str(episode_path), episode_sha256=_sha256(episode_path))
    _write_json(output / "meta.json", meta)
    _write_json(output / "result.json", result)
    return result


def parse_seeds(value: str) -> list[int]:
    """Accept comma-separated integers and inclusive nonnegative ranges (1-4)."""
    values = []
    try:
        for part in value.split(","):
            if "-" in part[1:]:
                start, end = map(int, part.split("-", 1))
                if start > end:
                    raise ValueError("descending range")
                values.extend(range(start, end + 1))
            else:
                values.append(int(part))
    except ValueError as error:
        raise gota_cli.UsageError(f"invalid seeds {value!r}; use 1-4 or 1,3,7") from error
    if not values or len(values) != len(set(values)):
        raise gota_cli.UsageError("seeds must be nonempty and distinct")
    return values


def _seeds(value) -> list[int]:
    values = parse_seeds(value) if isinstance(value, str) else list(value)
    if not values or len(values) != len(set(values)):
        raise gota_cli.UsageError("seeds must be nonempty and distinct")
    return values


def _run_pair(task: dict) -> dict:
    candidate, opponent = Path(task["candidate"]), Path(task["opponent"])
    side = task["side"]
    seats = [candidate] * 5 + [opponent] * 5 if side == 0 else [opponent] * 5 + [candidate] * 5
    result = match(seats, seed=task["seed"], ticks=task["ticks"], draft=task["draft"],
                   out=task["out"], tables=task["tables"], replay=task["replay"])
    meta_path = Path(task["out"]) / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta.update(candidate=str(candidate), candidate_sha256=_sha256(candidate),
                opponent=str(opponent), opponent_sha256=_sha256(opponent), side=side,
                candidate_seats=list(range(side * 5, side * 5 + 5)))
    _write_json(meta_path, meta)
    return {"seed": task["seed"], "side": side, "out": str(task["out"]), **result}


def _parallel(tasks: list[dict], workers: int) -> list[dict]:
    if workers < 1:
        raise gota_cli.UsageError("workers must be positive")
    with concurrent.futures.ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
        return list(pool.map(_run_pair, tasks))


def _tasks(candidate, opponent, seeds, sides, ticks, draft, out, tables, replay):
    candidate, opponent = Path(candidate).resolve(), Path(opponent).resolve()
    for path in (candidate, opponent):
        if not path.is_file():
            raise gota_cli.UsageError(f"policy file does not exist: {path}")
    seeds = _seeds(seeds)
    sides = list(sides)
    if not sides or len(sides) != len(set(sides)) or any(side not in (0, 1) for side in sides):
        raise gota_cli.UsageError("sides must be 0, 1, or 0,1")
    for seed in seeds:
        _validate(seed, ticks, draft)
    binary()
    return [{"candidate": str(candidate), "opponent": str(opponent), "seed": seed,
             "side": side, "ticks": ticks, "draft": draft, "tables": tables, "replay": replay,
             "out": str(Path(out).resolve() / f"seed{seed}-side{side}")}
            for seed in seeds for side in sides]


def record(candidate: str | Path, opponent: str | Path, *, seeds=range(1, 5),
           sides=(0, 1), ticks: int = DEFAULT_TICKS, draft: str = "open",
           out: str | Path, workers: int = 4, tables: bool = True, replay: bool = False) -> dict:
    """Record both sides for telemetry/audit; tables are enabled by default."""
    started = time.monotonic()
    tasks = _tasks(candidate, opponent, seeds, sides, ticks, draft, out, tables, replay)
    matches = _parallel(tasks, workers)
    result = {"matches": matches, "matches_with_bad_seats": _bad_matches(matches),
              "seconds": time.monotonic() - started}
    _write_json(Path(out).resolve() / "record.json", result)
    return result


def _bad_matches(matches: list[dict]) -> int:
    return sum(any(hero["vm_failed"] for hero in result["heroes"]) for result in matches)


def _baseline(task: dict) -> dict:
    output = Path(task["out"])
    output.parent.mkdir(parents=True, exist_ok=True)
    # Two concurrent screens may request the same immutable baseline.
    with output.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cached = output / "baseline.json"
        if cached.is_file():
            return json.loads(cached.read_text())
        result = _run_pair(task)
        if not _bad_matches([result]):
            _write_json(cached, result)
        return result


def _interval(values: list[float]) -> dict:
    from scipy.stats import t

    mean = statistics.mean(values)
    n = len(values)
    half = float(t.ppf(0.975, n - 1)) * statistics.stdev(values) / math.sqrt(n) if n > 1 else None
    return {"mean_outcome": mean, "ci95_low": mean - half if half is not None else None,
            "ci95_high": mean + half if half is not None else None, "n": n}


def screen(candidate: str | Path, opponent: str | Path, *, seeds=range(1, 5),
           ticks: int = DEFAULT_TICKS, draft: str = "open", out: str | Path,
           workers: int = 4, cache: str | Path | None = None) -> dict:
    """Paired local execution screen, with a Student t interval over seed means."""
    started = time.monotonic()
    tasks = _tasks(candidate, opponent, seeds, (0, 1), ticks, draft, out, False, False)
    if workers < 1:
        raise gota_cli.UsageError("workers must be positive")
    baseline_root = Path(cache) if cache else TOOLS.parent / "tmp/gota-local/baselines"
    fingerprint = {"policy_sha256": _sha256(Path(opponent).resolve()),
                   "engine": gota_cli.engine(), "binary_sha256": _sha256(binary())}
    baseline_tasks = []
    for task in tasks:
        key_data = {**fingerprint, "seed": task["seed"], "side": task["side"],
                    "ticks": ticks, "draft": draft}
        key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        baseline_tasks.append({**task, "candidate": task["opponent"],
                               "out": str(baseline_root.resolve() / key)})
    # Queue candidates and controls together so neither batch waits for the other.
    with concurrent.futures.ProcessPoolExecutor(max_workers=min(workers, len(tasks) * 2)) as pool:
        candidates = [pool.submit(_run_pair, task) for task in tasks]
        controls = [pool.submit(_baseline, task) for task in baseline_tasks]
        matches = [future.result() for future in candidates]
        baselines = [future.result() for future in controls]
    per_seed: dict[int, list[float]] = {}
    for result in matches:
        outcome = result["outcome"]
        if outcome in ("draw", "time_limit"):
            value = 0.5
        else:
            winner = {"RedTeam": 0, "BlueTeam": 1}[outcome]
            value = float(winner == result["side"])
        result["candidate_outcome"] = value
        per_seed.setdefault(result["seed"], []).append(value)
    seed_means = [statistics.mean(values) for values in per_seed.values()]
    result = {"seed_balanced": _interval(seed_means),
              "per_seed": [{"seed": seed, "mean_outcome": statistics.mean(values)}
                           for seed, values in per_seed.items()],
              "matches_with_bad_seats": _bad_matches(matches),
              "baselines_with_bad_seats": _bad_matches(baselines),
              "identical_play": all(a["state_hash"] == b["state_hash"]
                                    for a, b in zip(matches, baselines, strict=True)),
              "matches": matches, "baselines": baselines,
              "seconds": time.monotonic() - started}
    _write_json(Path(out).resolve() / "screen.json", result)
    return result


def _body(args, report):
    if args.command == "host":
        result = host()
    elif args.command == "stats":
        result = stats(args.policy)
        if not result["strict_ok"]:
            report.fail(args.policy, "strict_compile", result["strict_error"])
    elif args.command == "replay":
        result = replay(args.replay, out=args.out, episode=args.episode)
    elif args.command == "match":
        try:
            roster = list(map(int, args.roster.split(","))) if args.roster else None
        except ValueError as error:
            raise gota_cli.UsageError("roster requires ten class IDs from 0 through 9") from error
        result = match(args.seat, seed=args.seed, ticks=args.ticks, draft=args.draft,
                       out=args.out, tables=args.tables, replay=args.save_replay, roster=roster)
    else:
        common = {"seeds": args.seeds, "ticks": args.ticks, "draft": args.draft,
                  "out": args.out, "workers": args.workers}
        if args.command == "record":
            try:
                sides = tuple(map(int, args.sides.split(",")))
            except ValueError as error:
                raise gota_cli.UsageError("sides must be 0, 1, or 0,1") from error
            result = record(args.candidate, args.opponent, sides=sides,
                            tables=not args.no_tables, replay=args.save_replay, **common)
        else:
            result = screen(args.candidate, args.opponent, cache=args.cache, **common)
    matches = [result] if args.command == "match" else result.get("matches", [])
    for index, match_result in enumerate(matches + result.get("baselines", [])):
        for hero in match_result["heroes"]:
            if hero["vm_failed"]:
                report.fail(f"match-{index}/seat-{hero['slot']}", "vm_failed", hero["vm_error"])
    report.counts["processed"] = len(matches) or 1
    if hasattr(args, "out"):
        report.output(Path(args.out).resolve())
    if not report.json_mode:
        print(json.dumps(result, indent=2))
    return result


def main(argv=None) -> int:
    parser = gota_cli.ArgumentParser("gota_local", __doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="verify and expand a pinned replay")
    replay_parser.add_argument("replay")
    replay_parser.add_argument("--out", required=True)
    replay_parser.add_argument("--episode")
    commands.add_parser("host", help="inspect the pinned host surface")
    stats_parser = commands.add_parser("stats", help="measure exact compiler resources")
    stats_parser.add_argument("policy")
    match_parser = commands.add_parser("match", help="run exactly ten policy seats")
    match_parser.add_argument("--seat", action="append", required=True)
    match_parser.add_argument("--roster", help="ten class IDs; replace policy draft decisions")
    match_parser.add_argument("--seed", type=int, default=1)
    match_parser.add_argument("--tables", action="store_true")
    match_parser.add_argument("--save-replay", action="store_true")
    for command in ("screen", "record"):
        sub = commands.add_parser(command)
        sub.add_argument("candidate")
        sub.add_argument("opponent")
        sub.add_argument("--seeds", default="1-4")
        sub.add_argument("--workers", type=int, default=4)
        if command == "screen":
            sub.add_argument("--cache")
        else:
            sub.add_argument("--sides", default="0,1")
            sub.add_argument("--no-tables", action="store_true")
            sub.add_argument("--save-replay", action="store_true")
            sub.add_argument("--tables", action="store_true", help="enabled by default")
        sub.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
        sub.add_argument("--draft", choices=("open", "team", "unique"), default="open")
        sub.add_argument("--out", required=True)
    match_parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    match_parser.add_argument("--draft", choices=("open", "team", "unique"), default="open")
    match_parser.add_argument("--out", required=True)
    def run_cli(args, report):
        try:
            return _body(args, report)
        except EngineError as error:
            report.fail("engine", "engine_error", str(error))
            return None
    return gota_cli.run(parser, run_cli, argv)


if __name__ == "__main__":
    raise SystemExit(main())
