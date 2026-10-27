"""Bounded committed report reads from a completed card's recorded workspace."""

import hashlib
import subprocess
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .board import BOARD, Board, connection


class CompletedCardRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=256)
    run_id: int = Field(ge=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class ReportRead(CompletedCardRead):
    path: str = Field(min_length=1, max_length=512)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=6000)


class ArtifactVerify(CompletedCardRead):
    artifacts: dict[
        Annotated[str, Field(min_length=1, max_length=512)],
        Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
    ] = Field(min_length=1, max_length=64, description="Committed relative paths mapped to expected SHA-256 hashes")


class ArtifactDigest(BaseModel):
    path: str
    blob: str
    bytes: int
    sha256: str
    matches: bool


class Reports:
    def __init__(self, context):
        self.context = context

    def _workspace(self, request: CompletedCardRead) -> Path:
        snapshot = Board(self.context).card_show(request.task_id)
        task, runs = snapshot["card"], snapshot["runs"]
        latest = max(runs, key=lambda item: item["id"], default=None)
        if (not latest or latest["id"] != request.run_id or latest["task_id"] != request.task_id
                or latest["status"] != "done" or not latest.get("ended_at") or task["status"] != "done"):
            raise ValueError("Read a report only from the card's latest completed run; refresh card_show")
        workspace = Path(task.get("workspace_path") or "").resolve()
        if not task.get("workspace_path") or not workspace.is_relative_to(self.context.root.resolve()):
            raise ValueError("The card workspace is not on this profile host; request a report handoff from the worker")
        branch = task.get("branch_name")
        if not branch:
            raise ValueError("The card workspace is unavailable; request a report handoff from the worker")
        if not workspace.is_dir() and task["workspace_kind"] == "worktree":
            from hermes_cli import kanban_db as kb
            from hermes_cli.kanban_db_workspace import resolve_workspace

            # Native completion removes clean worktrees but retains the card branch.
            # Restore through the same lifecycle API used for completed-build uploads.
            with closing(connection()) as conn:
                completed = kb.get_task(conn, request.task_id)
            restored = resolve_workspace(completed, board=BOARD).resolve()
            if restored != workspace:
                raise ValueError("The restored workspace differs from the card's recorded workspace")
        if not workspace.is_dir():
            raise ValueError("The card workspace is unavailable; request a report handoff from the worker")

        self._git(workspace, "check-ref-format", "refs/heads/" + branch)
        self._git(workspace, "merge-base", "--is-ancestor", request.commit, "refs/heads/" + branch)
        return workspace

    @staticmethod
    def _git(workspace: Path, *args: str) -> bytes:
        result = subprocess.run(
            ["git", "--no-replace-objects", "--literal-pathspecs", "-C", str(workspace), *args],
            capture_output=True, timeout=30, check=False,
        )
        if result.returncode:
            raise ValueError("The requested commit/report is not available on the recorded card branch")
        return result.stdout

    def _blob(self, workspace: Path, commit: str, path: str) -> tuple[str, int]:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\0" in path or str(relative) != path:
            raise ValueError("Use the committed artifact's normalized relative path")
        entry = self._git(workspace, "ls-tree", "--format=%(objectmode) %(objecttype) %(objectname)", commit, "--", path).decode().strip().split()
        if len(entry) != 3 or entry[0] not in ("100644", "100755") or entry[1] != "blob":
            raise ValueError("Choose a committed regular report file; directories and symlinks are not supported")
        blob = entry[2]
        return blob, int(self._git(workspace, "cat-file", "-s", blob))

    def card_report(self, task_id, run_id, commit, path, offset=0, limit=4000):
        request = ReportRead(task_id=task_id, run_id=run_id, commit=commit, path=path, offset=offset, limit=limit)
        workspace = self._workspace(request)
        blob, size = self._blob(workspace, request.commit, request.path)
        if size > 256_000:
            raise ValueError("Report exceeds the 256,000-byte read limit; ask the worker for a bounded summary")
        try:
            text = self._git(workspace, "cat-file", "blob", blob).decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Report is not UTF-8 text") from None
        if "\0" in text:
            raise ValueError("Report is binary")
        if request.offset > len(text):
            raise ValueError("Offset exceeds the report's character count")
        end = min(request.offset + request.limit, len(text))
        return {
            "task_id": request.task_id, "run_id": request.run_id, "commit": request.commit,
            "path": request.path, "blob": blob, "content": text[request.offset:end],
            "characters": len(text), "offset": request.offset, "next_offset": end if end < len(text) else None,
            "offset_unit": "unicode_characters",
            "provenance": "commit reachable from the recorded branch of this card's latest completed run; not an integration receipt",
        }

    def card_verify_artifacts(self, task_id, run_id, commit, artifacts):
        request = ArtifactVerify(task_id=task_id, run_id=run_id, commit=commit, artifacts=artifacts)
        workspace = self._workspace(request)
        entries = [(path, *self._blob(workspace, request.commit, path)) for path in request.artifacts]
        if sum(size for _, _, size in entries) > 256 * 1024 * 1024:
            raise ValueError("Artifact batch exceeds 256 MiB; verify smaller batches")
        digests = []
        for path, blob, size in entries:
            digest = hashlib.sha256(self._git(workspace, "cat-file", "blob", blob)).hexdigest()
            digests.append(ArtifactDigest(
                path=path, blob=blob, bytes=size, sha256=digest, matches=digest == request.artifacts[path],
            ))
        return {
            "task_id": request.task_id, "run_id": request.run_id, "commit": request.commit,
            "status": "verified" if all(item.matches for item in digests) else "mismatch",
            "artifacts": [item.model_dump() for item in digests],
            "provenance": "committed bytes on this card's latest completed branch; hashes do not prove evaluation claims",
        }
