"""The ``attachment_download`` tool (Orchestrator): fetch a conversation attachment by SHA-256.

Clients attach files to a conversation and the context block names each by ``sha256``, size
and url (design A.2 ``attachments[]``). The tool asks the platform for the artifact
(``GET /v2/cogents/<player>/artifacts/<sha256>`` through ``platform.request``; the route the
cogents API serves until its replacement exists, design A.5), takes the bytes inline
(``content_base64``) or from the ``url`` it names (fetched with no Authorization header), checks
the size and the hash, and stores the file at ``<coworld>/documents/attachments/<sha256>``.
A size or hash mismatch is refused and nothing is written. A failing route or download reports
the ``attachments`` capability (A4.1). Runs in the workspace environment.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from research_core.platform.protocol import Platform
from research_core.platform.wait import call_platform
from research_core.tools import _args as a
from research_core.tools.base import ToolCall, ToolContext, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
CAPABILITY = "attachments"
MAX_BYTES = 25 * 1024 * 1024
USER_AGENT = "softmax-ide-research"
DOWNLOAD_TIMEOUT_S = 60
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    sha256 = str(call.get("sha256") or "").strip().lower()
    if not _SHA256.match(sha256):
        raise ToolError("sha256 must be the attachment's 64-character hex SHA-256")
    target = ctx.coworld().documents / "attachments" / sha256
    if target.is_file() and _digest(target.read_bytes()) == sha256:
        return {
            "path": str(target),
            "sha256": sha256,
            "size_bytes": target.stat().st_size,
            "existing": True,
        }
    platform = ctx.platform()
    player = _player_id(ctx, platform)
    path = f"/v2/cogents/{player}/artifacts/{sha256}"
    route = f"GET {path}"
    try:
        artifact = call_platform(ctx, route, lambda: platform.request("GET", path))
    except ToolError as error:
        raise _failure(
            ctx, error.problems, command=route, output=str(error), **error.context
        ) from error
    content = _content(ctx, route, artifact, sha256)
    size = _size(artifact)
    if len(content) != size or _digest(content) != sha256:
        raise ToolError(
            "attachment size or SHA-256 verification failed; nothing was written",
            expected={"sha256": sha256, "size_bytes": size},
            got={"sha256": _digest(content), "size_bytes": len(content)},
        )
    _store(target, content)
    return {"path": str(target), "sha256": sha256, "size_bytes": len(content), "existing": False}


def _content(ctx: ToolContext, route: str, artifact: object, sha256: str) -> bytes:
    """The bytes an artifact answer carries inline or at its url."""
    if not isinstance(artifact, dict):
        raise _failure(
            ctx, "the artifact route did not answer an object", command=route, output=repr(artifact)
        )
    if artifact.get("sha256") != sha256:
        raise ToolError(
            "the platform returned a different attachment",
            expected=sha256,
            got=artifact.get("sha256"),
        )
    size = _size(artifact)
    inline = artifact.get("content_base64")
    if isinstance(inline, str):
        try:
            return base64.b64decode(inline, validate=True)
        except binascii.Error as error:
            raise ToolError(f"the inline attachment is not valid base64: {error}") from error
    url = artifact.get("url")
    if not isinstance(url, str) or not url:
        raise ToolError("the artifact answer has neither content_base64 nor url", artifact=artifact)
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK):
        raise ToolError(f"attachment downloads require https, not {url}")
    # This request never carries the workspace's Authorization header: the url is signed.
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
            return response.read(size + 1)
    except (URLError, TimeoutError) as error:
        detail = f"{type(error).__name__}: {error}"
        raise _failure(
            ctx, f"downloading the attachment failed: {detail}", command=f"GET {url}", output=detail
        ) from error


def _size(artifact: dict) -> int:
    size = artifact.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_BYTES:
        raise ToolError(f"the attachment's size_bytes must be 0..{MAX_BYTES}, not {size!r}")
    return size


def _player_id(ctx: ToolContext, platform: Platform) -> str:
    player = ctx.env.get("SOFTMAX_PLAYER_ID")
    if player:
        return player
    who = call_platform(ctx, "GET /whoami", lambda: platform.whoami("player"))
    if not who.subject_id:
        raise ToolError("the platform did not name this Player (GET /whoami)", route="GET /whoami")
    return who.subject_id


def _store(target: Path, content: bytes) -> None:
    """Write privately and replace only after verification (the caller verified)."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TemporaryDirectory(dir=target.parent) as temporary:
        staged = Path(temporary) / target.name
        staged.write_bytes(content)
        os.chmod(staged, 0o600)
        staged.replace(target)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _failure(ctx: ToolContext, problems, *, command: str, output: str, **context) -> ToolError:
    from research_core.store import capabilities

    report = capabilities.report_failure(
        ctx.store, ctx.actor, CAPABILITY, output=output, command=command
    )
    task = report.get("maintenance_task")
    context.update(capability=CAPABILITY, maintenance_task=None if task is None else task["id"])
    return ToolError(problems, **context)


SPEC = tool(
    ToolSpec(
        name="attachment_download",
        description=(
            "Fetch a file the user attached to the conversation by its sha256 (from the context "
            "block's attachments), verify size and hash, and store it under the Coworld's "
            "documents/attachments/. Returns the path."
        ),
        handler=handle,
        roles=ORCHESTRATOR,
        executor="workspace",
        params={"sha256": a.string("The attachment's SHA-256 (64 hex characters).")},
        required=("sha256",),
    )
)
