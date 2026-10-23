"""Source commands publish through the same canonical transaction as native research."""

import hashlib
import tempfile
from pathlib import Path

from pydantic import BaseModel, JsonValue

from ide_research_core import policy_files, project_source, workspace
from ide_research_core.io import gitops
from ide_research_core.source_schema import HermesPolicyFiles, HermesProjectSource


class SourceReceipt(BaseModel):
    fingerprint: str
    result: dict[str, JsonValue]


def execute(
    root: Path, command: HermesPolicyFiles | HermesProjectSource, request_id: str
) -> dict:
    head = workspace.sync(root)
    writing = command.action in {"seed", "write", "patch"}
    fingerprint = hashlib.sha256(
        (type(command).__name__ + command.model_dump_json()).encode()
    ).hexdigest()
    receipt_path = (
        f"control/ide/requests/{hashlib.sha256(request_id.encode()).hexdigest()}.json"
    )
    if writing:
        if not request_id:
            raise workspace.WorkspaceError(
                "bad_arguments", "Source changes require their original request ID"
            )
        saved = gitops.show(root, head, receipt_path)
        if saved is not None:
            receipt = SourceReceipt.model_validate_json(saved)
            if receipt.fingerprint != fingerprint:
                raise workspace.WorkspaceError(
                    "request_conflict",
                    "This request already names another source change",
                )
            return receipt.result
    prefixes = (
        [f"projects/{command.project_id}"]
        if isinstance(command, HermesProjectSource)
        else ["versions", *([command.path] if command.path else [])]
    )
    with tempfile.TemporaryDirectory(prefix="research-source-") as directory:
        snapshot = Path(directory).resolve()
        # A committed parent link must never be reinterpreted as a fresh writable directory.
        ancestors = sorted(
            {
                str(parent)
                for prefix in prefixes
                for parent in Path(prefix).parents
                if str(parent) != "."
            }
        )
        if ancestors:
            entries = gitops.git(
                root, "--literal-pathspecs", "ls-tree", head, "--", *ancestors
            )
            if any(not entry.startswith("040000 ") for entry in entries.splitlines()):
                raise workspace.WorkspaceError(
                    "forbidden", "Source ancestors must be directories"
                )
        entries = gitops._run(
            root, "--literal-pathspecs", "ls-tree", "-rz", head, "--", *prefixes
        ).stdout
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            header, name = entry.split(b"\t", 1)
            mode, kind, _sha = header.decode().split()
            if kind != "blob":
                raise workspace.WorkspaceError(
                    "invalid_source", "Source submodules are unsupported"
                )
            path = name.decode()
            content = gitops.show(root, head, path)
            assert content is not None
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "120000":
                target.symlink_to(content.decode())
            else:
                target.write_bytes(content)
                target.chmod(int(mode, 8) & 0o777)
        if isinstance(command, HermesProjectSource):
            result = project_source.execute(snapshot, command).model_dump(mode="json")
        else:
            result = policy_files.execute(snapshot, command).model_dump(mode="json")
            if command.action == "read" and result["kind"] == "ok":
                result["modified_at"] = gitops.git(
                    root, "log", "-1", "--format=%cI", head, "--", command.path
                )
        if writing:
            files: dict[str, bytes] = {}
            if result["kind"] == "ok":
                if isinstance(command, HermesProjectSource):
                    changed = (
                        sorted((snapshot / prefixes[0]).rglob("*"))
                        if command.action == "seed"
                        else [snapshot / prefixes[0] / "files" / command.path]
                    )
                else:
                    changed = [snapshot / command.path]
                files = {
                    path.relative_to(snapshot).as_posix(): path.read_bytes()
                    for path in changed
                    if path.is_file()
                }
            receipt = SourceReceipt(fingerprint=fingerprint, result=result)
            files[receipt_path] = receipt.model_dump_json().encode()
            workspace.publish(root, files, "Update project source", expected_head=head)
        return result
