"""Source commands publish through the same canonical transaction as native research."""

import hashlib
import tempfile
from pathlib import Path

from pydantic import BaseModel, JsonValue

from ide_research_core import policy_files, project_source, workspace
from ide_research_core.games.scope import GAME_NAME
from ide_research_core.io import gitops
from ide_research_core.source_schema import HermesPolicyFiles, HermesProjectSource


class SourceReceipt(BaseModel):
    fingerprint: str
    result: dict[str, JsonValue]


def execute_policy(root: Path, command: HermesPolicyFiles, request_id: str) -> dict:
    """Resolve upload records across this profile's canonical game workspaces."""
    if command.action == "register_project":
        return register_project(root, command, request_id)
    canonical = workspace.canonical_of(root).resolve()
    roots = [root]
    for bare in sorted(canonical.parent.glob("canonical-*.git")):
        game = bare.name.removeprefix("canonical-").removesuffix(".git")
        if bare == canonical or bare.is_symlink() or not GAME_NAME.fullmatch(game):
            continue
        clone = root.parent / f"workspace-{game}"
        if clone.is_symlink():
            raise workspace.WorkspaceError(
                "forbidden", "Game workspaces cannot be symlinks"
            )
        with workspace.canonical_lock(bare):
            if not clone.exists():
                gitops.git(root.parent, "clone", "-q", str(bare), str(clone))
                workspace._ensure_committer(clone)
        if workspace.canonical_of(clone).resolve() != bare:
            raise workspace.WorkspaceError(
                "forbidden", "Game workspace has another canonical repository"
            )
        roots.append(clone)
    matches: list[tuple[Path, policy_files.PolicySource]] = []
    discovery = command.model_copy(
        update={"action": "sources", "path": "", "offset": 0}
    )
    for clone in roots:
        offset = 0
        while True:
            result = policy_files.FileResult.model_validate(
                execute(clone, discovery.model_copy(update={"offset": offset}), "")
            )
            if result.kind != "ok":
                return result.model_dump(mode="json")
            matches.extend((clone, source) for source in result.sources)
            if result.next_offset is None:
                break
            offset = result.next_offset
    if len(
        {(source.record_id, tuple(source.version_ids)) for _, source in matches}
    ) != len(matches):
        return policy_files.FileResult(
            kind="conflict",
            error="The source record exists in more than one game workspace.",
        ).model_dump(mode="json")
    if command.action == "sources":
        return policy_files.source_page(
            [source for _, source in matches], command.offset
        ).model_dump(mode="json")
    if len(matches) != 1:
        return policy_files.FileResult(
            kind="not_found", error="The original policy source record is unavailable."
        ).model_dump(mode="json")
    return execute(matches[0][0], command, request_id)


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
        else [
            "versions",
            "source-publications",
            *([command.path] if command.path else []),
        ]
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
            entries = gitops._run(
                root, "--literal-pathspecs", "ls-tree", "-tz", head, "--", *ancestors
            ).stdout
            for entry in entries.split(b"\0"):
                if not entry:
                    continue
                header, name = entry.split(b"\t", 1)
                if name.decode() in ancestors and not header.startswith(b"040000 "):
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


def register_project(root: Path, command: HermesPolicyFiles, request_id: str) -> dict:
    """Resolve the published revision from workspace history, independently of any chat."""
    head = workspace.sync(root)
    if not request_id:
        raise workspace.WorkspaceError(
            "bad_arguments", "Publication registration requires an operation ID"
        )
    binding_path = f"source-publications/{command.version_id}.json"
    existing = gitops.show(root, head, binding_path)
    if existing is not None:
        source = policy_files.PolicySource.model_validate_json(existing)
        if (source.record_id, source.policy_name, source.published_revision) != (
            command.record_id,
            command.policy_name,
            command.base_sha256,
        ):
            raise workspace.WorkspaceError(
                "request_conflict", "Publication already names another source"
            )
        return policy_files.FileResult(kind="ok", sources=[source]).model_dump(
            mode="json"
        )
    project_id = command.record_id[2:]
    prefix = f"projects/{project_id}"
    revisions = gitops.git(root, "log", "--format=%H", head, "--", prefix).splitlines()
    for revision in revisions:
        identity = gitops.show(root, revision, f"{prefix}/project.json")
        if identity is None:
            continue
        metadata = project_source.ProjectIdentity.model_validate_json(identity)
        with tempfile.TemporaryDirectory(prefix="publication-source-") as directory:
            snapshot = Path(directory).resolve()
            entries = gitops._run(
                root, "ls-tree", "-rz", revision, "--", f"{prefix}/files"
            ).stdout
            for entry in entries.split(b"\0"):
                if not entry:
                    continue
                header, name = entry.split(b"\t", 1)
                mode, kind, _ = header.decode().split()
                if kind != "blob" or mode == "120000":
                    raise workspace.WorkspaceError(
                        "invalid_source", "Project sources must be regular files"
                    )
                path = name.decode()
                data = gitops.show(root, revision, path)
                assert data is not None
                target = snapshot / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            result = project_source.snapshot(snapshot / prefix, metadata, "")
        if result.kind != "ok" or result.source_revision != command.base_sha256:
            continue
        source = policy_files.PolicySource(
            record_id=command.record_id,
            policy_name=command.policy_name,
            version_ids=[command.version_id],
            path=f"{prefix}/files",
            files=[f"{prefix}/files/{file.path}" for file in result.files],
            published_revision=command.base_sha256,
            published_commit=revision,
            project=policy_files.ProjectReference(
                project_id=project_id, conversation_id=metadata.conversation_id
            ),
        )
        # The publication's original bytes remain reachable through canonical history.
        workspace.publish(
            root,
            {binding_path: source.model_dump_json().encode()},
            "Register published project source",
            expected_head=head,
        )
        return policy_files.FileResult(kind="ok", sources=[source]).model_dump(
            mode="json"
        )
    raise workspace.WorkspaceError(
        "not_found", "Published source revision is absent from workspace history"
    )
