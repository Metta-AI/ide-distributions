"""Claim currency: stale marking and code-citation renewal on a game update (System Design §3.5).

``mark_stale`` marks every live claim (in ``kb/claims``) whose ``last_checked_version`` is older
than the current game version as ``stale``. Claims already stale, superseded or refuted are
left alone, and so are claims with no ``last_checked_version`` (their currency is unknown, not
older).

``renew_code_citations`` diffs the game source lines that claims cite between the old and new
game commits. A claim whose cited lines all stayed the same gets ``last_checked_version``
renewed. A ``mechanism`` claim that was stale only because of the version change also gets
its previous status back; other kinds keep ``stale`` for lint/apply and agents to judge, since
unchanged source does not show unchanged behaviour. A claim with any changed cited line, or a
cited file absent at either commit, is reported in ``RenewalReport.reverify`` for a
re-verification card.

Code citations are tier-S evidence entries whose ``ref`` is one of:

- ``<path>:<a>-<b>@<sha>`` or ``<path>:<a>@<sha>`` (the System Design form, canonical), or
- ``src:<path>#L<a>-L<b>`` or ``src:<path>#L<a>``.

Line numbers are 1-based and inclusive. The diff is always taken between the ``old_sha`` and
``new_sha`` passed in; a citation's own ``@sha`` is not used.

Every status or version change goes through ``RecordStore.update`` and gets one
``kb/log/<ULID>.md`` entry (codes ``STALE`` and ``RENEWED``).
"""

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

from ide_research_core.io import gitops
from ide_research_core.kb.apply import write_log
from ide_research_core.kb.lint import load_records
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore

STALE = "STALE"
RENEWED = "RENEWED"
STALE_REASON_PREFIX = "game version changed:"
_NOT_MARKABLE = {"stale", "superseded", "refuted"}
_NOT_RENEWABLE = {"superseded", "refuted"}

_SPEC_CITATION = re.compile(r"(?P<path>[^:@#\s]+):(?P<a>\d+)(?:-(?P<b>\d+))?@\S+")
_SRC_CITATION = re.compile(r"src:(?P<path>[^#\s]+)#L(?P<a>\d+)(?:-L(?P<b>\d+))?")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ", re.MULTILINE)


@dataclass
class RenewalReport:
    renewed: list[str] = field(default_factory=list)
    reverify: list[str] = field(default_factory=list)  # cited game code changed


def version_older(version: str, current: str) -> bool:
    """True if ``version`` is older than ``current``. Game versions are dotted numbers (Coworld
    versions such as ``2026.10.7.2``); they compare by their digit runs as integers ("9" <
    "10", "1.9" < "1.10"), ignoring surrounding whitespace; without digits, any difference
    counts as older."""
    version, current = str(version).strip(), str(current).strip()
    old_parts, new_parts = _numbers(version), _numbers(current)
    if old_parts and new_parts:
        return old_parts < new_parts
    return version != current


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def stale_since(meta: dict) -> datetime.datetime | None:
    """When a stale claim became stale: the ``at`` of its last status-history entry to stale."""
    for entry in reversed(meta.get("status_history") or []):
        if entry.get("to") == "stale":
            at = entry.get("at")
            return datetime.datetime.fromisoformat(at) if at else None
    return None


def mark_stale(layout: Layout, current_version: str, now: datetime.datetime) -> list[str]:
    """Mark live claims checked against an older game version as stale; return their ids."""
    store = RecordStore(layout)
    current_version = str(current_version).strip()
    at = now.isoformat()
    marked = []
    for claim in load_records(layout, "claim"):
        meta = claim.meta
        checked = meta.get("last_checked_version")
        if meta.get("status") in _NOT_MARKABLE or checked is None:
            continue
        if not version_older(str(checked), current_version):
            continue
        reason = (
            f"{STALE_REASON_PREFIX} last checked against {checked}, "
            f"current game version {current_version} (at {at})"
        )
        store.update(claim.id, _status_patch(meta, "stale", reason, at))
        write_log(layout, at, STALE, claim.id, meta["status"], "stale")
        marked.append(claim.id)
    return marked


def code_citations(meta: dict) -> list[tuple[str, int, int]]:
    """(path, first line, last line) for each tier-S code citation in a claim's evidence."""
    citations = []
    for entry in meta.get("evidence") or []:
        if entry.get("tier") != "S":
            continue
        ref = entry.get("ref", "")
        match = _SRC_CITATION.fullmatch(ref) or _SPEC_CITATION.fullmatch(ref)
        if match:
            first = int(match["a"])
            citations.append((match["path"], first, int(match["b"] or first)))
    return citations


def renew_code_citations(
    layout: Layout,
    game_repo: Path,
    old_sha: str,
    new_sha: str,
    *,
    current_version: str,
    now: datetime.datetime,
) -> RenewalReport:
    """Renew claims whose cited game source lines did not change from ``old_sha`` to
    ``new_sha``; report the claims whose cited lines changed, for a re-verification card."""
    store = RecordStore(layout)
    at = now.isoformat()
    report = RenewalReport()
    changed_ranges: dict[str, list[tuple[int, int]] | None] = {}
    for claim in load_records(layout, "claim"):
        meta = claim.meta
        citations = code_citations(meta)
        if not citations or meta.get("status") in _NOT_RENEWABLE:
            continue
        for path, _, _ in citations:
            if path not in changed_ranges:
                changed_ranges[path] = _changed_old_ranges(game_repo, old_sha, new_sha, path)
        if any(_range_changed(changed_ranges[p], first, last) for p, first, last in citations):
            report.reverify.append(claim.id)
        elif _renew(store, meta, current_version, at):
            write_log(
                layout, at, RENEWED, claim.id, meta.get("last_checked_version"), current_version
            )
            report.renewed.append(claim.id)
    report.renewed.sort()
    report.reverify.sort()
    return report


def _changed_old_ranges(
    repo: Path, old_sha: str, new_sha: str, path: str
) -> list[tuple[int, int]] | None:
    """Old-side (start, line count) of each diff hunk for ``path``; None if the file is absent
    at either commit (every cited line counts as changed)."""
    if gitops.show(repo, old_sha, path) is None or gitops.show(repo, new_sha, path) is None:
        return None
    diff = gitops.git(repo, "diff", "-U0", "--no-renames", old_sha, new_sha, "--", path)
    return [(int(start), int(count or 1)) for start, count in _HUNK.findall(diff)]


def _range_changed(hunks: list[tuple[int, int]] | None, first: int, last: int) -> bool:
    if hunks is None:
        return True
    for start, count in hunks:
        if count == 0:
            # Pure insertion after old line ``start``: it changes the range if it lands
            # between two cited lines.
            if first <= start < last:
                return True
        elif start <= last and start + count - 1 >= first:
            return True
    return False


def _renew(store: RecordStore, meta: dict, current_version: str, at: str) -> bool:
    """Renew one claim; False if it was already current."""
    stale_by_version = meta.get("status") == "stale" and str(
        meta.get("status_reason") or ""
    ).startswith(STALE_REASON_PREFIX)
    restore_status = stale_by_version and meta.get("kind") == "mechanism"
    if meta.get("last_checked_version") == current_version and not restore_status:
        return False
    patch = {"last_checked_version": current_version}
    if restore_status:
        reason = f"cited game source unchanged in {current_version}; renewed (at {at})"
        patch |= _status_patch(meta, _status_before_stale(meta), reason, at)
    store.update(meta["id"], patch)
    return True


def _status_before_stale(meta: dict) -> str:
    for entry in reversed(meta.get("status_history") or []):
        if entry.get("to") == "stale" and entry.get("from") not in (None, "stale"):
            return entry["from"]
    return "belief"


def _status_patch(meta: dict, status: str, reason: str, at: str) -> dict:
    """Front-matter patch for a status change with its history entry (stamped ``at``)."""
    entry = {"from": meta.get("status"), "to": status, "reason": reason, "ref": None, "at": at}
    return {
        "status": status,
        "status_reason": reason,
        "status_ref": None,
        "status_history": [*(meta.get("status_history") or []), entry],
    }
