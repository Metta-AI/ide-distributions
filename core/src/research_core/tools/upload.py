"""The ``upload`` tool (design B.6, B.13): send a Version's exact artifact bytes once.

Upload takes any built Version (an adopted one is already uploaded: a no-op). Before the call
an ``upload`` intent keyed by the artifact's sha256 is committed; an intent already ``pending``
or ``done`` (a crash after the platform accepted the bytes) is reconciled first: the Player's
policy versions under the Version's policy name are read and one whose ``content_hash`` is the
digest is adopted as the upload without sending again. The bytes come from the copy
``version.create`` kept (``documents/artifacts/<digest>``), checked against the digest before
they leave; the platform's returned hash is checked against it after. A mismatch finishes the
intent ``failed`` and the refusal shows both hashes. ``versions.mark_uploaded`` emits
``version.uploaded``; a wake Session also leaves a notice (A2.4: the only wake channel).

Workspace executor: it needs the platform client.
"""

from __future__ import annotations

import hashlib
from typing import Any

from research_core.platform import submission
from research_core.platform.errors import PlatformError
from research_core.platform.protocol import PolicyVersion, with_retries
from research_core.store import intents, notices, sessions, versions
from research_core.tools import version as version_tool
from research_core.tools.base import ToolCall, ToolContext, ToolError, ToolSpec, tool

UPLOAD_ROUTE = "POST /v2/policy-versions/upload"


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    record = versions.read(ctx.store, version_tool.version_number(call.get("version")))
    vid = record["id"]
    if record.get("uploaded"):
        return {"record": version_tool.with_status(ctx, record), "already_uploaded": True}
    artifact = record.get("artifact") or {}
    digest = artifact.get("digest")
    if not digest:
        raise ToolError(f"{vid} has no artifact to upload; version.create builds one")
    policy_name = record.get("policy_name")
    if not policy_name:
        raise ToolError(f"{vid} records no policy name to upload under")
    intent = intents.begin(ctx.store, ctx.actor, kind="upload", key=digest, subject=vid)
    if intent["existing"]:
        found = reconcile(ctx, policy_name, digest)
        if found is not None:
            updated = mark(ctx, record, found, digest_checked=True)
            if intent["state"] == "pending":
                finish(ctx, intent, "done", {**policy_dict(found), "reconciled": True})
            notify_wake(ctx, updated, found)
            return {"record": updated, "policy_version": policy_dict(found), "reconciled": True}
        if intent["state"] == "done":
            # The intent says the bytes went up but the platform has no such policy version:
            # upload again; the existing row already records the action for these bytes.
            intent = None
    content = read_artifact(ctx, vid, artifact)
    tags = {"version": vid, "commit": record.get("commit_sha") or ""}
    platform = ctx.platform()
    try:
        uploaded = with_retries(
            lambda: platform.upload_file_policy(name=policy_name, content=content, tags=tags),
            sleep=version_tool.sleep,
        )
    except PlatformError as error:
        if intent is not None:
            finish(ctx, intent, "failed", error.to_dict())
        raise version_tool.platform_refusal(UPLOAD_ROUTE, error) from error
    checked = uploaded.content_hash is not None
    if checked and uploaded.content_hash != digest:
        if intent is not None:
            finish(
                ctx, intent, "failed", {"platform_hash": uploaded.content_hash, "digest": digest}
            )
        raise ToolError(
            f"the platform's hash of the uploaded bytes ({uploaded.content_hash}) is not the"
            f" artifact digest ({digest}); {vid} is not marked uploaded",
            policy_version=policy_dict(uploaded),
        )
    updated = mark(ctx, record, uploaded, digest_checked=checked)
    if intent is not None:
        finish(ctx, intent, "done", policy_dict(uploaded))
    notify_wake(ctx, updated, uploaded)
    return {"record": updated, "policy_version": policy_dict(uploaded), "reconciled": False}


def reconcile(ctx: ToolContext, policy_name: str, digest: str) -> PolicyVersion | None:
    """The Player's policy version with these bytes, if the platform already has them."""
    platform = ctx.platform()
    found = version_tool.platform_call(
        "GET /v2/policy-versions", lambda: platform.policy_versions(name=policy_name)
    )
    for candidate in found:
        if candidate.content_hash == digest:
            return candidate
    return None


def read_artifact(ctx: ToolContext, vid: str, artifact: dict[str, Any]) -> bytes:
    """The exact built bytes, verified against the digest before they leave."""
    digest = artifact["digest"]
    path = version_tool.artifact_copy_path(ctx, digest)
    try:
        content = path.read_bytes()
    except OSError:
        raise ToolError(
            f"the built artifact of {vid} is gone ({artifact.get('path')}, sha256 {digest});"
            " build it again with version.create",
        ) from None
    actual = hashlib.sha256(content).hexdigest()
    if actual != digest:
        raise ToolError(
            f"the kept artifact of {vid} no longer matches its digest (now {actual}, recorded"
            f" {digest}); build it again with version.create"
        )
    return content


def mark(
    ctx: ToolContext, record: dict[str, Any], found: PolicyVersion, *, digest_checked: bool
) -> dict[str, Any]:
    other = versions.find_by_policy_version(ctx.store, found.id)
    if other is not None and other["id"] != record["id"]:
        # Identical bytes are one policy version on the platform (and one intent key here):
        # the Version that already carries it is the one to submit.
        raise ToolError(
            f"the exact bytes of {record['id']} are already uploaded as {other['id']} (policy"
            f" version {found.id}); a policy version belongs to one Version, so submit"
            f" {other['id']}",
            policy_version=version_tool.policy_version_dict(found),
            uploaded_as=other["id"],
        )
    updated = versions.mark_uploaded(
        ctx.store,
        ctx.actor,
        record["id"],
        policy_version_id=found.id,
        at=found.created_at or ctx.store.now(),
        digest_checked=digest_checked,
    )
    submission.invalidate(found.id)
    return version_tool.with_status(ctx, updated)


def finish(ctx: ToolContext, intent: dict[str, Any], state: str, result: dict[str, Any]) -> None:
    intents.finish(
        ctx.store, ctx.actor, version_tool.intent_number(intent), state=state, result=result
    )


def notify_wake(ctx: ToolContext, record: dict[str, Any], found: PolicyVersion) -> None:
    """A wake Session has no conversation: it reports the upload through a notice."""
    session = sessions.read(ctx.wstore, ctx.hermes_session_id())
    if session is None or session["kind"] != "wake":
        return
    notices.add(
        ctx.wstore,
        ctx.actor,
        text=f"Uploaded {record['id']} as {found.label}",
        refs=[record["id"]],
    )


policy_dict = version_tool.policy_version_dict


SPEC = tool(
    ToolSpec(
        name="upload",
        description=(
            "Upload a Version's exact built artifact to the platform as a policy version,"
            " once; the platform's hash is checked against the artifact digest. An adopted or"
            " already uploaded Version is a no-op. Returns the Version with its status."
        ),
        handler=handle,
        roles=version_tool.BOTH,
        executor="workspace",
        params={"version": version_tool.VERSION},
        required=("version",),
    )
)
