"""``research game``: show, list, new, check and install game folders.

- ``show [--name G]`` prints ``{"ok": true, "data": {name, bindings, reference_types,
  outcome_metrics, performance_guardrails, min_episodes_floor, league_id, noise_source,
  layers, compiler, root, path}}``: the validated ``GAME.md`` front matter
  (``games.loader.load_game``), ``root`` (run the bindings from it) and the file's path. The
  guide body is not printed; read ``path`` for it. ``--name`` defaults to ``RESEARCH_GAME``,
  which inside a card worktree is the card's game (``games.scope``). Exit 1 when the contract
  is missing or invalid, 2 when no game is named.
- ``list`` prints every shipped and installed game, its league, whether it has a research
  workspace on this host, and how an installed one was installed.
- ``new --name G --league L [--title T] [--source URL [--source-ref R]] [--mixin URL
  [--mixin-ref R]] [--out DIR]`` scaffolds ``<DIR>/games/G/`` from the template (``DIR``
  defaults to the card worktree, else the current directory); see ``games.authoring``.
- ``check --name G [--root DIR]`` prints the install gate's problems and gaps for
  ``<DIR>/games/G/`` (default: the card worktree's folder when it has one, else the shipped or
  installed game).
- ``install --name G --worktree W --commit SHA [--replace] [--card ID --run N]`` installs a
  card's committed folder on the host. The orchestrator only.

Every command prints one JSON object, ``{"ok": true, "data": ...}`` or ``{"ok": false,
"error": {"reason", "message", "details"?}}``.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from docker.utils.build import exclude_paths

from ide_research_core import policy_files, sources
from ide_research_core.cli import register_group
from ide_research_core.games import authoring
from ide_research_core.games.loader import GameError, load_game
from ide_research_core.games.scope import (
    GAME_ENV,
    WORKER_ENV,
    list_games,
    load_named_game,
    locate_game,
)
from ide_research_core.io import gitops

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS = 0, 1, 2


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _fail(reason: str, message: str, code: int, details: dict | None = None) -> int:
    error = {"reason": reason, "message": message}
    if details:
        error["details"] = details
    _emit({"ok": False, "error": error})
    return code


def _show(args: argparse.Namespace) -> int:
    name = args.name or os.environ.get(GAME_ENV)
    if not name:
        _emit({"ok": False, "error": {"message": f"pass --name or set {GAME_ENV}"}})
        return 2
    try:
        game = load_named_game(name)
    except GameError as error:
        _emit({"ok": False, "error": {"message": str(error)}})
        return 1
    data = asdict(game)
    del data["guide"]
    data["root"] = str(game.root)
    data["path"] = str(game.root / "games" / name / "GAME.md")
    _emit({"ok": True, "data": data})
    return 0


def _list(args) -> dict:
    games = list_games(os.environ)
    for entry in games:
        try:
            entry["league_id"] = load_game(Path(entry["root"]), entry["name"]).league_id
        except GameError as error:  # an installed folder can be broken; list it anyway
            entry["error"] = str(error)
        record = Path(entry["path"]) / authoring.INSTALL_RECORD
        if entry["kind"] == "installed" and record.is_file():
            entry["install"] = json.loads(record.read_text(encoding="utf-8"))
    return {"games": games, "default": os.environ.get(GAME_ENV)}


def _new(args) -> dict:
    out = Path(args.out or os.environ.get(WORKER_ENV) or Path.cwd())
    source = sources.fetch(args.source, args.source_ref) if args.source else None
    mixin = sources.fetch(args.mixin, args.mixin_ref) if args.mixin else None
    return authoring.new(args.name, args.league, out, title=args.title, source=source, mixin=mixin)


def _check(args) -> dict:
    if args.root:
        root = Path(args.root)
    else:
        worktree = os.environ.get(WORKER_ENV)
        local = Path(worktree) if worktree else Path.cwd()
        found = (local / "games" / args.name / "GAME.md").is_file()
        root = local if found else locate_game(args.name)
    return authoring.check(args.name, root)


def _install(args) -> dict:
    if (Path(os.environ["HERMES_HOME"]) / "plugins" / "researcher_guard").is_dir():
        raise authoring.AuthoringError(
            "coordinator_required", "Only the workspace coordinator installs games"
        )
    return authoring.install(
        args.name,
        Path(args.worktree),
        args.commit,
        replace=args.replace,
        card=args.card,
        run_id=args.run,
    )


def container_sources(
    root: Path, context: Path, strategy: Path, dockerfile: str = "Dockerfile"
) -> dict[str, str]:
    """Use Docker's context matcher, including the selected Dockerfile's ignore rules."""
    ignore = context / f"{dockerfile}.dockerignore"
    if not ignore.is_file():
        ignore = context / ".dockerignore"
    patterns = (
        [
            line.strip()
            for line in ignore.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if ignore.is_file()
        else []
    )
    selected = exclude_paths(str(context), patterns, dockerfile=dockerfile)
    paths = {context / name for name in selected if ".git" not in Path(name).parts}
    paths.add(strategy)
    if ignore.is_file():
        paths.add(ignore)
    hashes = {}
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            hashes[relative] = hashlib.sha256(os.fsencode(target)).hexdigest()
            continue
        if path.is_dir():
            continue
        if not policy_files.source_path(relative) or not path.is_file():
            raise authoring.AuthoringError("bad_arguments", f"Unsupported build source: {relative}")
        with path.open("rb") as stream:
            hashes[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


def _build_container(args) -> dict:
    """Build an OCI archive and the existing publication receipt; no queue or service."""
    if not os.environ.get("BUILDKIT_HOST"):
        raise authoring.AuthoringError(
            "missing_buildkit", "This host has no configured BuildKit endpoint"
        )
    context = Path(args.context).resolve()
    strategy = Path(args.strategy).resolve()
    out = Path(args.out).resolve()
    if out.is_relative_to(context):
        raise authoring.AuthoringError("bad_arguments", "--out must be outside --context")
    if out.exists():
        raise authoring.AuthoringError("bad_arguments", "--out must be a new build directory")
    root = (
        Path(os.environ[WORKER_ENV]).resolve()
        if WORKER_ENV in os.environ
        else Path(gitops.git(context, "rev-parse", "--show-toplevel")).resolve()
    )
    if not context.is_relative_to(root) or not strategy.is_relative_to(root):
        raise authoring.AuthoringError(
            "bad_arguments", "Build inputs must be in the task workspace"
        )
    source_hashes = container_sources(root, context, strategy, args.dockerfile)
    primary = (context / args.dockerfile).relative_to(root).as_posix()
    if primary not in source_hashes:
        raise authoring.AuthoringError(
            "bad_arguments", "The Dockerfile must be retained in the workspace"
        )
    strategy_hash = hashlib.sha256(strategy.read_bytes()).hexdigest()
    source_commit = gitops.head(context)
    out.mkdir(parents=True)
    artifact = out / "policy.tar"
    result = subprocess.run(
        [
            str(Path(sys.executable).parent / "coworld"),
            "build-policy",
            str(context),
            "--dockerfile",
            args.dockerfile,
            "--output",
            str(artifact),
        ],
        stdout=sys.stderr,
        check=False,
    )
    if result.returncode:
        raise authoring.AuthoringError("build_failed", "Container build failed; see build output")
    if container_sources(root, context, strategy, args.dockerfile) != source_hashes:
        raise authoring.AuthoringError("source_changed", "Source changed during the build")
    with artifact.open("rb") as stream:
        artifact_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    version = {
        "artifact": artifact.name,
        "artifact_type": "oci-image",
        "policy_sha256": artifact_hash,
        "strategy_sha256": strategy_hash,
        "source_commit": source_commit,
        "source_path": primary,
        "source_hashes": source_hashes,
        "gates": {"container_build": {"passed": True}},
        "evaluation": {"status": "not_run"},
    }
    (out / "version.json").write_text(json.dumps(version, indent=2) + "\n")
    from ide_research_core.build_sources import capture

    version = capture(out, root)
    return {"build_dir": str(out), **version}


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            data = handler(args)
        except authoring.AuthoringError as error:
            return _fail(error.reason, str(error), EXIT_FAILED, error.details)
        except sources.SourceError as error:
            code = EXIT_BAD_ARGS if error.reason == "bad_arguments" else EXIT_FAILED
            return _fail(error.reason, str(error), code)
        except GameError as error:
            return _fail("bad_arguments", str(error), EXIT_BAD_ARGS)
        except gitops.GitError as error:
            return _fail("git", str(error), EXIT_FAILED)
        except KeyError as error:
            return _fail("bad_arguments", f"{error.args[0]} is not set", EXIT_BAD_ARGS)
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


def setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    sub = commands.add_parser("show", help="print the game contract (GAME.md front matter)")
    sub.add_argument("--name", help=f"game directory under games/ (default ${GAME_ENV})")
    sub.set_defaults(func=_show)

    sub = commands.add_parser("list", help="list shipped and installed games")
    sub.set_defaults(func=_run(_list))

    sub = commands.add_parser("new", help="scaffold games/<name>/ from the game template")
    sub.add_argument("--name", required=True, help="the new game's folder name")
    sub.add_argument("--league", required=True, help="the league its evaluations play in")
    sub.add_argument("--title", help="the game's display name (default: --name)")
    sub.add_argument("--source", help="the game source: https://github.com/<owner>/<repo>")
    sub.add_argument("--source-ref", help="branch, tag or commit of --source")
    sub.add_argument("--mixin", help="a mixin to install: https://github.com/<owner>/<repo>")
    sub.add_argument("--mixin-ref", help="branch, tag or commit of --mixin")
    sub.add_argument("--out", help="directory that receives games/<name>/ (default: worktree)")
    sub.set_defaults(func=_run(_new))

    sub = commands.add_parser("check", help="check a game folder before installation")
    sub.add_argument("--name", required=True)
    sub.add_argument("--root", help="directory that holds games/<name>/")
    sub.set_defaults(func=_run(_check))

    sub = commands.add_parser(
        "build-container",
        help="build an OCI policy and publication evidence using BuildKit",
    )
    sub.add_argument(
        "--context", required=True, help="Dockerfile build context in the task worktree"
    )
    sub.add_argument("--strategy", required=True, help="STRATEGY.md describing the candidate")
    sub.add_argument("--out", required=True, help="new build directory outside the context")
    sub.add_argument("--dockerfile", default="Dockerfile")
    sub.set_defaults(func=_run(_build_container))

    sub = commands.add_parser("install", help="install a card's game folder (orchestrator)")
    sub.add_argument("--name", required=True)
    sub.add_argument("--worktree", required=True, help="the card worktree holding games/<name>/")
    sub.add_argument("--commit", required=True, help="the reviewed commit to install")
    sub.add_argument("--replace", action="store_true", help="replace an installed game")
    sub.add_argument("--card")
    sub.add_argument("--run", type=int)
    sub.set_defaults(func=_run(_install))


register_group("game", setup)
