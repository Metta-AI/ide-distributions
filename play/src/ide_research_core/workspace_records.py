"""Scoped IDE projections use native records, with canonical Git publication and receipts."""

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ide_research_core import workspace
from ide_research_core.io import gitops
from ide_research_core.kb.index import write_generated
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: Literal["topic", "claim", "presentation"]
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    meta: dict[str, JsonValue]
    body: str = ""


class RecordView(RecordPatch):
    created_at: str | None = None
    updated_at: str | None = None


class RecordsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "save"]
    include_goals: bool = False
    league_id: str = Field(pattern=r"^league_[a-f0-9-]{36}$")
    owner_id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    patch: RecordPatch | None = None
    intent: JsonValue = None
    projection: JsonValue = None
    request_id: str = Field(default="", max_length=200)


class RecordReceipt(BaseModel):
    fingerprint: str
    result: dict[str, JsonValue]


def execute(root: Path, request: RecordsRequest) -> dict:
    head = workspace.sync(root)
    revision = int(gitops.git(root, "rev-list", "--count", head))
    prefix = f"control/ide/{request.league_id}/research"
    receipt_path = (
        f"{prefix}/requests/{hashlib.sha256(request.request_id.encode()).hexdigest()}.json"
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [request.owner_id, request.league_id, request.intent],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if request.request_id:
        saved = gitops.show(root, head, receipt_path)
        if saved is not None:
            receipt = RecordReceipt.model_validate_json(saved)
            if receipt.fingerprint != fingerprint:
                raise workspace.WorkspaceError(
                    "request_conflict", "This request already names another record edit"
                )
            return {"replay": receipt.result} if request.action == "read" else receipt.result
    if request.action == "save":
        if not request.request_id or request.patch is None or request.intent is None:
            raise workspace.WorkspaceError(
                "bad_arguments", "Record saves require a patch, intent and original request ID"
            )
        if request.revision != revision:
            raise workspace.WorkspaceError(
                "conflict", "Research changed; read its current revision"
            )
    # Work only from committed bytes. Unpublished local edits never enter a UI read or save.
    with tempfile.TemporaryDirectory(prefix="research-records-") as directory:
        snapshot = Path(directory)
        paths = gitops.git(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            head,
            "--",
            "topics",
            "kb",
            "versions",
            "hypotheses",
            "experiments",
            "results",
            "journal",
            "lessons",
            "evidence",
            f"{prefix}/views",
        ).splitlines()
        for path in paths:
            content = gitops.show(root, head, path)
            assert content is not None
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        store = RecordStore(Layout(snapshot))
        records: list[RecordView] = []
        scoped_topics = {}
        for entity in ("topic", "claim"):
            for native_id in store.find(entity):
                meta, body = store.load(native_id)
                scoped = (
                    meta.get("league_id") == request.league_id
                    and meta.get("agent_id") == request.owner_id
                )
                if entity == "claim" and "league_id" not in meta and "agent_id" not in meta:
                    references = meta["topics"]
                    parents = [scoped_topics[id_] for id_ in references if id_ in scoped_topics]
                    if (
                        parents
                        and len(parents) == len(references)
                        and len({parent["policy_id"] for parent in parents}) == 1
                    ):
                        meta = {
                            **meta,
                            **{
                                key: parents[0][key]
                                for key in ("league_id", "agent_id", "policy_id")
                            },
                            "policy_name": parents[0].get("policy_name", parents[0]["policy_id"]),
                            "game": parents[0].get("game"),
                        }
                        scoped = True
                if not scoped:
                    continue
                if entity == "topic":
                    scoped_topics[native_id] = meta
                if not request.include_goals:
                    meta.pop("goals", None)
                path = store.path_of(native_id).relative_to(snapshot).as_posix()
                dates = gitops.git(root, "log", "--format=%cI", head, "--", path).splitlines()
                records.append(
                    RecordView.model_validate(
                        {
                            "entity": entity,
                            "id": native_id,
                            "meta": meta,
                            "body": body,
                            "created_at": dates[-1],
                            "updated_at": dates[0],
                        }
                    )
                )
        for path in sorted((snapshot / prefix / "views").glob("*.json")):
            view = RecordView.model_validate_json(path.read_bytes())
            annotations = view.meta["ide"]
            if isinstance(annotations, dict) and annotations["agent_id"] == request.owner_id:
                records.append(view)
        if request.action == "read":
            return {
                "revision": revision,
                "records": [item.model_dump(mode="json", exclude_none=True) for item in records],
            }
        patch = request.patch
        assert patch is not None
        if not request.include_goals and "goals" in patch.meta:
            raise workspace.WorkspaceError(
                "bad_arguments", "Use the goal command to update goal context"
            )
        if patch.entity == "presentation":
            annotations = patch.meta["ide"]
            if not isinstance(annotations, dict) or annotations["agent_id"] != request.owner_id:
                raise workspace.WorkspaceError("forbidden", "Presentation belongs to another owner")
            path = f"{prefix}/views/{patch.id}.json"
            content = patch.model_dump_json().encode()
        else:
            meta = {**patch.meta, "league_id": request.league_id, "agent_id": request.owner_id}
            # Native IDs are retained; client IDs are display aliases only.
            previous = next(
                (
                    item
                    for item in records
                    if item.entity == patch.entity
                    and (item.id == patch.id or item.meta.get("ide_id") == patch.id)
                ),
                None,
            )
            if previous is None:
                native_id = store.create(patch.entity, {**meta, "ide_id": patch.id}, patch.body)
            else:
                native_id = previous.id
                store.update(native_id, meta, patch.body)
            native_path = store.path_of(native_id)
            path = native_path.relative_to(snapshot).as_posix()
            content = native_path.read_bytes()
        result = {"kind": "ok", "revision": revision + 1, "record": request.projection}
        receipt = RecordReceipt(fingerprint=fingerprint, result=result)
        files = {path: content, receipt_path: receipt.model_dump_json().encode()}
        if patch.entity != "presentation":
            files.update(
                {
                    item.relative_to(snapshot).as_posix(): item.read_bytes()
                    for item in write_generated(Layout(snapshot))
                }
            )
        workspace.publish(root, files, "Update research record", expected_head=head)
        return result
