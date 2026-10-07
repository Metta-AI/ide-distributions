"""``autoresearch stats ...``: ingest episode rows, calibrate noise, apply the decision rule.

Same envelope, exit codes and root resolution as ``autoresearch records`` (see
``autoresearch.records.cli``). A blocked decision is still ``ok: true`` with
``promote_eligible: false``; exit 1 is reserved for failures such as a missing record or rows.

Files under the workspace root:

- ``stats/rows/<league-id or E-id>.jsonl``: kept EpisodeRows, one JSON object per line.
  ``stats ingest`` creates or replaces the file.
- ``stats/metric-noise.yaml``: the noise record (``stats noise``).
- ``stats/outcomes/<E-id>.json``: the last ``stats evaluate`` outcome, with the champion and
  candidate it was evaluated against, ``rows_sha256`` (of the rows file) and ``design_sha256``
  (sha256 of the design as compact sorted-key JSON). The promotion check reads it and can
  detect a stale outcome by recomputing both hashes.
"""

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from autoresearch.cli import register_group
from autoresearch.games.loader import GameError, load_game
from autoresearch.io.docs import _atomic_write_text
from autoresearch.paths import Layout
from autoresearch.records.cli import BadArguments, _check_id, _ok, _run
from autoresearch.records.store import RecordStore
from autoresearch.stats.decision_rule import evaluate
from autoresearch.stats.design import EvaluationDesign
from autoresearch.stats.ingest import EpisodeRow, ingest, ingest_rows, pair
from autoresearch.stats.noise import compute_noise, write_noise

# games/ lives at the autoresearch project root, next to schemas/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
CHAMPION_UNKNOWN = "champion unknown: pass --champion until promotion lands"
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


def _resolve_champion(args, store: RecordStore) -> str:
    """The promotion module's champion when it exists (``--champion`` must then agree),
    else ``--champion``."""
    try:
        from autoresearch.runtime.promotion import current_champion
    except ImportError:
        if not args.champion:
            raise BadArguments(CHAMPION_UNKNOWN) from None
        return args.champion
    champion = current_champion(store.layout)
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
    champion = _resolve_champion(args, store)
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

    sub = command("evaluate", _evaluate, "apply the decision rule; writes stats/outcomes/<E>.json")
    sub.add_argument("experiment")
    sub.add_argument("--champion", help="current champion policy version id")


register_group("stats", setup)
