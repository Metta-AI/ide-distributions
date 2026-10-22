"""Revisioned UI presentation in canonical workspace files; Git owns publication."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ide_research_core import workspace
from ide_research_core.io import gitops


class PresentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "save"]
    league_id: str = Field(pattern=r"^league_[a-f0-9-]{36}$")
    key: str = Field(
        pattern=r"^(layout|draft|(page|tabs|thread|agent|tabhtml):[A-Za-z0-9_.:-]+)$",
        max_length=240,
    )
    revision: int = Field(default=0, ge=0)
    document: JsonValue = None
    request_id: str = Field(default="", max_length=200)


class PresentationDocument(BaseModel):
    revision: int = 0
    document: JsonValue = None
    updated_at: str | None = None


class PresentationReceipt(BaseModel):
    fingerprint: str
    result: PresentationDocument


def execute(root: Path, request: PresentationRequest) -> dict:
    revision = workspace.sync(root)
    prefix = f"control/ide/{request.league_id}"
    path = f"{prefix}/documents/{request.key}.json"
    raw = gitops.show(root, revision, path)
    current = (
        PresentationDocument.model_validate_json(raw)
        if raw is not None
        else PresentationDocument()
    )
    if request.action == "read":
        return current.model_dump()
    if not request.request_id:
        raise workspace.WorkspaceError(
            "bad_arguments", "A presentation save requires its original request ID"
        )
    payload = json.dumps(
        request.model_dump(exclude={"request_id"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    fingerprint = hashlib.sha256(payload).hexdigest()
    receipt_path = f"{prefix}/requests/{hashlib.sha256(request.request_id.encode()).hexdigest()}.json"
    saved = gitops.show(root, revision, receipt_path)
    if saved is not None:
        receipt = PresentationReceipt.model_validate_json(saved)
        if receipt.fingerprint != fingerprint:
            raise workspace.WorkspaceError(
                "request_conflict",
                "This request already names another presentation save",
            )
        return receipt.result.model_dump()
    if request.revision != current.revision:
        raise workspace.WorkspaceError(
            "conflict", "Presentation changed; read its current revision"
        )
    result = PresentationDocument(
        revision=current.revision + 1,
        document=request.document,
        updated_at=datetime.now(UTC).isoformat(),
    )
    receipt = PresentationReceipt(fingerprint=fingerprint, result=result)
    workspace.publish(
        root,
        {
            path: result.model_dump_json().encode(),
            receipt_path: receipt.model_dump_json().encode(),
        },
        "Update IDE presentation",
        expected_head=revision,
    )
    return result.model_dump()
