"""Bounded committed report reads from a completed card's recorded workspace."""

import subprocess
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .board import Board


class ReportRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=256)
    run_id: int = Field(ge=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: str = Field(min_length=1, max_length=512)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=6000)


class Reports:
    def __init__(self, context):
        self.context = context

    def card_report(self, task_id, run_id, commit, path, offset=0, limit=4000):
        request = ReportRead(task_id=task_id, run_id=run_id, commit=commit, path=path, offset=offset, limit=limit)
        relative = PurePosixPath(request.path)
        if relative.is_absolute() or ".." in relative.parts or "\0" in request.path or str(relative) != request.path:
            raise ValueError("Use the committed report's normalized relative path")
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
        if not branch or not workspace.is_dir():
            raise ValueError("The card workspace is unavailable; request a report handoff from the worker")

        def git(*args):
            result = subprocess.run(
                ["git", "--no-replace-objects", "--literal-pathspecs", "-C", str(workspace), *args],
                capture_output=True, timeout=30, check=False,
            )
            if result.returncode:
                raise ValueError("The requested commit/report is not available on the recorded card branch")
            return result.stdout

        git("check-ref-format", "refs/heads/" + branch)
        git("merge-base", "--is-ancestor", request.commit, "refs/heads/" + branch)
        entry = git("ls-tree", "--format=%(objectmode) %(objecttype) %(objectname)", request.commit, "--", request.path).decode().strip().split()
        if len(entry) != 3 or entry[0] not in ("100644", "100755") or entry[1] != "blob":
            raise ValueError("Choose a committed regular report file; directories and symlinks are not supported")
        blob = entry[2]
        if int(git("cat-file", "-s", blob)) > 256_000:
            raise ValueError("Report exceeds the 256,000-byte read limit; ask the worker for a bounded summary")
        try:
            text = git("cat-file", "blob", blob).decode("utf-8")
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
