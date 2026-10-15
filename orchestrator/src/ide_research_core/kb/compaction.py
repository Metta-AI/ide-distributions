"""Compaction: archive the longest-stale claims when too many are stale (System Design §3.5).

Stale share is stale claims over all claims in ``kb/claims`` (archived claims are not counted).
When it reaches ``trigger``, ``compact`` moves stale claims to ``kb/archive/`` with
``RecordStore.archive``, longest-stale first, until the share is at most ``target``. A claim's
stale age is the ``at`` of its last status-history entry to ``stale``.

Guards (a claim is never archived when any holds):

- it has been stale for less than ``min_stale_days``, or its stale time is unknown. After a game
  update storm, freshly stale claims wait for re-verification instead of being archived;
- it is in ``pending_reverify`` (bare id or ``ar://claim/<id>``);
- a hub or a live topic, hypothesis or experiment cites it (same citation rules as the linter).

Archived claims keep their id, status, links and body, and stay resolvable as
``ar://claim/<id>``. Each archive gets one ``kb/log/<ULID>.md`` entry (code ``ARCHIVED``).
"""

import datetime
from collections.abc import Collection

from ide_research_core.kb.apply import write_log
from ide_research_core.kb.lint import claims_cited_by_live_records, load_records
from ide_research_core.kb.staleness import stale_since
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore

ARCHIVED = "ARCHIVED"


def compact(
    layout: Layout,
    now: datetime.datetime,
    trigger: float = 0.10,
    target: float = 0.025,
    min_stale_days: int = 14,
    pending_reverify: Collection[str] = frozenset(),
) -> list[str]:
    """Archive stale claims per the rules above; return archived ids, longest-stale first."""
    claims = load_records(layout, "claim")
    stale = [claim for claim in claims if claim.meta.get("status") == "stale"]
    total = len(claims)
    if total == 0 or len(stale) / total < trigger:
        return []

    pending = {ref.removeprefix("ar://claim/") for ref in pending_reverify}
    cited = claims_cited_by_live_records(layout)
    oldest_allowed = now - datetime.timedelta(days=min_stale_days)
    candidates = []
    for claim in stale:
        since = stale_since(claim.meta)
        if since is None or since > oldest_allowed:
            continue
        if claim.id in pending or claim.id in cited:
            continue
        candidates.append((since, claim.id))
    candidates.sort()

    store = RecordStore(layout)
    at = now.isoformat()
    archived: list[str] = []
    stale_count = len(stale)
    for since, claim_id in candidates:
        if stale_count / total <= target:
            break
        share = f"{stale_count / total:.1%}"
        reason = (
            f"compaction at {at}: stale since {since.isoformat()}, stale share {share} "
            f"reached the {trigger:.1%} trigger"
        )
        store.archive(claim_id, reason, now)
        write_log(layout, at, ARCHIVED, claim_id, "kb/claims", "kb/archive")
        archived.append(claim_id)
        stale_count -= 1
        total -= 1
    return archived
