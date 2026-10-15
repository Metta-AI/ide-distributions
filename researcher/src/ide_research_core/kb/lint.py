"""Knowledge-base linter: ``analyze`` reads the workspace and reports problems (System Design §3.5).

Pure: it reads files and writes nothing; ``kb.apply`` fixes the findings it can fix. Findings
are sorted by (code, entity, message), so the same workspace always gives the same report.

Links. Bodies cite records as ``[[<id>]]`` (Basic Memory wiki links, the hub syntax) or as
``ar://<type>/<id>[#anchor]``. Wiki links whose target is not a record id (a page title) and
``ar://`` types that are not records (journal decisions resolve with their journal; skill
proposals, files) are not checked. Claims resolve in ``kb/claims`` or ``kb/archive``.

Citations. A record cites a claim when its body links it or any front-matter value is the
claim id or its ``ar://claim/`` URI (topic and hub ``claims``, hypothesis ``source``, claim
``evidence`` refs and ``relations``). A claim citing itself does not count.

Codes:

- ``LINK_UNRESOLVED``: a body link in a claim, hub, topic, hypothesis or experiment names a
  record that does not exist (a hub's missing claims are ``HUB_CITES_MISSING_CLAIM`` instead).
- ``ORPHAN_CLAIM``: a live claim that no hub, topic, hypothesis, experiment or other claim cites.
- ``HUB_SENTENCE_UNCITED``: a non-empty, non-heading hub body line with no claim link.
- ``HUB_CITES_MISSING_CLAIM``: a hub cites (body or ``claims``) a claim that does not exist.
- ``UNRESOLVED_CONTRADICTION``: a live claim has contradicting evidence nothing resolves.
- ``STATUS_GRADE_MISMATCH``: a live claim's stored ``evidence_grade`` differs from the computed one.
- ``VERIFIED_NOT_ELIGIBLE``: a live claim is ``verified`` but not verification-eligible.
- ``ARCHIVED_STILL_CITED``: a hub or a live topic, hypothesis or experiment cites an archived claim.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from ide_research_core.io.docs import read_doc
from ide_research_core.kb.grading import Ev, grade, unresolved_contradictions, verification_eligible
from ide_research_core.paths import Layout
from ide_research_core.records.registry import (
    REGISTRY,
    entity_of_id,
    is_archived_path,
    iter_record_files,
    normalize,
    parse_record_path,
    path_for,
)

LINK_UNRESOLVED = "LINK_UNRESOLVED"
ORPHAN_CLAIM = "ORPHAN_CLAIM"
HUB_SENTENCE_UNCITED = "HUB_SENTENCE_UNCITED"
HUB_CITES_MISSING_CLAIM = "HUB_CITES_MISSING_CLAIM"
UNRESOLVED_CONTRADICTION = "UNRESOLVED_CONTRADICTION"
STATUS_GRADE_MISMATCH = "STATUS_GRADE_MISMATCH"
VERIFIED_NOT_ELIGIBLE = "VERIFIED_NOT_ELIGIBLE"
ARCHIVED_STILL_CITED = "ARCHIVED_STILL_CITED"

# Records whose bodies and front matter can cite claims.
CITING_ENTITIES = ("claim", "hub", "topic", "hypothesis", "experiment")
_CLOSED_STATES = {
    "topic": {"achieved", "closed"},
    "hypothesis": {"supported", "falsified", "abandoned"},
    "experiment": {"reviewed", "stopped"},
}

_WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_AR_LINK = re.compile(r"ar://([a-z_-]+)/([A-Za-z0-9_-]+)")
_CLAIM_URI = re.compile(r"ar://claim/(C-[0-9A-HJKMNP-TV-Z]{26})")


@dataclass(frozen=True, order=True)
class Finding:
    code: str
    entity: str
    message: str


@dataclass
class LintReport:
    findings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class _Record:
    entity: str
    id: str
    meta: dict
    body: str


def claim_evs(meta: dict) -> list[Ev]:
    """The grading view of a claim's ``evidence`` entries."""
    return [
        Ev(
            ref=entry["ref"],
            tier=entry["tier"],
            direct=entry["direct"],
            stance=entry["stance"],
            source_group=entry.get("source_group") or entry["ref"],
            target=entry.get("target"),
        )
        for entry in meta.get("evidence") or []
    ]


def superseded_or_refuted_refs(claims: list[dict]) -> set[str]:
    """Refs (bare id and ``ar://claim/`` URI) of claims that are superseded or refuted."""
    ids = [meta["id"] for meta in claims if meta.get("status") in ("superseded", "refuted")]
    return {*ids, *(f"ar://claim/{id_}" for id_ in ids)}


def load_records(layout: Layout, entity: str) -> list[_Record]:
    """Records in the entity's primary directory (archived claims: ``load_archived_claims``)."""
    records = []
    for path in iter_record_files(layout, entity):
        rel = path.relative_to(layout.root).as_posix()
        parsed = parse_record_path(layout, rel)
        if parsed is None or parsed[0] != entity or is_archived_path(layout, rel):
            continue
        meta, body = read_doc(path)
        records.append(_Record(entity, parsed[1], normalize(meta), body))
    return records


def load_archived_claims(layout: Layout) -> list[_Record]:
    records = []
    for path in sorted(layout.archive.glob("*.md")):
        meta, body = read_doc(path)
        records.append(_Record("claim", meta["id"], normalize(meta), body))
    return records


def body_links(body: str) -> Iterator[tuple[str, str | None]]:
    """(record id, ar:// type or None for a wiki link) for each checkable link in a body."""
    for match in _WIKI_LINK.finditer(body):
        target = match[1].strip()
        if _is_record_id(target):
            yield target, None
    for match in _AR_LINK.finditer(body):
        if match[1] in REGISTRY:
            yield match[2], match[1]


def _is_record_id(value: str) -> bool:
    try:
        entity_of_id(value)
    except ValueError:
        return False
    return True


def _claim_ids_in(value) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _claim_ids_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _claim_ids_in(item)
    elif isinstance(value, str):
        if value.startswith("C-") and _is_record_id(value):
            yield value
        yield from _CLAIM_URI.findall(value)


def cited_claims(record: _Record) -> set[str]:
    """Claim ids a record cites, from body links and front-matter values (not its own id)."""
    meta = {key: value for key, value in record.meta.items() if key != "id"}
    cited = set(_claim_ids_in(meta))
    cited |= {id_ for id_, _ in body_links(record.body) if id_.startswith("C-")}
    cited.discard(record.id)
    return cited


def _resolves(layout: Layout, id_: str, ar_type: str | None, archived: set[str]) -> bool:
    if not _is_record_id(id_):
        return False
    entity = entity_of_id(id_).name
    if ar_type is not None and ar_type != entity:
        return False
    if entity == "claim" and id_ in archived:
        return True
    try:
        return path_for(layout, id_).exists()
    except LookupError:
        return False


def _is_live(record: _Record) -> bool:
    if record.entity == "hub":
        return True
    state = record.meta.get("state" if record.entity == "hypothesis" else "status")
    return state not in _CLOSED_STATES.get(record.entity, ())


def claims_cited_by_live_records(layout: Layout) -> set[str]:
    """Claim ids cited by any hub or any live topic, hypothesis or experiment."""
    cited: set[str] = set()
    for entity in ("hub", "topic", "hypothesis", "experiment"):
        for record in load_records(layout, entity):
            if _is_live(record):
                cited |= cited_claims(record)
    return cited


def _hub_uncited_lines(body: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(body.splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if not any(id_.startswith("C-") for id_, _ in body_links(text)):
            yield number, text


def analyze(layout: Layout) -> LintReport:
    records = {entity: load_records(layout, entity) for entity in CITING_ENTITIES}
    claims = records["claim"]
    archived = load_archived_claims(layout)
    archived_ids = {record.id for record in archived}
    live_claim_ids = {record.id for record in claims}
    findings: list[Finding] = []

    citers: dict[str, set[str]] = {}
    for entity in CITING_ENTITIES:
        for record in records[entity]:
            for claim_id in cited_claims(record):
                citers.setdefault(claim_id, set()).add(record.id)

    for entity in CITING_ENTITIES:
        for record in records[entity]:
            findings += _link_findings(layout, record, archived_ids)

    for hub in records["hub"]:
        findings += _hub_findings(hub, live_claim_ids | archived_ids)

    dead_refs = superseded_or_refuted_refs([r.meta for r in [*claims, *archived]])
    for claim in claims:
        if not citers.get(claim.id):
            findings.append(
                Finding(
                    ORPHAN_CLAIM,
                    claim.id,
                    "no hub, topic, hypothesis, experiment or other claim cites this claim",
                )
            )
        findings += _claim_findings(claim, dead_refs)

    for entity in ("hub", "topic", "hypothesis", "experiment"):
        for record in records[entity]:
            if not _is_live(record):
                continue
            for claim_id in sorted(cited_claims(record) & archived_ids):
                findings.append(
                    Finding(ARCHIVED_STILL_CITED, record.id, f"cites archived claim {claim_id}")
                )

    return LintReport(sorted(set(findings)))


def _link_findings(layout: Layout, record: _Record, archived: set[str]) -> list[Finding]:
    findings = []
    for id_, ar_type in sorted(set(body_links(record.body)), key=str):
        if record.entity == "hub" and id_.startswith("C-") and ar_type in (None, "claim"):
            continue  # reported as HUB_CITES_MISSING_CLAIM
        if not _resolves(layout, id_, ar_type, archived):
            link = f"ar://{ar_type}/{id_}" if ar_type else f"[[{id_}]]"
            findings.append(Finding(LINK_UNRESOLVED, record.id, f"link {link} does not resolve"))
    return findings


def _hub_findings(hub: _Record, existing_claims: set[str]) -> list[Finding]:
    findings = [
        Finding(HUB_SENTENCE_UNCITED, hub.id, f"line {number} cites no claim: {text}")
        for number, text in _hub_uncited_lines(hub.body)
    ]
    for claim_id in sorted(cited_claims(hub) - existing_claims):
        findings.append(
            Finding(
                HUB_CITES_MISSING_CLAIM, hub.id, f"cites claim {claim_id}, which does not exist"
            )
        )
    return findings


def _claim_findings(claim: _Record, dead_refs: set[str]) -> list[Finding]:
    meta, findings = claim.meta, []
    evs = claim_evs(meta)
    open_contradictions = unresolved_contradictions(evs, dead_refs)
    if open_contradictions:
        refs = ", ".join(e.ref for e in open_contradictions)
        findings.append(
            Finding(
                UNRESOLVED_CONTRADICTION, claim.id, f"unresolved contradicting evidence: {refs}"
            )
        )
    computed = grade(evs, dead_refs)
    if meta.get("evidence_grade") != computed:
        findings.append(
            Finding(
                STATUS_GRADE_MISMATCH,
                claim.id,
                f"stored evidence_grade {meta.get('evidence_grade')} != computed {computed}",
            )
        )
    if meta.get("status") == "verified" and not verification_eligible(meta["kind"], evs, dead_refs):
        findings.append(
            Finding(
                VERIFIED_NOT_ELIGIBLE,
                claim.id,
                f"verified {meta['kind']} claim is not verification-eligible",
            )
        )
    return findings


def computed_state(layout: Layout, claim_id: str) -> tuple[dict, str, bool] | None:
    """(front matter, computed grade, verification-eligible) for one live claim against the
    current workspace, or None if it is not a live claim. ``apply`` uses it to act only on
    findings that still hold."""
    live = [r.meta for r in load_records(layout, "claim")]
    dead_refs = superseded_or_refuted_refs([*live, *(r.meta for r in load_archived_claims(layout))])
    meta = next((m for m in live if m["id"] == claim_id), None)
    if meta is None:
        return None
    evs = claim_evs(meta)
    return meta, grade(evs, dead_refs), verification_eligible(meta["kind"], evs, dead_refs)
