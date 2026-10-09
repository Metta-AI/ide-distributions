"""``autoresearch stats ...``: ingest episode rows, calibrate noise, apply the decision rule.

Same envelope, exit codes and root resolution as ``autoresearch records`` (see
``autoresearch.records.cli``). A blocked decision is still ``ok: true`` with
``promote_eligible: false``; exit 1 is reserved for failures such as a missing record or rows.

Files under the workspace root:

- ``stats/rows/<league-id or E-id>.jsonl``: kept EpisodeRows, one JSON object per line.
  ``stats ingest`` creates or replaces the file.
- ``stats/metric-noise.yaml``: the noise record (``stats noise``).
- ``stats design <E> --request-file F [--planned-pairs N]`` freezes the experiment's
  ``evaluation_design`` (``stats.freeze.freeze_design``) from the request, the game contract
  (``--game`` or ``AUTORESEARCH_GAME``) and the noise record for the league's current game
  version (``Softmax.game_version``, so it needs a profile context). It stores the request as
  ``evaluation_design_request``. The experiment must be ``designed`` and its
  ``environment.game_version`` must be the current version. A refusal is exit 1 with
  ``error.reason``: ``needs_calibration`` (no noise record for the current version),
  ``not_designed``, ``version_mismatch`` or ``bad_request`` (the request does not freeze).
  ``--planned-pairs`` (the pairs ``delta`` is computed for) defaults to the request's seed count.
  Opponents (A12): a request with ``opponent_count: N`` (and optionally ``opponent_episodes``,
  default 100) is sampled here: the league snapshot from that many recent league episodes and
  the standings (``stats.opponents.league_snapshot``, our player from ``whoami``, our policy
  the request's ``baseline_version``), one line-up of N per seed salted with the experiment
  id, both frozen into the design. A refusal to sample (no opponents in the snapshot) is
  ``bad_request``. A fixed ``opponents`` set needs ``opponent_rationale``.
- ``stats/outcomes/<E-id>.json``: the last ``stats evaluate`` outcome, with the champion and
  candidate it was evaluated against, ``rows_sha256`` (of the rows file) and ``design_sha256``
  (sha256 of the design as compact sorted-key JSON). The promotion check reads it and can
  detect a stale outcome by recomputing both hashes.

The champion for ``stats evaluate`` comes from ``runtime.promotion.current_champion`` (the
policy the game's league shows competing for our player, A9) when the command runs inside a
profile (``HERMES_HOME`` and ``AUTORESEARCH_GAME`` set); ``--champion`` must then agree with
it. Outside a profile, or when the league shows no champion, ``--champion`` is required.
"""

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.games.loader import GameError, load_game
from autoresearch.io.docs import _atomic_write_text
from autoresearch.paths import Layout
from autoresearch.records.cli import BadArguments, _check_id, _ok, _run
from autoresearch.records.store import RecordStore
from autoresearch.runtime.promotion import current_champion
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env
from autoresearch.stats.decision_rule import evaluate
from autoresearch.stats.design import DesignError, EvaluationDesign
from autoresearch.stats.freeze import freeze_design
from autoresearch.stats.ingest import EpisodeRow, ingest, ingest_rows, pair
from autoresearch.stats.noise import (
    NoiseError,
    compute_noise,
    load_noise,
    noise_revision,
    write_noise,
)
from autoresearch.stats.opponents import DEFAULT_EPISODES, league_snapshot, sample_lineups

# games/ lives at the autoresearch project root, next to schemas/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
CHAMPION_UNKNOWN = "champion unknown: pass --champion"
GAME_ENV = "AUTORESEARCH_GAME"
# A league id becomes a file name under stats/rows/, so no path separators or leading dots.
_LEAGUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _check_league(league: str) -> str:
    if not _LEAGUE_ID.fullmatch(league) or ".." in league:
        raise BadArguments(f"invalid league id {league!r}: use letters, digits, '.', '_', '-'")
    return league


def _rows_path(layout: Layout, name: str) -> Path:
    return layout.stats / "rows" / f"{name}.jsonl"


def _parse_row(line: str, where: str) -> EpisodeRow:
    try:
        data = json.loads(line)
        row = EpisodeRow(**data)
    except (json.JSONDecodeError, TypeError) as error:
        raise BadArguments(f"{where}: not an EpisodeRow: {error}") from None
    row.seats, row.opponents = tuple(row.seats), tuple(row.opponents)
    if not isinstance(row.metrics, dict):
        raise BadArguments(f"{where}: metrics must be an object")
    for name, value in row.metrics.items():
        # A well-formed row with a bad value is bad data, not a bad argument: ValueError is
        # what the shared CLI runner maps to exit 1.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{where}: metric {name!r} is not a number: {value!r}")  # noqa: TRY004
    return row


def _read_rows(path: Path, label: str) -> list[EpisodeRow]:
    return _parse_rows(path.read_text(encoding="utf-8"), label)


def _parse_rows(text: str, label: str) -> list[EpisodeRow]:
    lines = text.splitlines()
    return [
        _parse_row(line, f"{label} line {number}")
        for number, line in enumerate(lines, start=1)
        if line.strip()
    ]


def _write_rows(path: Path, rows: list[EpisodeRow]) -> None:
    text = "".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in rows)
    _atomic_write_text(path, text)


def _load_design(store: RecordStore, experiment: str) -> tuple[dict, EvaluationDesign]:
    meta, _ = store.load(_check_id(experiment))
    if not meta.get("evaluation_design"):
        raise ValueError(f"{experiment} has no frozen evaluation_design")
    return meta, EvaluationDesign.from_dict(meta["evaluation_design"])


def _design_sha256(design: EvaluationDesign) -> str:
    canonical = json.dumps(design.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Refusal(ValueError):
    """A business refusal with a stable ``reason`` code (exit 1)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _with_refusals(handler):
    def run(args, store: RecordStore) -> int:
        try:
            return handler(args, store)
        except Refusal as error:
            print(
                json.dumps(
                    {"ok": False, "error": {"reason": error.reason, "message": str(error)}},
                    sort_keys=True,
                )
            )
            return 1

    return run


# --- handlers ---


def _ingest(args, store: RecordStore) -> int:
    if args.league is not None:
        _check_league(args.league)
    try:
        rows = _read_rows(Path(args.rows), args.rows)
    except OSError as error:
        raise BadArguments(f"cannot read {args.rows}: {error.strerror or error}") from None
    if args.league is not None:
        if not args.game_version or not args.game:
            raise BadArguments("--league needs --game-version and --game")
        try:
            game = load_game(_REPO_ROOT, args.game)
        except GameError as error:
            raise BadArguments(str(error)) from None
        metrics = [metric.name for metric in game.outcome_metrics]
        kept, report = ingest_rows(rows, args.game_version, metrics)
        target = args.league
    else:
        _, design = _load_design(store, args.experiment)
        kept, report = ingest(rows, design)
        target = args.experiment
    path = _rows_path(store.layout, target)
    _write_rows(path, kept)
    return _ok({"target": target, "path": str(path), "report": asdict(report)})


def _noise(args, store: RecordStore) -> int:
    path = _rows_path(store.layout, _check_league(args.league))
    if not path.exists():
        raise ValueError(f"no rows for league {args.league}; run stats ingest --league first")
    metrics = [name for name in args.metrics.split(",") if name]
    if not metrics:
        raise BadArguments("--metrics is empty")
    rows = _read_rows(path, str(path))
    record = compute_noise(rows, metrics, args.game_version, args.evidence)
    revision = write_noise(store.layout, record)
    return _ok({"noise_rev": revision, "path": str(store.layout.stats / "metric-noise.yaml")})


def _current_game_version(league_id: str) -> str:
    try:
        ctx = runctx.from_env()
    except KeyError as error:
        raise BadArguments(
            f"{error.args[0]} is not set: the current game version needs a profile context"
        ) from None
    try:
        return str(softmax_from_env(os.environ, ctx.canonical).game_version(league_id)).strip()
    except (RuntimeError, OSError) as error:
        raise ValueError(f"cannot read the current game version from Softmax: {error}") from None


def _design(args, store: RecordStore) -> int:
    experiment = _check_id(args.experiment)
    request = _json_object_file(args.request_file)
    game_name = args.game or os.environ.get(GAME_ENV)
    if not game_name:
        raise BadArguments(f"pass --game or set {GAME_ENV}")
    try:
        game = load_game(_REPO_ROOT, game_name)
    except GameError as error:
        raise BadArguments(str(error)) from None
    meta, _ = store.load(experiment)
    if meta.get("status") != "designed":
        raise Refusal(
            "not_designed",
            f"{experiment} is {meta.get('status')}; the design freezes only while designed",
        )
    version = _current_game_version(game.league_id)
    declared = (meta.get("environment") or {}).get("game_version")
    if declared is None or str(declared).strip() != version:
        raise Refusal(
            "version_mismatch",
            f"{experiment} environment.game_version is {declared!r} but the current game "
            f"version of {game.league_id} is {version!r}",
        )
    try:
        noise = load_noise(store.layout, version)
    except NoiseError as error:
        raise Refusal("needs_calibration", str(error)) from None
    planned = args.planned_pairs or len(request.get("seeds") or [])
    if planned < 1:
        raise BadArguments("--planned-pairs must be positive (or give the request seeds)")
    try:
        sampled = _with_sampled_opponents(request, experiment, game.league_id)
        design = freeze_design(
            {"evaluation_design_request": sampled},
            game,
            noise,
            planned,
            noise_revision(store.layout),
        )
    except (DesignError, NoiseError, TypeError) as error:
        raise Refusal("bad_request", f"the request does not freeze: {error}") from None
    store.update(
        experiment,
        {"evaluation_design_request": request, "evaluation_design": design.to_dict()},
    )
    return _ok(
        {"experiment": experiment, "evaluation_design": design.to_dict(), "planned_pairs": planned}
    )


def _with_sampled_opponents(request: dict, experiment: str, league_id: str) -> dict:
    """The request plus ``lineups`` and ``league_snapshot`` when it asks for
    ``opponent_count`` sampled opponents (module docstring, A12); else the request as is."""
    if "opponent_count" not in request:
        return request
    count = request["opponent_count"]
    episodes = request.get("opponent_episodes", DEFAULT_EPISODES)
    if not isinstance(count, int) or not isinstance(episodes, int) or episodes < 1:
        raise DesignError("opponent_count and opponent_episodes must be positive integers")
    ctx = runctx.from_env()
    sm = softmax_from_env(os.environ, ctx.canonical)
    try:
        snapshot = league_snapshot(
            sm.league_episodes(league_id, episodes),
            sm.standings(league_id),
            league_id,
            sm.whoami().player_id,
            request.get("baseline_version"),
            datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except (RuntimeError, OSError) as error:
        raise ValueError(f"cannot read the league to sample opponents: {error}") from None
    lineups = sample_lineups(snapshot, request.get("seeds") or [], count, experiment)
    return {**request, "lineups": lineups, "league_snapshot": snapshot}


def _json_object_file(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise BadArguments(f"cannot read {path}: {error.strerror or error}") from None
    except json.JSONDecodeError as error:
        raise BadArguments(f"{path} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise BadArguments(f"{path} must hold a JSON object")
    return value


def _confirmed_champion() -> str | None:
    """The league's champion for our player inside a profile (None when it shows none, A9);
    None outside a profile."""
    game_name = os.environ.get(GAME_ENV)
    if not game_name:
        return None
    try:
        ctx = runctx.from_env()
    except KeyError:
        return None
    try:
        game = load_game(_REPO_ROOT, game_name)
    except GameError as error:
        raise BadArguments(str(error)) from None
    try:
        sm = softmax_from_env(os.environ, ctx.canonical)
        return current_champion(ctx, sm, game.league_id)
    except (RuntimeError, OSError) as error:  # whoami / leaderboard failures (exit 1)
        raise ValueError(f"cannot confirm the champion with Softmax: {error}") from None


def _resolve_champion(args) -> str:
    """The league's champion when there is one (``--champion`` must then agree), else
    ``--champion``."""
    champion = _confirmed_champion()
    if champion is None:
        if not args.champion:
            raise BadArguments(CHAMPION_UNKNOWN)
        return args.champion
    if args.champion and args.champion != champion:
        raise ValueError(
            f"champion mismatch: --champion {args.champion} but the current champion is {champion}"
        )
    return champion


def _evaluate(args, store: RecordStore) -> int:
    meta, design = _load_design(store, args.experiment)
    binding = meta.get("candidate_binding")
    if not binding:
        raise ValueError(f"{args.experiment} has no candidate_binding; bind the candidate first")
    candidate = binding["platform_policy_version_id"]
    path = _rows_path(store.layout, args.experiment)
    if not path.exists():
        raise ValueError(
            f"no rows for {args.experiment}; run stats ingest --experiment {args.experiment}"
        )
    champion = _resolve_champion(args)
    rows_bytes = path.read_bytes()
    pairs = pair(_parse_rows(rows_bytes.decode("utf-8"), str(path)), design, candidate)
    outcome = evaluate(design, pairs, candidate, champion)
    stored = {
        "experiment": args.experiment,
        "champion": champion,
        "candidate_platform_id": candidate,
        "noise_rev": design.noise_rev,
        "rows_sha256": hashlib.sha256(rows_bytes).hexdigest(),
        "design_sha256": _design_sha256(design),
        **outcome.to_dict(),
    }
    _atomic_write_text(
        store.layout.stats / "outcomes" / f"{args.experiment}.json",
        json.dumps(stored, indent=2, sort_keys=True) + "\n",
    )
    return _ok(stored)


# --- parser ---


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="workspace root (default: worker worktree or canonical)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    sub = command("ingest", _ingest, "clean EpisodeRow JSONL into stats/rows/<target>.jsonl")
    sub.add_argument("--rows", required=True, help="JSONL file, one EpisodeRow per line")
    target = sub.add_mutually_exclusive_group(required=True)
    target.add_argument("--league", help="league id: league-wide rows for noise calibration")
    target.add_argument("--experiment", help="experiment id: rows checked against its design")
    sub.add_argument("--game-version", help="required with --league")
    sub.add_argument("--game", help="game name under games/; required with --league")

    sub = command("noise", _noise, "compute and write the noise record from league rows")
    sub.add_argument("--league", required=True)
    sub.add_argument("--game-version", required=True)
    sub.add_argument("--metrics", required=True, help="comma-separated metric names")
    sub.add_argument("--evidence", nargs="*", default=[], metavar="REF")

    sub = command(
        "design",
        _with_refusals(_design),
        "freeze an experiment's evaluation_design (designed only)",
    )
    sub.add_argument("experiment")
    sub.add_argument(
        "--request-file",
        required=True,
        help="JSON evaluation_design_request (see stats.freeze.freeze_design)",
    )
    sub.add_argument(
        "--planned-pairs", type=int, help="pairs delta is computed for (default: seeds)"
    )
    sub.add_argument("--game", help=f"game name under games/ (default ${GAME_ENV})")

    sub = command("evaluate", _evaluate, "apply the decision rule; writes stats/outcomes/<E>.json")
    sub.add_argument("experiment")
    sub.add_argument("--champion", help="current champion policy version id")


register_group("stats", setup)
