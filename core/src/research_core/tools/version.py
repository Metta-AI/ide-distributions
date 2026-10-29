"""The ``version`` tool (design B.6, C.2, Q13): build, adopt, read and list Versions.

``create`` runs the mixin ``build`` command in the caller's working tree (a Researcher: the
worktree of the Task it holds; the Orchestrator: ``worktrees/orch-<n>`` when ``worktree`` is
given, else ``code/``), hashes the artifact the build produced and records the Version at the
tree's exact commit, tagged ``v/<n>`` in the code repository. The tree must be clean apart from
the artifact itself: a Version is a commit, not a working copy. The build runs detached with a
budget (A3.2): past it the tool answers ``background`` and the agent either waits for the
process or calls ``create`` again with ``resume``. A failed build reports the ``build``
capability (A4.1) and the refusal carries the log path and tail (B.15).

The exact artifact bytes are also kept under ``<coworld>/documents/artifacts/<digest>`` so the
upload tool sends what the build produced even after the tree moved on.

``adopt`` makes an adopted Version from a policy version uploaded outside the workspace.
``read`` and ``list`` carry the computed submission status (platform/submission.py).

Workspace executor: it needs PyYAML (the mixin front matter) and the platform.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from research_core import gitops, mixin
from research_core.platform import submission
from research_core.platform.errors import PlatformError
from research_core.platform.protocol import PolicyVersion, with_retries
from research_core.store import capabilities, sessions, tasks, versions
from research_core.store._common import parse_display_id
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
BOTH = frozenset({"orchestrator", "researcher"})

BUILD_BUDGET_S = 240
IMAGE_REFUSAL = "image policies are not supported yet: hosted sandboxes have no Docker"
RUNTIME_MARKER = ".softmax-runtime.json"

VERSION = a.string("The Version, such as V-7.")

# Tests replace this to skip the retry backoff; platform_call reads it on every call.
sleep: Callable[[float], None] = time.sleep


# --- shared by the upload and submit tools ------------------------------------------------


def platform_call[T](route: str, fn: Callable[[], T]) -> T:
    """Run a platform call with retries; an error that escapes is a refusal with its context."""
    try:
        return with_retries(fn, sleep=sleep)
    except PlatformError as error:
        raise platform_refusal(route, error) from error


def platform_refusal(route: str, error: PlatformError) -> ToolError:
    """The refusal for a platform error: its detail plus route, status and request id (B.15)."""
    context = error.to_dict()
    context.pop("error", None)
    context["route"] = error.route or route
    return ToolError([f"{route}: {error.detail}"], **context)


def intent_number(intent: dict[str, Any]) -> int:
    """The integer id behind an intent record's ``I-<n>``."""
    return parse_display_id(intent["id"])[1]


def with_status(ctx: ToolContext, record: dict[str, Any]) -> dict[str, Any]:
    """The Version record with the full submission-status ladder filled in."""
    record["submission_status"] = submission.status(ctx.store, ctx.platform(), record)
    return record


def version_number(value: Any) -> int:
    return a.record_number(value, "version", "version")


def policy_version_dict(found: PolicyVersion) -> dict[str, Any]:
    return {**asdict(found), "label": found.label}


def artifact_copy_path(ctx: ToolContext, digest: str) -> Path:
    """Where the exact built bytes of a Version live, by their sha256."""
    return ctx.coworld().documents / "artifacts" / digest


# --- create --------------------------------------------------------------------------------


def handle_create(call: ToolCall) -> dict:
    ctx = call.ctx
    coworld = ctx.coworld()
    tree, task_id = working_tree(ctx, call)
    loaded = load_mixin(tree, coworld.mixin)
    if loaded.artifact.get("kind") == "image":
        raise ToolError(IMAGE_REFUSAL, artifact=loaded.artifact)
    artifact_path = str(loaded.artifact["path"])
    dirty = dirty_files_excluding(tree, artifact_path)
    if dirty:
        raise ToolError(
            f"{tree_name(coworld, tree)} has uncommitted changes; commit them first"
            " (a Version is an exact commit)",
            dirty_files=dirty,
        )
    branch = gitops.current_branch(tree)
    if branch is None:
        raise ToolError(f"{tree_name(coworld, tree)} is on a detached HEAD; check out a branch")
    commit = gitops.rev_parse(tree, "HEAD")
    result = run_build(call, loaded, tree, coworld.documents / "logs")
    if not result.finished:
        return background_result(result)
    if result.exit_code != 0:
        raise build_failure(ctx, result, f"exit code {result.exit_code}")
    artifact_file = tree / artifact_path
    if not artifact_file.is_file():
        raise build_failure(ctx, result, f"no artifact at {artifact_path} after it")
    content = artifact_file.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    keep_artifact_copy(ctx, digest, content)
    record = versions.create(
        ctx.store,
        ctx.actor,
        summary=call.get("summary"),
        commit_sha=commit,
        branch=branch,
        artifact={"kind": "file", "path": artifact_path, "digest": digest, "size": len(content)},
        based_on=[f"V-{n}" for n in a.record_numbers(call.get("based_on"), "version", "based_on")],
        task_id=task_id,
        experiment_id=(
            a.record_number(call.get("experiment"), "experiment", "experiment")
            if call.get("experiment")
            else None
        ),
        image=runtime_image(ctx),
        policy_name=loaded.artifact.get("policy_name") or default_policy_name(coworld, branch),
    )
    gitops.tag(coworld.code, f"v/{parse_display_id(record['id'])[1]}", commit)
    return {
        "record": with_status(ctx, record),
        "build": {"log": str(result.log_path), "duration_s": round(result.duration_s, 3)},
    }


def working_tree(ctx: ToolContext, call: ToolCall) -> tuple[Path, int | None]:
    """The tree to build in and the Task it belongs to (A3.1, design B.4).

    A Researcher builds in the worktree of the Task it holds (``task``, else the Session's
    Task) and must be its lease holder. The Orchestrator builds in ``worktrees/orch-<n>`` when
    ``worktree`` names one, else in ``code/``.
    """
    coworld = ctx.coworld()
    task_arg = call.get("task")
    task_id = a.record_number(task_arg, "task", "task") if task_arg else None
    if ctx.role == "researcher":
        if task_id is None:
            task_id = session_task_id(ctx)
        if task_id is None:
            raise ToolError(
                "a Researcher builds in its Task's worktree; this Session holds no Task"
                " (give task, or claim one first)"
            )
        tasks.require_holder(ctx.store, task_id, ctx.actor)
        worktree = tasks.read(ctx.store, task_id)["worktree"]
        if not worktree:
            raise ToolError(f"T-{task_id} records no worktree; claim it to get one")
        path = Path(worktree)
        return (path if path.is_absolute() else coworld.root / path), task_id
    name = call.get("worktree")
    if not name:
        return coworld.code, task_id
    path = coworld.worktrees / orch_worktree_name(str(name))
    if not path.is_dir():
        existing = sorted(p.name for p in coworld.worktrees.glob("orch-*") if p.is_dir())
        raise ToolError(
            f"no worktree {path.name}; open one with workspace.worktree_open"
            + (f" (existing: {', '.join(existing)})" if existing else ""),
        )
    return path, task_id


def session_task_id(ctx: ToolContext) -> int | None:
    session = sessions.read(ctx.wstore, ctx.hermes_session_id())
    if session is None or not session.get("task_id"):
        return None
    return int(session["task_id"])


def orch_worktree_name(name: str) -> str:
    """``orch-3``, ``3`` or a path ending in ``orch-3`` all name worktree ``orch-3``."""
    base = Path(name).name
    return base if base.startswith("orch-") else f"orch-{base}"


def tree_name(coworld, tree: Path) -> str:
    try:
        return str(tree.relative_to(coworld.root))
    except ValueError:
        return tree.name


def load_mixin(tree: Path, fallback: Path) -> mixin.Mixin:
    try:
        return submission.load_mixin_for(tree, fallback)
    except mixin.MixinError as error:
        raise ToolError(error.problems, mixin=str(tree / "mixin")) from error


def dirty_files_excluding(tree: Path, artifact_path: str) -> list[str]:
    """Uncommitted paths, ignoring the artifact the build writes (and its untracked folder)."""
    artifact = artifact_path.strip("/")
    kept: list[str] = []
    for entry in gitops.dirty_files(tree):
        clean = entry.rstrip("/")
        if clean == artifact or artifact.startswith(clean + "/"):
            continue
        kept.append(entry)
    return kept


def run_build(
    call: ToolCall, loaded: mixin.Mixin, tree: Path, log_dir: Path
) -> mixin.CommandResult:
    token = call.get("resume")
    if token:
        try:
            return mixin.resume(token)
        except mixin.MixinError as error:
            raise ToolError(error.problems) from error
    return mixin.run_command(loaded, "build", cwd=tree, log_dir=log_dir, budget_s=BUILD_BUDGET_S)


def background_result(result: mixin.CommandResult) -> dict:
    return {
        "ok": True,
        "background": {
            "pid": result.pid,
            "log": str(result.log_path),
            "resume": result.resume_token,
        },
        "output_tail": result.output_tail,
        "next": (
            "the build is still running: call version.create again with the same arguments"
            " plus resume=<background.resume> when it ends, or task.wait {kind: process, ref:"
            f" {result.pid}}} and end this run"
        ),
    }


def build_failure(ctx: ToolContext, result: mixin.CommandResult, what: str) -> ToolError:
    """Mark ``build`` broken with its repair Task (A4.1) and explain with the log (B.15)."""
    report = capabilities.report_failure(
        ctx.store,
        ctx.actor,
        "build",
        output=log_text(result.log_path),
        command=result.command,
    )
    task = report.get("maintenance_task")
    return ToolError(
        [f"the build ended with {what}; full output in {result.log_path}"],
        command=result.command,
        exit_code=result.exit_code,
        log=str(result.log_path),
        output_tail=result.output_tail,
        capability="build",
        maintenance_task=None if task is None else task["id"],
    )


def log_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def keep_artifact_copy(ctx: ToolContext, digest: str, content: bytes) -> Path:
    path = artifact_copy_path(ctx, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    return path


def runtime_image(ctx: ToolContext) -> str | None:
    """The hosted runtime image recorded by metta in ``<HERMES_HOME>/.softmax-runtime.json``."""
    home = ctx.env.get("HERMES_HOME")
    if not home:
        return None
    marker = Path(home).expanduser() / RUNTIME_MARKER
    try:
        image = json.loads(marker.read_text(encoding="utf-8")).get("image")
    except (OSError, ValueError, AttributeError):
        return None
    return image if isinstance(image, str) and image else None


def default_policy_name(coworld, branch: str) -> str:
    return f"{coworld.coworld_id}-{branch.replace('/', '-')}"


# --- adopt, read, list ---------------------------------------------------------------------


def handle_adopt(call: ToolCall) -> dict:
    ctx = call.ctx
    policy_version_id, label = call.get("policy_version"), call.get("label")
    if not policy_version_id and not label:
        raise ToolError("give policy_version (the platform's id) or label (name:vN)")
    platform = ctx.platform()
    if label:
        name, _, number = str(label).rpartition(":v")
        if not name or not number.isdigit():
            raise ToolError(f"label must look like name:v3, not {label!r}")
        candidates = platform_call(
            "GET /v2/policy-versions", lambda: platform.policy_versions(name=name)
        )
        found = [p for p in candidates if p.version == int(number)]
    else:
        candidates = platform_call("GET /v2/policy-versions", lambda: platform.policy_versions())
        found = [p for p in candidates if p.id == policy_version_id]
    if not found:
        known = ", ".join(f"{p.label} ({p.id})" for p in candidates) or "none"
        raise ToolError(
            f"no policy version {label or policy_version_id} among this Player's uploads",
            known=known,
        )
    chosen = found[0]
    record = versions.adopt(
        ctx.store,
        ctx.actor,
        summary=call.get("summary"),
        policy_version_id=chosen.id,
        policy_name=chosen.name,
        uploaded_at=chosen.created_at or ctx.store.now(),
        digest=chosen.content_hash,
    )
    return {"record": with_status(ctx, record), "policy_version": policy_version_dict(chosen)}


def handle_read(call: ToolCall) -> dict:
    ctx = call.ctx
    record = with_status(ctx, versions.read(ctx.store, version_number(call.get("version"))))
    result: dict[str, Any] = {"record": record}
    policy_version_id = (record.get("uploaded") or {}).get("policy_version_id")
    if policy_version_id:
        try:
            states = submission.platform_states(ctx.platform(), policy_version_id)
        except PlatformError as error:
            result["platform"] = {"unavailable": error.detail}
        else:
            result["platform"] = submission.states_as_dicts(states)
    return result


def handle_list(call: ToolCall) -> dict:
    ctx = call.ctx
    limit = call.get("limit") or 20
    records = [with_status(ctx, r) for r in versions.list(ctx.store, limit=limit)]
    return {"records": records}


def dispatch(call: ToolCall) -> dict:
    if call.action == "create":
        return handle_create(call)
    if call.action == "adopt":
        return handle_adopt(call)
    if call.action == "read":
        return handle_read(call)
    if call.action == "list":
        return handle_list(call)
    raise AssertionError(call.action)


SPEC = tool(
    ToolSpec(
        name="version",
        description=(
            "Versions: policy candidates. create builds the policy at the current commit of"
            " your working tree and records the artifact; adopt records a policy version"
            " uploaded outside the workspace. Each Version shows its submission status"
            " (built, uploaded, submitted, qualifying, competing, champion). Returns the record."
        ),
        handler=dispatch,
        roles=BOTH,
        executor="workspace",
        actions=(
            ActionSpec(
                "create",
                "Build a Version from the committed state of your working tree (a Researcher:"
                " its Task's worktree; the Orchestrator: worktree orch-<n>, else code/). The"
                " tree must be clean. A long build returns background with a resume token.",
                {
                    "summary": a.string("What this Version changes, in one sentence."),
                    "based_on": a.strings("Versions it was built from, such as V-3."),
                    "task": a.string(
                        "The Task this Version belongs to (a Researcher's held Task)."
                    ),
                    "experiment": a.string("The Experiment it is a candidate for, such as X-2."),
                    "worktree": a.string(
                        "Orchestrator only: the worktree to build in, such as orch-2; omit"
                        " for code/."
                    ),
                    "resume": a.string(
                        "The resume token from an earlier background answer, once the build ended."
                    ),
                },
                ("summary",),
            ),
            ActionSpec(
                "adopt",
                "Record a policy version the user uploaded elsewhere as an adopted Version"
                " (already uploaded; submit accepts it).",
                {
                    "policy_version": a.string("The platform's policy version id."),
                    "label": a.string("Or its label, name:vN."),
                    "summary": a.string("What this policy is, in one sentence."),
                },
                ("summary",),
            ),
            ActionSpec(
                "read",
                "Read a Version with its submission status and the platform's view of it.",
                {"version": VERSION},
                ("version",),
            ),
            ActionSpec(
                "list",
                "List the newest Versions with their submission status.",
                {"limit": a.integer("At most this many (default 20).")},
            ),
        ),
    )
)
