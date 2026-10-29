"""Immutable build inputs and UUID publication pointers on the workspace volume."""

import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ide_research_core import policy_files
from ide_research_core.io import gitops
from ide_research_core.source_io import SourceError, write_file
from ide_research_core.workspace import canonical_lock


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit: str
    source_path: str
    source_hashes: dict[str, str]
    ignored: list[str]
    modes: dict[str, int]

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class SourcePublication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: UUID
    policy_name: str
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def stage_sources(
    storage: Path,
    repository: Path,
    *,
    commit: str,
    source_path: str,
    source_hashes: dict[str, str],
) -> SourceSnapshot:
    """Retain a committed build plus ignored inputs; storage must be on the workspace PVC.

    Names are repository-relative. Dirty/new inputs become an immutable Git commit without
    changing the user index. Ignored inputs are copied outside the Git tree.
    """
    storage = storage.resolve()
    repository = repository.resolve()
    if source_path not in source_hashes or not source_hashes:
        raise SourceError("Source manifest must include the primary input")
    if any(not policy_files.source_path(name) for name in source_hashes):
        raise SourceError("Source manifest names must be repository-relative")
    commit = gitops.git(repository, "rev-parse", f"{commit}^{{commit}}")
    storage.mkdir(parents=True, exist_ok=True)
    objects = storage / "objects.git"
    with canonical_lock(storage):
        if not objects.exists():
            gitops.git(storage, "init", "--bare", str(objects))
        gitops.git(objects, "fetch", "--no-tags", str(repository), commit)
        ignored = []
        copies = {}
        changes = {}
        modes = {}
        for name, expected in sorted(source_hashes.items()):
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise SourceError("Source hashes must be SHA256 values")
            tracked = gitops.show(objects, commit, name)
            if tracked is not None and hashlib.sha256(tracked).hexdigest() == expected:
                mode = gitops.git(
                    objects, "--literal-pathspecs", "ls-tree", commit, "--", name
                ).split()[0]
                modes[name] = int(mode, 8)
                current = repository / name
                if current.exists() or current.is_symlink():
                    metadata = current.lstat()
                    current_mode = (
                        0o120000
                        if stat.S_ISLNK(metadata.st_mode)
                        else 0o100755
                        if metadata.st_mode & 0o111
                        else 0o100644
                    )
                    if current_mode != modes[name]:
                        modes[name] = current_mode
                        changes[name] = tracked
                continue
            with policy_files.parent_descriptor(repository, name) as (directory, filename):
                metadata = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    data = os.fsencode(os.readlink(filename, dir_fd=directory))
                    modes[name] = 0o120000
                else:
                    value = policy_files.read_file(repository, name, limit=1024 * 1024 * 1024)
                    if isinstance(value, policy_files.FileResult):
                        raise SourceError(f"Cannot retain build input {name}: {value.kind}")
                    data = value.data
                    modes[name] = 0o100755 if value.mode & 0o111 else 0o100644
            if hashlib.sha256(data).hexdigest() != expected:
                raise SourceError(f"Input changed since build: {name}")
            if (
                tracked is None
                and gitops._run(
                    repository, "check-ignore", "--quiet", "--", name, check=False
                ).returncode
                == 0
            ):
                ignored.append(name)
                copies[name] = data
            else:
                changes[name] = data
        if changes:
            with tempfile.TemporaryDirectory(prefix="source-index-") as directory:
                env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
                gitops._run(objects, "read-tree", commit, env=env)
                for name, data in changes.items():
                    blob = (
                        gitops._run(objects, "hash-object", "-w", "--stdin", input=data)
                        .stdout.decode()
                        .strip()
                    )
                    gitops._run(
                        objects,
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"{modes[name]:o},{blob},{name}",
                        env=env,
                    )
                tree = gitops._run(objects, "write-tree", env=env).stdout.decode().strip()
                date = gitops.git(objects, "show", "-s", "--format=%cI", commit)
                env.update(
                    GIT_AUTHOR_NAME="Source retention",
                    GIT_AUTHOR_EMAIL="source@localhost",
                    GIT_COMMITTER_NAME="Source retention",
                    GIT_COMMITTER_EMAIL="source@localhost",
                    GIT_AUTHOR_DATE=date,
                    GIT_COMMITTER_DATE=date,
                )
                commit = (
                    gitops._run(
                        objects,
                        "commit-tree",
                        tree,
                        "-p",
                        commit,
                        "-m",
                        "Retain exact build inputs",
                        env=env,
                    )
                    .stdout.decode()
                    .strip()
                )
        snapshot = SourceSnapshot(
            commit=commit,
            source_path=source_path,
            source_hashes=dict(sorted(source_hashes.items())),
            ignored=ignored,
            modes=modes,
        )
        prefix = f"staged/{snapshot.digest}"
        manifest = storage / prefix / "snapshot.json"
        if manifest.exists():
            if SourceSnapshot.model_validate_json(manifest.read_bytes()) != snapshot:
                raise SourceError("Snapshot identity conflict")
        else:
            for name, data in copies.items():
                write_file(storage, f"{prefix}/ignored/{name}", data)
            # A ref, rather than FETCH_HEAD, keeps source reachable across later fetches and GC.
            gitops.git(objects, "update-ref", f"refs/snapshots/{snapshot.digest}", commit)
            write_file(storage, f"{prefix}/snapshot.json", snapshot.model_dump_json().encode())
        return snapshot


def promote_sources(
    storage: Path,
    *,
    snapshot: str,
    version_id: UUID,
    policy_name: str,
    artifact_sha256: str,
) -> SourcePublication:
    """Bind a platform UUID after upload/recovery; replay never changes the original binding."""
    publication = SourcePublication(
        version_id=version_id,
        policy_name=policy_name,
        snapshot=snapshot,
        artifact_sha256=artifact_sha256,
    )
    storage = storage.resolve()
    with canonical_lock(storage):
        SourceSnapshot.model_validate_json(
            (storage / "staged" / snapshot / "snapshot.json").read_bytes()
        )
        relative = f"versions/{version_id}.json"
        target = storage / relative
        if target.exists():
            existing = SourcePublication.model_validate_json(target.read_bytes())
            if existing != publication:
                raise SourceError("Policy version already names different source")
            return existing
        write_file(storage, relative, publication.model_dump_json().encode())
    return publication


def read_snapshot(storage: Path, snapshot: SourceSnapshot, name: str) -> bytes:
    """Read immutable bytes without materializing a second source checkout."""
    storage = storage.resolve()
    if name not in snapshot.source_hashes or not policy_files.source_path(name):
        raise SourceError("Path is absent from published source")
    if name in snapshot.ignored:
        value = policy_files.read_file(
            storage,
            f"staged/{snapshot.digest}/ignored/{name}",
            limit=1024 * 1024 * 1024,
        )
        if isinstance(value, policy_files.FileResult):
            raise SourceError(f"Cannot read retained input: {value.kind}")
        data = value.data
    else:
        data = gitops.show(storage / "objects.git", snapshot.commit, name)
        if data is None:
            raise SourceError("Published source commit is incomplete")
    if hashlib.sha256(data).hexdigest() != snapshot.source_hashes[name]:
        raise SourceError("Published source bytes do not match the build")
    return data


class SourceEdits(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)
    receipts: dict[str, tuple[str, policy_files.FileResult]] = Field(default_factory=dict)


def publications(storage: Path, command) -> list[policy_files.PolicySource]:
    found = []
    for path in sorted((storage / "versions").glob("*.json")):
        publication = SourcePublication.model_validate_json(path.read_bytes())
        version = str(publication.version_id)
        if publication.policy_name != command.policy_name:
            continue
        if command.version_id and command.version_id != version:
            continue
        if command.record_id and command.record_id != version:
            continue
        snapshot = SourceSnapshot.model_validate_json(
            (storage / "staged" / publication.snapshot / "snapshot.json").read_bytes()
        )
        found.append(
            policy_files.PolicySource(
                record_id=version,
                policy_name=publication.policy_name,
                version_ids=[version],
                path=snapshot.source_path,
                files=list(snapshot.source_hashes),
                published_revision=publication.snapshot,
                published_commit=snapshot.commit,
            )
        )
    return found


def execute_files(storage: Path, command, request_id: str) -> policy_files.FileResult:
    """Read immutable source with a sparse edit overlay; save edit and replay result atomically."""
    from ide_research_core.source_schema import HermesPolicyFiles

    assert isinstance(command, HermesPolicyFiles)
    matched = publications(storage, command)
    if len(matched) != 1:
        return policy_files.FileResult(kind="not_found", error="Published source is unavailable")
    source = matched[0]
    if command.action == "tree":
        prefix = command.path + "/" if command.path else ""
        entries = {}
        for path in source.files:
            if path.startswith(prefix):
                tail = path[len(prefix) :]
                name = tail.split("/")[0]
                entries[name] = policy_files.FileEntry(
                    path=prefix + name,
                    name=name,
                    type="directory" if "/" in tail else "file",
                )
        ordered = sorted(
            entries.values(), key=lambda entry: (entry.type != "directory", entry.name)
        )
        return policy_files.FileResult(
            kind="ok",
            entries=ordered[command.offset : command.offset + 200],
            next_offset=command.offset + 200 if len(ordered) > command.offset + 200 else None,
        )
    if command.path not in source.files:
        return policy_files.FileResult(
            kind="forbidden", error="File is absent from published source"
        )
    snapshot = SourceSnapshot.model_validate_json(
        (storage / "staged" / source.published_revision / "snapshot.json").read_bytes()
    )
    if snapshot.modes[command.path] == 0o120000:
        return policy_files.FileResult(
            kind="forbidden", error="Symbolic links are build metadata, not editable files"
        )
    state_path = f"edits/{source.record_id}.json"
    fingerprint = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    with canonical_lock(storage):
        path = storage / state_path
        state = (
            SourceEdits.model_validate_json(path.read_bytes()) if path.exists() else SourceEdits()
        )
        if command.action == "write":
            if not request_id:
                return policy_files.FileResult(
                    kind="request_conflict", error="Edits require an operation ID"
                )
            if request_id in state.receipts:
                previous, result = state.receipts[request_id]
                if previous != fingerprint:
                    return policy_files.FileResult(
                        kind="request_conflict", error="Operation ID names another edit"
                    )
                return result
        data = (
            state.files[command.path].encode()
            if command.path in state.files
            else read_snapshot(storage, snapshot, command.path)
        )
        content = data.decode("utf8", errors="replace")
        if len(data) > 60000:
            result = policy_files.FileResult(
                kind="too_large", error="Source exceeds the editor limit"
            )
        elif content.encode() != data:
            result = policy_files.FileResult(kind="not_text", error="Source is not UTF-8 text")
        elif command.action == "write" and hashlib.sha256(data).hexdigest() != command.base_sha256:
            result = policy_files.FileResult(
                kind="conflict", error="Source changed; reload before saving"
            )
        elif command.action == "write" and len(command.content.encode()) > 60000:
            result = policy_files.FileResult(
                kind="too_large", error="Edit exceeds the editor limit"
            )
        else:
            if command.action == "write":
                content = command.content
                data = content.encode()
                state.files[command.path] = content
            result = policy_files.FileResult(
                kind="ok",
                content=content,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        if command.action == "write":
            state.receipts[request_id] = (fingerprint, result)
            write_file(storage, state_path, state.model_dump_json().encode())
        return result


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["stage"]
    repository: Path
    commit: str
    source_path: str
    source_hashes: dict[str, str]


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["promote"]
    snapshot: str
    version_id: UUID
    policy_name: str
    artifact_sha256: str


class StagePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["stage_publication"]
    version_id: UUID


snapshot_request = TypeAdapter(StageRequest | PromoteRequest | StagePublicationRequest)


@contextmanager
def build_context(storage: Path, snapshot: SourceSnapshot) -> Iterator[Path]:
    """Materialize exact inputs for one build; this disposable tree is never the retained record."""
    with tempfile.TemporaryDirectory(prefix="policy-build-") as directory:
        root = Path(directory)
        for name in snapshot.source_hashes:
            mode = snapshot.modes[name]
            data = read_snapshot(storage, snapshot, name)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == 0o120000:
                target.symlink_to(os.fsdecode(data))
            else:
                target.write_bytes(data)
                target.chmod(mode & 0o777)
        yield root


def stage_publication(storage: Path, version_id: UUID) -> SourceSnapshot:
    """Freeze the current Code edits as inputs for a new build of a published policy."""
    storage = storage.resolve()
    publication = SourcePublication.model_validate_json(
        (storage / "versions" / f"{version_id}.json").read_bytes()
    )
    original = SourceSnapshot.model_validate_json(
        (storage / "staged" / publication.snapshot / "snapshot.json").read_bytes()
    )
    with canonical_lock(storage):
        path = storage / "edits" / f"{version_id}.json"
        state = (
            SourceEdits.model_validate_json(path.read_bytes()) if path.exists() else SourceEdits()
        )
    if not state.files:
        return original
    # Native Git preserves ignore rules and ancestry while staging the edited inputs.
    with tempfile.TemporaryDirectory(prefix="source-edit-build-") as directory:
        checkout = Path(directory) / "repository"
        objects = storage / "objects.git"
        gitops.git(objects, "worktree", "add", "--detach", str(checkout), original.commit)
        try:
            for name in original.ignored:
                target = checkout / name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = read_snapshot(storage, original, name)
                if original.modes[name] == 0o120000:
                    target.symlink_to(os.fsdecode(data))
                else:
                    target.write_bytes(data)
                    target.chmod(original.modes[name] & 0o777)
            hashes = dict(original.source_hashes)
            for name, content in state.files.items():
                write_file(checkout.resolve(), name, content.encode())
                hashes[name] = hashlib.sha256(content.encode()).hexdigest()
            return stage_sources(
                storage,
                checkout,
                commit=original.commit,
                source_path=original.source_path,
                source_hashes=hashes,
            )
        finally:
            gitops.git(objects, "worktree", "remove", "--force", str(checkout))
