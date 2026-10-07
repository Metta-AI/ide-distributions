"""Generated knowledge-base views: ``kb/index.md`` and ``kb/_generated/*`` (System Design §3.5).

``write_generated`` is pure in its inputs: the same workspace always gives byte-identical
files (sorted keys, stable ordering, no timestamps), so queue-keeper can regenerate after
every merge and git shows only real changes.

Files:

- ``kb/index.md``: hubs first (title and one-line summary), then live claims grouped by Topic
  title (claims with no topic under "Unfiled"). A claim line is
  ``- [<id>](ar://claim/<id>) <status> <grade> — <summary>``; the status ``stale`` is the stale
  marker. The grade is the stored ``evidence_grade``.
- ``kb/_generated/backlinks.json``: claim id -> sorted ids of the records that cite it.
- ``kb/_generated/contradictions.json``: claim id -> refs of its unresolved contradicting evidence.
- ``kb/_generated/versions.jsonl``: one line per version record, its events folded in, by id.

Token budget. Tokens are estimated as ``len(text) // 4``. When the index is over budget, claim
lines are dropped lowest grade first (very_low, low, moderate, high; then by id, last id first),
and a final line ``… N claims omitted (budget)`` is added. Hubs and headings are never dropped.
"""

import json
from pathlib import Path

from autoresearch.io.docs import write_json, write_text
from autoresearch.kb.grading import unresolved_contradictions
from autoresearch.kb.lint import (
    CITING_ENTITIES,
    cited_claims,
    claim_evs,
    load_archived_claims,
    load_records,
    superseded_or_refuted_refs,
)
from autoresearch.paths import Layout

UNFILED = "Unfiled"
_DROP_ORDER = ("very_low", "low", "moderate", "high")
_SUMMARY_LIMIT = 120


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def write_generated(layout: Layout, token_budget: int = 6000) -> list[Path]:
    """Write the index and generated views; return the paths written."""
    records = {entity: load_records(layout, entity) for entity in CITING_ENTITIES}
    claims = records["claim"]
    index = layout.kb / "index.md"
    backlinks = layout.kb_generated / "backlinks.json"
    contradictions = layout.kb_generated / "contradictions.json"
    versions = layout.kb_generated / "versions.jsonl"

    write_text(index, render_index(records["hub"], records["topic"], claims, token_budget))
    write_json(backlinks, _backlinks([r for e in CITING_ENTITIES for r in records[e]]))
    write_json(contradictions, _contradictions(layout, claims))
    write_text(versions, _versions_jsonl(layout))
    return [index, backlinks, contradictions, versions]


def render_index(hubs, topics, claims, token_budget: int) -> str:
    topic_titles = {topic.id: topic.meta.get("title", topic.id) for topic in topics}
    header = ["# Knowledge base index", "", "## Hubs", ""]
    for hub in sorted(hubs, key=lambda h: h.id):
        header.append(
            f"- [{hub.meta.get('title', hub.id)}](ar://hub/{hub.id}) — {_hub_summary(hub)}"
        )

    entries = []  # (group title, claim, line)
    for claim in sorted(claims, key=lambda c: c.id):
        group = _topic_title(claim.meta, topic_titles)
        entries.append((group, claim, _claim_line(claim)))

    kept = list(entries)
    omitted = 0
    # Lowest grade first; within a grade the highest id first (stable sort on the id order).
    drop_queue = sorted(entries, key=lambda e: e[1].id, reverse=True)
    drop_queue.sort(key=lambda e: _DROP_ORDER.index(_grade(e[1])))
    while estimate_tokens(_assemble(header, kept, omitted)) > token_budget and drop_queue:
        victim = drop_queue.pop(0)
        kept.remove(victim)
        omitted += 1
    return _assemble(header, kept, omitted)


def _grade(claim) -> str:
    grade = claim.meta.get("evidence_grade")
    return grade if grade in _DROP_ORDER else "very_low"


def _assemble(header: list[str], entries, omitted: int) -> str:
    lines = list(header)
    groups: dict[str, list[str]] = {}
    for group, _, line in entries:
        groups.setdefault(group, []).append(line)
    # Named topics alphabetically, "Unfiled" last.
    for group in sorted(groups, key=lambda g: (g == UNFILED, g)):
        lines += ["", f"## {group}", "", *groups[group]]
    if omitted:
        lines += ["", f"… {omitted} claims omitted (budget)"]
    return "\n".join(lines) + "\n"


def _topic_title(meta: dict, topic_titles: dict[str, str]) -> str:
    topics = sorted(meta.get("topics") or [])
    if not topics:
        return UNFILED
    return topic_titles.get(topics[0], topics[0])


def _first_line(body: str) -> str:
    for line in body.splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text if len(text) <= _SUMMARY_LIMIT else text[: _SUMMARY_LIMIT - 1] + "…"
    return ""


def _hub_summary(hub) -> str:
    return hub.meta.get("load_when") or _first_line(hub.body)


def _claim_line(claim) -> str:
    meta = claim.meta
    return (
        f"- [{claim.id}](ar://claim/{claim.id}) {meta.get('status', 'belief')} "
        f"{meta.get('evidence_grade', 'very_low')} — {_first_line(claim.body)}"
    )


def _backlinks(records) -> dict[str, list[str]]:
    citers: dict[str, set[str]] = {}
    for record in records:
        for claim_id in cited_claims(record):
            citers.setdefault(claim_id, set()).add(record.id)
    return {claim_id: sorted(ids) for claim_id, ids in sorted(citers.items())}


def _contradictions(layout: Layout, claims) -> dict[str, list[str]]:
    dead_refs = superseded_or_refuted_refs(
        [r.meta for r in [*claims, *load_archived_claims(layout)]]
    )
    found = {}
    for claim in sorted(claims, key=lambda c: c.id):
        open_ones = unresolved_contradictions(claim_evs(claim.meta), dead_refs)
        if open_ones:
            found[claim.id] = sorted(e.ref for e in open_ones)
    return found


def _versions_jsonl(layout: Layout) -> str:
    lines = []
    for version_file in sorted(layout.versions.glob("*/version.json")):
        record = json.loads(version_file.read_text(encoding="utf-8"))
        events = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (version_file.parent / "events").glob("*.json")
        ]
        record["events"] = sorted(events, key=lambda event: event["id"])
        lines.append((record["id"], json.dumps(record, sort_keys=True)))
    return "".join(f"{line}\n" for _, line in sorted(lines))
