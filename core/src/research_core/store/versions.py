"""Versions: policy candidates built from a commit or adopted from a platform upload (B.6).

A built Version records the exact commit and the artifact the build produced
(``{kind, path, digest, size}``); upload sends those bytes and checks the platform's hash
against the digest. An adopted Version stands for a policy version the user uploaded outside
the workspace, so the submit tool accepts it; it has no commit. ``version_parents`` keeps
``based_on``. A Version is uploaded at most once: ``mark_uploaded`` with the same policy
version id again is a no-op, with a different one is refused.

``submission_status`` is computed, never stored. Here it reads ``built`` or ``uploaded`` from
the record; the platform layer (entry 4) extends the ladder to submitted, qualifying,
competing and champion from the champion record and the platform's states.

``create`` with a ``task_id`` needs the Task's lease holder (A2.3); the check runs inside the
write's transaction.
"""

from __future__ import annotations

import builtins
import re
from typing import TYPE_CHECKING, Any

from research_core.store._common import (
    display_id,
    json_field,
    parse_display_id,
    parse_iso,
    parse_json,
    require,
)
from research_core.store.errors import NotFound, StoreError
from research_core.store.registry import RECORDS, VERSION, record_from_row

if TYPE_CHECKING:
    from research_core.store.actors import Actor
    from research_core.store.db import Store

ARTIFACT_KEYS = ("kind", "path", "digest", "size")
ARTIFACT_KINDS = ("file",)

_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


# --- writes ------------------------------------------------------------------------------


def create(
    store: Store,
    actor: Actor,
    *,
    summary: str,
    commit_sha: str,
    branch: str,
    artifact: Any,
    based_on: Any,
    task_id: int | str | None = None,
    experiment_id: int | str | None = None,
    image: str | None = None,
    policy_name: str | None = None,
) -> dict[str, Any]:
    """A built Version from a commit and its artifact. Event ``version.created``."""
    problems: list[str] = []
    require(problems, _is_text(summary), "summary is required")
    require(
        problems,
        isinstance(commit_sha, str) and _COMMIT_SHA.match(commit_sha),
        "commit_sha must be 7 to 40 hex digits",
    )
    require(problems, _is_text(branch), "branch is required")
    artifact = _check_artifact(artifact, problems)
    parents = _check_based_on(store, based_on, problems)
    if task_id is not None:
        task_id = _require_record(store, task_id, kind="task", field="task_id", problems=problems)
    if experiment_id is not None:
        experiment_id = _require_record(
            store, experiment_id, kind="experiment", field="experiment_id", problems=problems
        )
    require(problems, image is None or _is_text(image), "image must be text")
    require(problems, policy_name is None or _is_text(policy_name), "policy_name must be text")
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        if task_id is not None:
            _require_task_holder(store, task_id, actor)
        cursor = tx.execute(
            "INSERT INTO versions(summary, source, commit_sha, branch, artifact, task_id,"
            " experiment_id, image, policy_name, created, created_by)"
            " VALUES (?, 'built', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary.strip(),
                commit_sha.lower(),
                branch.strip(),
                json_field(artifact),
                task_id,
                experiment_id,
                image,
                policy_name,
                tx.at,
                str(actor),
            ),
        )
        version_id = cursor.lastrowid
        for parent in parents:
            tx.execute(
                "INSERT INTO version_parents(version_id, parent_id) VALUES (?, ?)",
                (version_id, parent),
            )
        tx.event(
            "version.created",
            display_id("V", version_id),
            {
                "source": "built",
                "commit_sha": commit_sha.lower(),
                "based_on": [display_id("V", parent) for parent in parents],
            },
        )
    return read(store, version_id)


def adopt(
    store: Store,
    actor: Actor,
    *,
    summary: str,
    policy_version_id: str,
    policy_name: str,
    uploaded_at: str,
    digest: str | None = None,
) -> dict[str, Any]:
    """An adopted Version for a policy version uploaded outside the workspace.

    It is already uploaded: ``uploaded`` carries the platform's id, the upload time and, when
    known, the platform's digest of the bytes. Event ``version.created``.
    """
    problems: list[str] = []
    require(problems, _is_text(summary), "summary is required")
    require(problems, _is_text(policy_version_id), "policy_version_id is required")
    require(problems, _is_text(policy_name), "policy_name is required")
    require(problems, _is_iso_time(uploaded_at), "uploaded_at must be an ISO 8601 time")
    require(problems, digest is None or _is_text(digest), "digest must be text")
    if _is_text(policy_version_id):
        existing = find_by_policy_version(store, policy_version_id)
        if existing is not None:
            problems.append(f"policy version {policy_version_id} is already {existing['id']}")
    if problems:
        raise StoreError(problems)
    uploaded: dict[str, Any] = {"policy_version_id": policy_version_id, "at": uploaded_at}
    if digest is not None:
        uploaded["digest"] = digest
    with store.tx(actor) as tx:
        cursor = tx.execute(
            "INSERT INTO versions(summary, source, uploaded, policy_name, created, created_by)"
            " VALUES (?, 'adopted', ?, ?, ?, ?)",
            (summary.strip(), json_field(uploaded), policy_name.strip(), tx.at, str(actor)),
        )
        version_id = cursor.lastrowid
        tx.event(
            "version.created",
            display_id("V", version_id),
            {"source": "adopted", "policy_version_id": policy_version_id},
        )
    return read(store, version_id)


def mark_uploaded(
    store: Store,
    actor: Actor,
    version_id: int | str,
    *,
    policy_version_id: str,
    at: str,
    digest_checked: bool,
) -> dict[str, Any]:
    """Record the upload: ``uploaded = {policy_version_id, at, digest_checked}``.

    At most once per Version: the same policy version id again is a no-op, a different one is
    refused, and a policy version already recorded on another Version is refused. Event
    ``version.uploaded``.
    """
    row = _fetch(store, version_id)
    vid = display_id("V", row["id"])
    problems: list[str] = []
    require(problems, _is_text(policy_version_id), "policy_version_id is required")
    require(problems, _is_iso_time(at), "at must be an ISO 8601 time")
    require(problems, isinstance(digest_checked, bool), "digest_checked must be true or false")
    if problems:
        raise StoreError(problems)
    current = parse_json(row["uploaded"])
    if current is not None:
        if current.get("policy_version_id") == policy_version_id:
            return read(store, row["id"])
        raise StoreError(
            [
                f"{vid} is already uploaded as policy version {current.get('policy_version_id')};"
                " a Version is uploaded at most once"
            ]
        )
    other = find_by_policy_version(store, policy_version_id)
    if other is not None:
        raise StoreError([f"policy version {policy_version_id} already belongs to {other['id']}"])
    uploaded = {"policy_version_id": policy_version_id, "at": at, "digest_checked": digest_checked}
    with store.tx(actor) as tx:
        tx.execute(
            "UPDATE versions SET uploaded = ? WHERE id = ?", (json_field(uploaded), row["id"])
        )
        tx.event("version.uploaded", vid, uploaded)
    return read(store, row["id"])


# --- reads -------------------------------------------------------------------------------


def read(store: Store, version_id: int | str) -> dict[str, Any]:
    """The Version with ``based_on``, the Experiments using it and its ``submission_status``."""
    row = _fetch(store, version_id)
    record = record_from_row(VERSION, row)
    parents = store.q(
        "SELECT parent_id FROM version_parents WHERE version_id = ? ORDER BY parent_id",
        (row["id"],),
    )
    record["based_on"] = [display_id("V", parent["parent_id"]) for parent in parents]
    record["experiments"] = _experiments_using(store, record["id"])
    record["submission_status"] = submission_status(record)
    return record


def list(store: Store, limit: int = 20) -> builtins.list[dict[str, Any]]:
    """The newest Versions first. (The binding name shadows the builtin inside this module.)"""
    rows = store.q("SELECT id FROM versions ORDER BY id DESC LIMIT ?", (limit,))
    return [read(store, row["id"]) for row in rows]


def find_by_policy_version(store: Store, policy_version_id: str) -> dict[str, Any] | None:
    """The Version uploaded (or adopted) as this platform policy version, if any."""
    row = store.one(
        "SELECT id FROM versions WHERE json_extract(uploaded, '$.policy_version_id') = ?",
        (policy_version_id,),
    )
    return None if row is None else read(store, row["id"])


def submission_status(record: dict[str, Any]) -> str:
    """``built`` or ``uploaded`` from the record alone.

    The hook the platform layer extends: the full ladder (submitted, qualifying, competing,
    champion) needs the champion record and the platform's states (B.6).
    """
    return "uploaded" if record.get("uploaded") else "built"


# --- validation --------------------------------------------------------------------------


def _check_artifact(artifact: Any, problems: list[str]) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        problems.append("artifact must be an object {kind, path, digest, size}")
        return {}
    kind = artifact.get("kind")
    if kind not in ARTIFACT_KINDS:
        problems.append(
            f"artifact.kind is {kind!r}; only file artifacts are supported (image policies are not)"
        )
    require(problems, _is_text(artifact.get("path")), "artifact.path is required")
    require(problems, _is_text(artifact.get("digest")), "artifact.digest is required")
    size = artifact.get("size")
    require(
        problems,
        isinstance(size, int) and not isinstance(size, bool) and size >= 0,
        "artifact.size must be a whole number of bytes",
    )
    for key in sorted(set(artifact) - set(ARTIFACT_KEYS)):
        problems.append(f"artifact.{key} is not an artifact field ({', '.join(ARTIFACT_KEYS)})")
    return dict(artifact)


def _check_based_on(store: Store, based_on: Any, problems: list[str]) -> builtins.list[int]:
    if isinstance(based_on, str) or not isinstance(based_on, builtins.list | tuple):
        problems.append("based_on must be a list of Version references")
        return []
    seen: builtins.list[int] = []
    parents: builtins.list[int] = []
    for value in based_on:
        number = _ref_number(value, "version")
        if number is None:
            problems.append(f"based_on: {value!r} is not a Version reference (V-<n>)")
        elif number in seen:
            problems.append(f"based_on: {display_id('V', number)} is listed twice")
        elif store.one("SELECT 1 FROM versions WHERE id = ?", (number,)) is None:
            problems.append(f"based_on: {display_id('V', number)} does not exist")
            seen.append(number)
        else:
            seen.append(number)
            parents.append(number)
    return parents


def _require_record(
    store: Store, value: Any, *, kind: str, field: str, problems: list[str]
) -> int | None:
    """The number of an existing record of ``kind`` from ``3`` or ``"V-3"``; else a problem."""
    spec = RECORDS[kind]
    number = _ref_number(value, kind)
    if number is None:
        problems.append(
            f"{field}: {value!r} is not a {spec.name.capitalize()} reference ({spec.prefix}-<n>)"
        )
        return None
    if store.one(f"SELECT 1 FROM {spec.table} WHERE id = ?", (number,)) is None:
        problems.append(f"{field}: {display_id(spec.prefix, number)} does not exist")
        return None
    return number


def _ref_number(value: Any, kind: str) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 1 else None
    if isinstance(value, str):
        try:
            parsed_kind, number = parse_display_id(value.strip())
        except ValueError:
            return None
        return number if parsed_kind == kind else None
    return None


def _require_task_holder(store: Store, task_id: int, actor: Actor) -> None:
    """A2.3: a Version created for a Task needs the Task's lease holder at the current epoch.

    Called inside the write's transaction, so the lease cannot change between the check and
    the write. ``tasks`` is imported here rather than at module level: it is written alongside
    this module and the lazy import keeps the two modules' import order free.
    """
    from research_core.store import tasks

    tasks.require_holder(store, task_id, actor)


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso_time(value: Any) -> bool:
    if not _is_text(value):
        return False
    try:
        parse_iso(value)
    except ValueError:
        return False
    return True


# --- rows --------------------------------------------------------------------------------


def _fetch(store: Store, version_id: int | str) -> Any:
    number = _ref_number(version_id, "version")
    if number is None:
        raise StoreError([f"{version_id!r} is not a Version reference (V-<n>)"])
    row = store.one("SELECT * FROM versions WHERE id = ?", (number,))
    if row is None:
        raise NotFound([f"{display_id('V', number)} does not exist"])
    return row


def _experiments_using(store: Store, vid: str) -> builtins.list[dict[str, str]]:
    """Experiments whose methods name this Version as the baseline or the candidate."""
    rows = store.q(
        "SELECT id, status, json_extract(methods, '$.baseline') AS baseline"
        " FROM experiments WHERE json_extract(methods, '$.baseline') = ?"
        " OR json_extract(methods, '$.candidate') = ? ORDER BY id",
        (vid, vid),
    )
    return [
        {
            "id": display_id("X", row["id"]),
            "role": "baseline" if row["baseline"] == vid else "candidate",
            "status": row["status"],
        }
        for row in rows
    ]
