"""``research kb ...``: the knowledge-base linter, currency and index tools.

Same envelope, exit codes and root resolution as ``research records`` (see
``ide_research_core.records.cli``). Findings are data: ``kb lint`` and ``kb hubs`` exit 0 with
``ok: true`` and list what they found; exit 1 is for failures such as an unreadable game repo.

- ``kb lint``: report findings (writes nothing).
- ``kb apply``: lint, then fix what the linter may fix (grades, demotions).
- ``kb index``: regenerate ``kb/index.md`` and ``kb/_generated/*``.
- ``kb stale --version V``: mark claims checked against older versions stale.
- ``kb renew --game-repo P --old SHA --new SHA --version V``: renew claims whose cited code
  did not change; list in ``reverify`` those whose code did.
- ``kb compact``: archive the longest-stale claims when too many are stale.
- ``kb hubs``: hub health flags.

``apply``, ``stale``, ``renew`` and ``compact`` change many claims at once: a maintenance card
runs them in its worktree, and ``workspace integrate`` merges the result like any card's.
``index`` is for local inspection; integrate regenerates the views on ``main``.
"""

import argparse
import datetime
from dataclasses import asdict
from pathlib import Path

from ide_research_core.cli import register_group
from ide_research_core.io import gitops
from ide_research_core.kb.apply import apply
from ide_research_core.kb.compaction import compact
from ide_research_core.kb.hubs import hub_flags
from ide_research_core.kb.index import write_generated
from ide_research_core.kb.lint import analyze
from ide_research_core.kb.staleness import mark_stale, renew_code_citations
from ide_research_core.paths import Layout
from ide_research_core.records.cli import _ok, _run
from ide_research_core.records.store import RecordStore


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
    return _ok({"renewed": report.renewed, "reverify": report.reverify})


def _compact(args, store: RecordStore) -> int:
    archived = compact(store.layout, _now())
    return _ok({"archived": archived})


def _hubs(args, store: RecordStore) -> int:
    findings = hub_flags(store.layout, args.size_limit_tokens)
    return _ok({"findings": [asdict(finding) for finding in findings]})


# --- parser ---


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="workspace root (default: worker worktree or canonical)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text, description=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    command("lint", _lint, "report knowledge-base findings (writes nothing)")
    command("apply", _apply, "lint, then apply the fixes the linter may make")
    sub = command("index", _index, "regenerate kb/index.md and kb/_generated/*")
    sub.add_argument("--token-budget", type=int, default=6000)
    sub = command(
        "stale", _stale, "mark claims checked against an older game version stale"
    )
    sub.add_argument("--version", required=True, help="current game version")
    sub = command(
        "renew", _renew, "renew claims whose cited game code did not change"
    )
    sub.add_argument("--game-repo", required=True, help="path to the game git repository")
    sub.add_argument("--old", required=True, help="previous game commit")
    sub.add_argument("--new", required=True, help="current game commit")
    sub.add_argument("--version", required=True, help="current game version")
    command(
        "compact",
        _compact,
        "archive the longest-stale claims when too many are stale",
    )
    sub = command("hubs", _hubs, "hub health flags")
    sub.add_argument("--size-limit-tokens", type=int, default=2000)


register_group("kb", setup)
