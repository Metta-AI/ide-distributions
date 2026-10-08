"""``autoresearch kb ...``: the knowledge-base linter, currency and index tools.

Same envelope, exit codes and root resolution as ``autoresearch records`` (see
``autoresearch.records.cli``). Findings are data: ``kb lint`` and ``kb hubs`` exit 0 with
``ok: true`` and list what they found; exit 1 is for failures such as an unreadable game repo.

- ``kb lint``: report findings (writes nothing).
- ``kb apply``: lint, then fix what the linter may fix (grades, demotions).
- ``kb index``: regenerate ``kb/index.md`` and ``kb/_generated/*``.
- ``kb stale --version V``: mark claims checked against older versions stale.
- ``kb renew --game-repo P --old SHA --new SHA --version V``: renew claims whose cited code
  did not change; request re-verification for those whose code did.
- ``kb compact``: archive the longest-stale claims when too many are stale. Claims with a
  re-verification request in ``cards/requests`` are never archived.
- ``kb hubs [--request]``: hub health flags; ``--request`` also writes maintenance requests.

``apply``, ``index``, ``stale``, ``renew`` and ``compact`` are operator/runtime commands: they
change many claims at once, so a card running them would fail the D-23 ref check at card
finish. queue-keeper's KB step runs stale marking, apply, compaction and the generated views
on canonical every run; ``renew`` is an operator command. Cards use ``kb lint`` and ``kb hubs`` and edit only the claims their
refs name.
"""

import argparse
import datetime
from dataclasses import asdict
from pathlib import Path

from autoresearch.cli import register_group
from autoresearch.io import gitops
from autoresearch.kb.apply import apply
from autoresearch.kb.compaction import compact
from autoresearch.kb.hubs import hub_flags, request_hub_maintenance
from autoresearch.kb.index import write_generated
from autoresearch.kb.lint import analyze
from autoresearch.kb.staleness import mark_stale, pending_reverification, renew_code_citations
from autoresearch.paths import Layout
from autoresearch.records.cli import _ok, _run
from autoresearch.records.store import RecordStore


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _relative(layout: Layout, paths: list[Path]) -> list[str]:
    return [path.relative_to(layout.root).as_posix() for path in paths]


# --- handlers ---


def _lint(args, store: RecordStore) -> int:
    findings = [asdict(finding) for finding in analyze(store.layout).findings]
    return _ok({"findings": findings, "count": len(findings)})


def _apply(args, store: RecordStore) -> int:
    report = analyze(store.layout)
    written = apply(store.layout, report, _now())
    findings = [asdict(finding) for finding in report.findings]
    return _ok({"findings": findings, "written": _relative(store.layout, written)})


def _index(args, store: RecordStore) -> int:
    written = write_generated(store.layout, args.token_budget)
    return _ok({"written": _relative(store.layout, written)})


def _stale(args, store: RecordStore) -> int:
    return _ok({"marked": mark_stale(store.layout, args.version, _now())})


def _renew(args, store: RecordStore) -> int:
    repo = Path(args.game_repo)
    # An unreadable repo or unknown commit would otherwise look like "cited file absent" and
    # request re-verification of every code-cited claim.
    for sha in (args.old, args.new):
        try:
            gitops.git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
        except gitops.GitError as error:
            raise ValueError(str(error)) from None
    report = renew_code_citations(
        store.layout,
        repo,
        args.old,
        args.new,
        current_version=args.version,
        now=_now(),
    )
    return _ok({"renewed": report.renewed, "requested": report.requested})


def _compact(args, store: RecordStore) -> int:
    archived = compact(store.layout, _now(), pending_reverify=pending_reverification(store.layout))
    return _ok({"archived": archived})


def _hubs(args, store: RecordStore) -> int:
    findings = hub_flags(store.layout, args.size_limit_tokens)
    data = {"findings": [asdict(finding) for finding in findings]}
    if args.request:
        data["requested"] = request_hub_maintenance(store.layout, findings)
    return _ok(data)


# --- parser ---


RUNTIME_ONLY = "[operator/runtime only, not for cards] "


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="workspace root (default: worker worktree or canonical)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text, description=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    command("lint", _lint, "report knowledge-base findings (writes nothing)")
    command("apply", _apply, RUNTIME_ONLY + "lint, then apply the fixes the linter may make")
    sub = command("index", _index, RUNTIME_ONLY + "regenerate kb/index.md and kb/_generated/*")
    sub.add_argument("--token-budget", type=int, default=6000)
    sub = command(
        "stale", _stale, RUNTIME_ONLY + "mark claims checked against an older game version stale"
    )
    sub.add_argument("--version", required=True, help="current game version")
    sub = command(
        "renew", _renew, RUNTIME_ONLY + "renew claims whose cited game code did not change"
    )
    sub.add_argument("--game-repo", required=True, help="path to the game git repository")
    sub.add_argument("--old", required=True, help="previous game commit")
    sub.add_argument("--new", required=True, help="current game commit")
    sub.add_argument("--version", required=True, help="current game version")
    command(
        "compact",
        _compact,
        RUNTIME_ONLY + "archive the longest-stale claims when too many are stale",
    )
    sub = command("hubs", _hubs, "hub health flags")
    sub.add_argument("--size-limit-tokens", type=int, default=2000)
    sub.add_argument("--request", action="store_true", help="write maintenance card requests")


register_group("kb", setup)
