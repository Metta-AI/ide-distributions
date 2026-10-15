"""Apply pass: the linter's trusted writes to claim front matter (System Design §3.5).

``apply`` fixes two finding codes and ignores the rest (they need an agent):

- ``STATUS_GRADE_MISMATCH``: write the computed ``evidence_grade``.
- ``VERIFIED_NOT_ELIGIBLE``: demote the claim to ``belief`` with a fresh ``status_reason``
  naming the finding and the time.

Writes go through ``RecordStore.update``, so the record mutability rules hold, and claim text
(the body) is never touched. Each change gets one ``kb/log/<ULID>.md`` entry whose front matter
is ``{at, finding, entity, before, after}``. Each finding is re-checked against the current
workspace first, so applying again (or applying a stale report) writes nothing.

A maintenance card runs ``kb apply`` in its worktree; integrate merges the result.
"""

import datetime
from pathlib import Path

from ulid import ULID

from ide_research_core.io.docs import write_doc
from ide_research_core.kb.lint import (
    STATUS_GRADE_MISMATCH,
    VERIFIED_NOT_ELIGIBLE,
    LintReport,
    computed_state,
)
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore


def apply(layout: Layout, report: LintReport, now: datetime.datetime) -> list[Path]:
    """Fix grade mismatches and demote ineligible verified claims; return every path written."""
    store = RecordStore(layout)
    at = now.isoformat()
    written: list[Path] = []
    for finding in report.findings:
        if finding.code == STATUS_GRADE_MISMATCH:
            change = _write_grade(layout, store, finding.entity)
        elif finding.code == VERIFIED_NOT_ELIGIBLE:
            change = _demote(layout, store, finding.entity, at)
        else:
            continue
        if change is None:
            continue
        before, after = change
        written.append(store.path_of(finding.entity))
        written.append(write_log(layout, at, finding.code, finding.entity, before, after))
    return written


def _write_grade(layout: Layout, store: RecordStore, claim_id: str) -> tuple | None:
    state = computed_state(layout, claim_id)
    if state is None or state[0].get("evidence_grade") == state[1]:
        return None
    meta, computed, _ = state
    store.update(claim_id, {"evidence_grade": computed})
    return meta.get("evidence_grade"), computed


def _demote(layout: Layout, store: RecordStore, claim_id: str, at: str) -> tuple | None:
    state = computed_state(layout, claim_id)
    if state is None or state[0].get("status") != "verified" or state[2]:
        return None
    meta = state[0]
    reason = f"lint {VERIFIED_NOT_ELIGIBLE} at {at}: evidence no longer meets the verification rule"
    entry = {"from": "verified", "to": "belief", "reason": reason, "ref": None, "at": at}
    store.update(
        claim_id,
        {
            "status": "belief",
            "status_reason": reason,
            "status_ref": None,
            "status_history": [*(meta.get("status_history") or []), entry],
        },
    )
    return "verified", "belief"


def write_log(layout: Layout, at: str, code: str, entity: str, before, after) -> Path:
    """Write one ``kb/log/<ULID>.md`` entry for a trusted KB change; return its path."""
    path = layout.kb_log / f"{ULID()}.md"
    meta = {"at": at, "finding": code, "entity": entity, "before": before, "after": after}
    write_doc(path, meta, f"{code}: {entity} {before} -> {after}\n")
    return path
