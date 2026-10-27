"""Discover, inspect and export committed evidence from completed native cards."""

import hashlib
import json
import subprocess
from contextlib import closing
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

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


ArtifactPath = Annotated[str, Field(min_length=1, max_length=512)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CardFiles(CompletedCardRead):
    directory: str = Field(default="", max_length=512)
    recursive: bool = True
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=200)


class ArtifactRead(CompletedCardRead):
    paths: list[ArtifactPath] = Field(min_length=1, max_length=64)
    expected_sha256: dict[ArtifactPath, Sha256] = Field(default_factory=dict, max_length=64)
    export: bool = False


class ArtifactDigest(BaseModel):
    path: str
    blob: str
    bytes: int
    sha256: str
    expected_sha256: str | None
    matches: bool | None


class ArtifactError(ValueError):
    def __init__(self, reason: Literal[
        "card_not_completed", "workspace_unavailable", "artifact_unavailable",
        "invalid_artifact_path", "invalid_artifact_selection", "artifact_limit", "invalid_export_cache",
    ], message: str):
        super().__init__(message)
        self.envelope = {"ok": False, "error": {"reason": reason, "message": message}}


class Reports:
    def __init__(self, context):
        self.context = context

    def _workspace(self, request: CompletedCardRead) -> Path:
        snapshot = Board(self.context).card_show(request.task_id)
        task, runs = snapshot["card"], snapshot["runs"]
        latest = max(runs, key=lambda item: item["id"], default=None)
        if (not latest or latest["id"] != request.run_id or latest["task_id"] != request.task_id
                or latest["status"] != "done" or not latest.get("ended_at") or task["status"] != "done"):
            raise ArtifactError("card_not_completed", "Read artifacts only from the card's latest completed run; refresh card_show")
        workspace = Path(task.get("workspace_path") or "").resolve()
        if not task.get("workspace_path") or not workspace.is_relative_to(self.context.root.resolve()):
            raise ArtifactError("workspace_unavailable", "The card workspace is not on this profile host; request a report handoff from the worker")
        branch = task.get("branch_name")
        if not branch:
            raise ArtifactError("workspace_unavailable", "The card workspace is unavailable; request a report handoff from the worker")
        if not workspace.is_dir() and task["workspace_kind"] == "worktree":
            from hermes_cli import kanban_db as kb
            from hermes_cli.kanban_db_workspace import resolve_workspace

            # Native completion removes clean worktrees but retains the card branch.
            # Restore through the same lifecycle API used for completed-build uploads.
            with closing(connection()) as conn:
                completed = kb.get_task(conn, request.task_id)
            restored = resolve_workspace(completed, board=BOARD).resolve()
            if restored != workspace:
                raise ArtifactError("workspace_unavailable", "The restored workspace differs from the card's recorded workspace")
        if not workspace.is_dir():
            raise ArtifactError("workspace_unavailable", "The card workspace is unavailable; request a report handoff from the worker")

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
            raise ArtifactError("artifact_unavailable", "The requested commit/artifact is not available on the recorded card branch")
        return result.stdout

    @staticmethod
    def _relative(path: str) -> PurePosixPath:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\0" in path or str(relative) != path:
            raise ArtifactError("invalid_artifact_path", "Use the committed artifact's normalized relative path")
        return relative

    def _blob(self, workspace: Path, commit: str, path: str) -> tuple[str, int]:
        self._relative(path)
        entry = self._git(workspace, "ls-tree", "--format=%(objectmode) %(objecttype) %(objectname)", commit, "--", path).decode().strip().split()
        if len(entry) != 3 or entry[0] not in ("100644", "100755") or entry[1] != "blob":
            raise ArtifactError("artifact_unavailable", "Choose a committed regular file; directories and symlinks are not supported")
        blob = entry[2]
        return blob, int(self._git(workspace, "cat-file", "-s", blob))

    def card_files(self, task_id, run_id, commit, directory="", recursive=True, offset=0, limit=100):
        request = CardFiles(task_id=task_id, run_id=run_id, commit=commit,
                            directory=directory, recursive=recursive, offset=offset, limit=limit)
        workspace = self._workspace(request)
        if request.directory:
            self._relative(request.directory)
        tree = request.commit + (":" + request.directory if request.directory else "")
        traversal = ["-r", "-t"] if request.recursive else []
        entries = self._git(workspace, "ls-tree", *traversal, "--long", "-z", tree).split(b"\0")[:-1]
        if request.offset > len(entries):
            raise ArtifactError("invalid_artifact_selection", "Offset exceeds the directory's entry count")
        files = []
        for entry in entries[request.offset:request.offset + request.limit]:
            metadata, name = entry.split(b"\t", 1)
            mode, kind, blob, size = metadata.decode().split()
            files.append({
                "path": str(PurePosixPath(request.directory) / name.decode()),
                "kind": "symlink" if mode == "120000" else kind,
                "blob": blob, "bytes": int(size) if size != "-" else None,
            })
        end = min(request.offset + request.limit, len(entries))
        return {
            "task_id": request.task_id, "run_id": request.run_id, "commit": request.commit,
            "directory": request.directory, "recursive": request.recursive, "files": files, "total": len(entries),
            "next_offset": end if end < len(entries) else None,
        }

    def card_report(self, task_id, run_id, commit, path, offset=0, limit=4000):
        request = ReportRead(task_id=task_id, run_id=run_id, commit=commit, path=path, offset=offset, limit=limit)
        workspace = self._workspace(request)
        blob, size = self._blob(workspace, request.commit, request.path)
        if size > 256_000:
            raise ArtifactError("artifact_limit", "Report exceeds the 256,000-byte read limit; export it with card_artifacts for local analysis")
        try:
            text = self._git(workspace, "cat-file", "blob", blob).decode("utf-8")
        except UnicodeDecodeError:
            raise ArtifactError("artifact_unavailable", "Report is not UTF-8 text; export it with card_artifacts") from None
        if "\0" in text:
            raise ArtifactError("artifact_unavailable", "Report is binary; export it with card_artifacts")
        if request.offset > len(text):
            raise ArtifactError("invalid_artifact_selection", "Offset exceeds the report's character count")
        end = min(request.offset + request.limit, len(text))
        return {
            "task_id": request.task_id, "run_id": request.run_id, "commit": request.commit,
            "path": request.path, "blob": blob, "content": text[request.offset:end],
            "characters": len(text), "offset": request.offset, "next_offset": end if end < len(text) else None,
            "offset_unit": "unicode_characters",
            "provenance": "commit reachable from the recorded branch of this card's latest completed run; not an integration receipt",
        }

    def card_artifacts(self, task_id, run_id, commit, paths, expected_sha256=None, export=False):
        request = ArtifactRead(task_id=task_id, run_id=run_id, commit=commit, paths=paths,
                               expected_sha256=expected_sha256 or {}, export=export)
        if len(set(request.paths)) != len(request.paths) or not request.expected_sha256.keys() <= set(request.paths):
            raise ArtifactError("invalid_artifact_selection", "Choose distinct paths and provide expected hashes only for those paths")
        workspace = self._workspace(request)
        entries = [(path, *self._blob(workspace, request.commit, path)) for path in request.paths]
        if sum(size for _, _, size in entries) > 256 * 1024 * 1024:
            raise ArtifactError("artifact_limit", "Artifact batch exceeds 256 MiB; select smaller batches")
        digests = []
        contents = []
        for path, blob, size in entries:
            content = self._git(workspace, "cat-file", "blob", blob)
            digest = hashlib.sha256(content).hexdigest()
            expected = request.expected_sha256.get(path)
            digests.append(ArtifactDigest(
                path=path, blob=blob, bytes=size, sha256=digest,
                expected_sha256=expected, matches=digest == expected if expected else None,
            ))
            if request.export:
                contents.append(content)
        result = {
            "task_id": request.task_id, "run_id": request.run_id, "commit": request.commit,
            "status": "mismatch" if any(item.matches is False for item in digests) else "inspected",
            "artifacts": [item.model_dump() for item in digests],
            "provenance": "committed bytes on this card's latest completed branch; hashes do not prove evaluation claims",
        }
        if request.export and result["status"] != "mismatch":
            cache = self.context.home / "cache" / "card-artifacts"
            if not cache.resolve().is_relative_to(self.context.home.resolve()):
                raise ArtifactError("invalid_export_cache", "Artifact exports must remain inside this coordinator profile")
            cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            with TemporaryDirectory(prefix=".staging-", dir=cache) as temporary:
                staged = Path(temporary) / "snapshot"
                staged.mkdir(mode=0o700)
                for item, content in zip(digests, contents, strict=True):
                    target = staged / "files" / item.path
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with target.open("xb") as output:
                        output.write(content)
                    target.chmod(0o600)
                manifest = staged / "manifest.json"
                manifest.write_text(json.dumps(result, indent=2) + "\n")
                manifest.chmod(0o600)
                destination = cache / Path(temporary).name.removeprefix(".staging-")
                staged.rename(destination)
            result.update(status="exported", directory=str(destination / "files"),
                          manifest=str(destination / "manifest.json"))
        return result
