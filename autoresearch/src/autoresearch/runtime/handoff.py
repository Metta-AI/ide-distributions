"""Handoff validation at commit SHAs, and handoff record files (Overview C3, C4, C5, C11).

``validate_at`` judges one card run's result purely from git objects
(``git diff base..result`` and ``git show <sha>:<path>``), never from a working directory, so
the runtime can revalidate a handoff after the worker's worktree is gone. ``card finish``
(``card_finish.py``) and handoff processing (Task 3.4) both use it.

Handoff records live at ``.supervisor/ledger/handoffs/<task-id>/<run-id>.json`` and follow
``schemas/handoff.json``.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import frontmatter
import jsonschema
import yaml

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import REQUESTS_DIR, load_schema
from autoresearch.records.registry import (
    REGISTRY,
    Doc,
    in_record_dir,
    normalize,
    parse_record_path,
)
from autoresearch.records.store import RecordStore
from autoresearch.runtime.ledger import ledger_of

HANDOFFS_DIR = ".supervisor/ledger/handoffs"
INBOUND_DIR = ".supervisor/ledger/inbound"
STAGED_DIR = "comms/staged/"
# A card branch must not touch these: generated views and runtime-owned bookkeeping.
FORBIDDEN_FILES = ("kb/index.md",)
FORBIDDEN_DIRS = ("kb/_generated/", ".supervisor/", "comms/outbox/", "comms/inbox/")
# Inbound kinds the responsible card must answer with a staged ``in_reply_to`` message. A
# ``task`` is acknowledged by the runtime when its card publishes (C11); ``seen`` needs none.
ACK_REQUIRED_KINDS = frozenset({"reply", "steer"})
D23 = "D-23"  # a card changed an existing entity outside its refs

# Paths relative to the repository root (as git reports them).
_RELATIVE = Layout(Path())
_STORE = RecordStore(_RELATIVE)


@dataclass
class HandoffReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    violations: dict[str, int] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)  # record ids created in the branch
    changed_existing: list[str] = field(default_factory=list)  # existing ids modified/deleted
    journal: str | None = None  # path of this run's journal, if exactly one was found

    def to_dict(self) -> dict:
        """The report as stored in a handoff record (the journal is a top-level field there)."""
        data = asdict(self)
        del data["journal"]
        return data


# --- handoff records ---


def handoff_path(canonical: Path, task_id: str, run_id: str) -> Path:
    return ledger_of(canonical) / "handoffs" / task_id / f"{run_id}.json"


def read_handoff(path: Path) -> dict:
    return read_json(path)


def write_handoff(path: Path, record: dict) -> None:
    """Validate against ``schemas/handoff.json`` and write; the caller commits it."""
    jsonschema.validate(record, load_schema("handoff.json"))
    write_json(path, record)


def card_spec(card_meta: dict) -> CardSpec:
    """The spec fields validation needs, from ``runtime/cards/<task>.json`` or a handoff record.

    Validation reads only ``refs``; title, why, created_by and priority are not kept there
    and are filled with neutral values."""
    return CardSpec(
        request_id=card_meta["request_id"],
        title=card_meta.get("title", ""),
        mode=card_meta["mode"],
        why=card_meta.get("why", ""),
        refs=list(card_meta.get("refs", [])),
        created_by=card_meta.get("created_by", ""),
        priority=card_meta.get("priority", "P2"),
    )


# --- validation ---


def _is_forbidden(path: str) -> bool:
    return path in FORBIDDEN_FILES or path.startswith(FORBIDDEN_DIRS)


def _ref_ids(refs: list[str]) -> set[str]:
    """Entity ids named by ``ar://<type>/<id>[#fragment]`` refs."""
    ids = set()
    for uri in refs:
        target = uri.removeprefix("ar://").split("#", 1)[0]
        _, _, rest = target.partition("/")
        if rest and "/" not in rest:
            ids.add(rest)
    return ids


def _parse_doc(entity: str, content: bytes) -> Doc:
    text = content.decode("utf-8")
    if REGISTRY[entity].fmt == "json":
        return normalize(json.loads(text)), ""
    post = frontmatter.loads(text)
    return normalize(dict(post.metadata)), post.content


def _doc_at(repo: Path, sha: str, path: str, entity: str) -> Doc | None:
    content = gitops.show(repo, sha, path)
    return None if content is None else _parse_doc(entity, content)


def _json_at(repo: Path, sha: str, path: str):
    content = gitops.show(repo, sha, path)
    return None if content is None else json.loads(content)


def _message_problems(path: str, message) -> list[str]:
    """Minimal staged message checks until Task 3.5 adds ``schemas/message.json``."""
    if not isinstance(message, dict):
        return [f"{path}: a staged message must be a JSON object"]
    problems = [
        f"{path}: staged message needs a string {name!r}"
        for name in ("id", "kind", "title", "body")
        if not isinstance(message.get(name), str)
    ]
    if not isinstance(message.get("refs"), list):
        problems.append(f"{path}: staged message needs a 'refs' list")
    if message.get("in_reply_to") is not None and not isinstance(message["in_reply_to"], str):
        problems.append(f"{path}: staged message 'in_reply_to' must be a string")
    return problems


def _request_problems(path: str, request) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema("card_request.json"))
    return [f"{path}: card request: {error.message}" for error in validator.iter_errors(request)]


def _inbound_kind(repo: Path, inbound_id: str) -> str | None:
    """Kind of an inbound from canonical's ledger at HEAD (the ledger is never on a card
    branch); None if there is no record yet, which is treated as needing acknowledgement."""
    record = _json_at(repo, "HEAD", f"{INBOUND_DIR}/{inbound_id}.json")
    return record.get("kind") if isinstance(record, dict) else None


def _journals_of_other_runs(repo: Path, task_id: str, result_sha: str) -> set[str]:
    """Journal paths already claimed by this task's handoffs for other results. A retry runs on
    the same branch, so its diff from base also holds earlier runs' journals."""
    listing = gitops.git(repo, "ls-tree", "--name-only", "HEAD", f"{HANDOFFS_DIR}/{task_id}/")
    journals = set()
    for path in listing.splitlines():
        record = _json_at(repo, "HEAD", path)
        if record and record.get("result_sha") != result_sha and record.get("journal"):
            journals.add(record["journal"])
    return journals


def validate_at(
    repo: Path,
    base_sha: str,
    result_sha: str,
    spec: CardSpec,
    responsible_inbound: list[str],
    task_id: str,
) -> HandoffReport:
    """Judge the change ``base_sha..result_sha`` of card ``task_id`` from git objects only.

    ``repo`` is the canonical repository (worktrees share its objects). Checks: exactly one
    new journal for this run (card == task_id; content rules give ``## Decisions`` and
    ``doctrine_version``); every changed record passes ``validate_change``; existing entities
    changed are within ``spec.refs`` (else D-23); no generated, ledger, outbox or inbox file
    changed; staged messages and card requests are valid; each responsible ``reply``/``steer``
    inbound is answered by a staged message with ``in_reply_to``."""
    report = HandoffReport(ok=False)
    allowed_ids = _ref_ids(spec.refs)
    own_journals: list[str] = []
    replied_to: set[str] = set()

    for status, path in gitops.diff_status(repo, base_sha, result_sha):
        deleted = status == "D"
        if _is_forbidden(path):
            action = "deletes" if deleted else "changes"
            report.problems.append(f"{path}: a card {action} a runtime-owned file")
            continue
        parsed = parse_record_path(_RELATIVE, path)
        if parsed is not None:
            entity, id_ = parsed
            try:
                before = None if status == "A" else _doc_at(repo, base_sha, path, entity)
                after = None if deleted else _doc_at(repo, result_sha, path, entity)
            except (ValueError, yaml.YAMLError) as error:  # unparseable file: a finding
                report.problems.append(f"{path} cannot be parsed: {error}")
                continue
            report.problems += _STORE.validate_change(path, before, after)
            if status == "A":
                report.created.append(id_)
                if entity == "journal" and after[0].get("card") == task_id:
                    own_journals.append(path)
            else:
                report.changed_existing.append(id_)
                if id_ not in allowed_ids:
                    report.violations[D23] = report.violations.get(D23, 0) + 1
                    report.problems.append(
                        f"{path}: {id_} is an existing entity outside the card refs ({D23})"
                    )
            continue
        if deleted:
            continue  # deleting a staged message or a card request is allowed
        if in_record_dir(_RELATIVE, path):
            report.problems.append(f"{path} is not a valid record path")
            continue
        if path.startswith((STAGED_DIR, f"{REQUESTS_DIR}/")):
            try:
                content = _json_at(repo, result_sha, path)
            except ValueError as error:
                report.problems.append(f"{path} is not valid JSON: {error}")
                continue
            if path.startswith(STAGED_DIR):
                report.problems += _message_problems(path, content)
                if isinstance(content, dict) and isinstance(content.get("in_reply_to"), str):
                    replied_to.add(content["in_reply_to"])
            else:
                report.problems += _request_problems(path, content)

    earlier = _journals_of_other_runs(repo, task_id, result_sha)
    own_journals = [path for path in own_journals if path not in earlier]
    if len(own_journals) == 1:
        report.journal = own_journals[0]
    else:
        report.problems.append(
            f"the card must add exactly one new journal with card {task_id}; "
            f"found {len(own_journals)}"
        )

    for inbound_id in responsible_inbound:
        kind = _inbound_kind(repo, inbound_id)
        needs_ack = kind is None or kind in ACK_REQUIRED_KINDS
        if needs_ack and inbound_id not in replied_to:
            report.problems.append(
                f"responsible inbound {inbound_id} is not acknowledged: stage a message "
                f"with in_reply_to {inbound_id}"
            )

    report.created.sort()
    report.changed_existing.sort()
    report.ok = not report.problems
    return report
