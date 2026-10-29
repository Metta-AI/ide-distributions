"""The ``upload`` tool (design B.6, B.13): send a Version's exact artifact bytes once.

Upload takes any built Version (an adopted one is already uploaded: a no-op). Before the first
send the tool computes the exact inputs the platform deduplicates an upload on (coworld's
``policy_upload_inputs``: name, player, file hash, file size, attributes) and commits an
``upload`` intent keyed by the artifact's sha256 with those inputs saved on it. The bytes are
sent once per call and the write is never retried: a lost answer leaves the intent ``pending``,
a platform error finishes it ``failed``, and either way the next call reads the platform first.

Recovery of an existing intent is the read-only lookup ``POST /stats/policies/lookup`` with the
saved inputs, as the Player; it needs no local artifact, so it works after the copy is gone:

- a match is adopted as the upload (nothing is sent);
- the platform's definite "no such version" (HTTP 200, null) lets the tool send again on its
  own once per intent, recorded as a new attempt with ``{"lookup": "not_found"}`` on the earlier
  one; a second absence, or a ``done`` intent the platform no longer shows, stays unresolved;
- a lookup that errors, is refused or is unavailable stays unresolved: the tool answers
  ``ok: false`` naming the intent and the saved inputs, writes a notice for the user and sends
  nothing.

``user_requested: true`` (the user asked for this upload) always sends again. The platform's
returned hash, when it has one, is checked against the digest; a mismatch finishes the intent
``failed``. ``versions.mark_uploaded`` emits ``version.uploaded``; a wake Session also leaves a
notice (A2.4: the only wake channel).

Workspace executor: it needs the platform client.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from research_core.platform import submission
from research_core.platform.errors import PlatformError
from research_core.platform.protocol import PolicyVersion, with_retries
from research_core.store import intents, notices, sessions, versions
from research_core.tools import _args as a
from research_core.tools import version as version_tool
from research_core.tools.base import ToolCall, ToolContext, ToolError, ToolSpec, tool

UPLOAD_ROUTE = "POST /v2/policy-versions/upload"
# Recorded on the earlier attempt when the tool sends again after the lookup found nothing;
# one such attempt per intent happens without a user request.
NOT_FOUND = {"lookup": "not_found"}


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
    if not record.get("policy_name"):
        raise ToolError(f"{vid} records no policy name to upload under")
    intent = intents.find(ctx.store, kind="upload", key=digest)
    if intent is None:
        content = read_artifact(ctx, vid, artifact)
        request = upload_inputs(ctx, record)
        intent = intents.begin(
            ctx.store, ctx.actor, kind="upload", key=digest, subject=vid, request=request
        )
        return send(ctx, record, intent, content)
    if call.get("user_requested"):
        return send_again(ctx, record, intent, {"user_requested": True})
    return recover(ctx, record, intent)


def recover(ctx: ToolContext, record: dict[str, Any], intent: dict[str, Any]) -> dict:
    """An existing intent: ask the platform what happened, with the saved inputs; never send
    blindly."""
    vid = record["id"]
    request = intent.get("request")
    if not request:
        raise ToolError(
            f"{intent['id']} has no saved upload inputs to look up; call upload with"
            f" user_requested=true to send {vid} again"
        )
    platform = ctx.platform()
    try:
        found = with_retries(
            lambda: platform.lookup_policy_upload(request), sleep=version_tool.sleep
        )
    except PlatformError as error:
        raise unresolved(
            ctx, record, intent, f"the lookup failed ({error.detail})", lookup=error.to_dict()
        ) from error
    if found is not None:
        return adopt(ctx, record, intent, found)
    if intent["state"] == "done":
        raise unresolved(
            ctx,
            record,
            intent,
            "the intent says the platform accepted the bytes, but the lookup shows no such"
            " policy version now",
        )
    if retried_automatically(intent):
        raise unresolved(
            ctx,
            record,
            intent,
            "the lookup shows no such policy version, and the one automatic retry of this"
            " intent is used",
        )
    return send_again(ctx, record, intent, NOT_FOUND)


def retried_automatically(intent: dict[str, Any]) -> bool:
    """Whether an earlier attempt of this intent was already followed by a send on absence."""
    failures = (intent.get("result") or {}).get(intents.FAILURES_KEY, ())
    return any(entry.get("lookup") == "not_found" for entry in failures)


def adopt(
    ctx: ToolContext, record: dict[str, Any], intent: dict[str, Any], found: PolicyVersion
) -> dict:
    """The platform has the bytes: record the upload without sending."""
    updated = mark(ctx, record, found, digest_checked=True)
    if intent["state"] == "failed":
        # The attempt that came back failed reached the platform after all.
        intent = intents.reopen(
            ctx.store, ctx.actor, version_tool.intent_number(intent), reason={"lookup": "found"}
        )
    if intent["state"] == "pending":
        finish(ctx, intent, "done", {**policy_dict(found), "reconciled": True})
    notify_wake(ctx, updated, found)
    return {"record": updated, "policy_version": policy_dict(found), "reconciled": True}


def send_again(
    ctx: ToolContext, record: dict[str, Any], intent: dict[str, Any], reason: dict[str, Any]
) -> dict:
    """A new attempt under the same intent; the bytes are read first, so a gone artifact opens
    none."""
    content = read_artifact(ctx, record["id"], record["artifact"])
    reopened = intents.reopen(
        ctx.store, ctx.actor, version_tool.intent_number(intent), reason=reason
    )
    return send(ctx, record, reopened, content)


def send(ctx: ToolContext, record: dict[str, Any], intent: dict[str, Any], content: bytes) -> dict:
    """One send of the bytes; the write is not retried, the next call asks the platform first."""
    vid = record["id"]
    digest = record["artifact"]["digest"]
    tags = {"version": vid, "commit": record.get("commit_sha") or ""}
    platform = ctx.platform()
    try:
        uploaded = platform.upload_file_policy(
            name=record["policy_name"], content=content, tags=tags
        )
    except PlatformError as error:
        finish(ctx, intent, "failed", error.to_dict())
        raise version_tool.platform_refusal(UPLOAD_ROUTE, error) from error
    checked = uploaded.content_hash is not None
    if checked and uploaded.content_hash != digest:
        finish(ctx, intent, "failed", {"platform_hash": uploaded.content_hash, "digest": digest})
        raise ToolError(
            f"the platform's hash of the uploaded bytes ({uploaded.content_hash}) is not the"
            f" artifact digest ({digest}); {vid} is not marked uploaded",
            policy_version=policy_dict(uploaded),
        )
    updated = mark(ctx, record, uploaded, digest_checked=checked)
    finish(ctx, intent, "done", policy_dict(uploaded))
    notify_wake(ctx, updated, uploaded)
    return {
        "record": updated,
        "policy_version": policy_dict(uploaded),
        "reconciled": False,
        "attempt": intent["attempt"],
    }


def unresolved(
    ctx: ToolContext, record: dict[str, Any], intent: dict[str, Any], why: str, **context: Any
) -> ToolError:
    """Nothing was sent and the platform settled nothing: tell the agent and the user."""
    vid = record["id"]
    notice = (
        f"{vid} upload is unresolved ({intent['id']}): {why}. Nothing was sent; ask for the"
        " upload again or wait for the platform."
    )
    if all(existing["text"] != notice for existing in notices.unread(ctx.wstore)):
        notices.add(ctx.wstore, ctx.actor, text=notice, refs=[vid], urgency="high")
    inputs = json.dumps(intent.get("request"), sort_keys=True)
    return ToolError(
        f"{vid} upload is unresolved: {why}. Nothing was sent. The saved upload inputs of"
        f" {intent['id']} are {inputs}; call upload again to look them up again, or with"
        " user_requested=true to send the bytes again on the user's request.",
        intent=intent["id"],
        upload_inputs=intent.get("request"),
        unresolved=True,
        **context,
    )


def upload_inputs(ctx: ToolContext, record: dict[str, Any]) -> dict[str, Any]:
    """The platform client's exact description of this upload, from the kept artifact copy."""
    digest = record["artifact"]["digest"]
    path = version_tool.artifact_copy_path(ctx, digest)
    try:
        inputs = ctx.platform().upload_inputs(name=record["policy_name"], artifact=path)
    except PlatformError as error:
        raise version_tool.platform_refusal("upload inputs", error) from error
    if inputs.get("file_hash") != digest:
        # The intent key is the digest; the lookup must ask for the same bytes.
        raise ToolError(
            f"the upload client hashes the artifact of {record['id']} as"
            f" {inputs.get('file_hash')}, not its digest {digest}; nothing was sent"
        )
    return inputs


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
            " once; the platform's hash is checked against the artifact digest. An earlier"
            " attempt whose answer was lost is looked up, never sent again blindly; an"
            " unresolved one needs user_requested=true. An adopted or already uploaded Version"
            " is a no-op. Returns the Version with its status."
        ),
        handler=handle,
        roles=version_tool.BOTH,
        executor="workspace",
        params={
            "version": version_tool.VERSION,
            "user_requested": a.boolean(
                "true when the user asked for this upload: the bytes are sent again even when"
                " an earlier attempt is unresolved. false (default) for your own initiative."
            ),
        },
        required=("version",),
    )
)
