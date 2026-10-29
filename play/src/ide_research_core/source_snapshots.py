"""Immutable build inputs and UUID publication pointers on the workspace volume."""

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ide_research_core import policy_files
from ide_research_core.io import gitops
from ide_research_core.source_io import SourceError, retain_file, write_file
from ide_research_core.source_schema import SourceReceipt
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
    storage.mkdir(parents=True, exist_ok=True)
    objects = storage / "objects.git"
    with canonical_lock(storage):
        if not objects.exists():
            gitops.git(storage, "init", "--bare", str(objects))
            gitops.git(objects, "config", "core.fsync", "committed")
        if commit:
            commit = gitops.git(repository, "rev-parse", f"{commit}^{{commit}}")
            gitops.git(objects, "fetch", "--no-tags", str(repository), commit)
        else:
            # Installed compiler tools also accept source directories without Git history.
            # Give those inputs the same object store and immutable snapshot format.
            tree = gitops._run(objects, "mktree", input=b"").stdout.decode().strip()
            commit = (
                gitops._run(
                    objects,
                    "commit-tree",
                    tree,
                    "-m",
                    "Source inputs",
                    env={
                        **os.environ,
                        "GIT_AUTHOR_NAME": "Source retention",
                        "GIT_AUTHOR_EMAIL": "source@localhost",
                        "GIT_COMMITTER_NAME": "Source retention",
                        "GIT_COMMITTER_EMAIL": "source@localhost",
                        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
                        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
                    },
                )
                .stdout.decode()
                .strip()
            )
        ignored = []
        changes = {}
        modes = {}
        for name, expected in sorted(source_hashes.items()):
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise SourceError("Source hashes must be SHA256 values")
            entry = gitops.git(objects, "--literal-pathspecs", "ls-tree", commit, "--", name)
            with tempfile.TemporaryFile() as captured:
                with policy_files.parent_descriptor(repository, name) as (
                    directory,
                    filename,
                ):
                    metadata = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                    digest = hashlib.sha256()
                    if stat.S_ISLNK(metadata.st_mode):
                        data = os.fsencode(os.readlink(filename, dir_fd=directory))
                        captured.write(data)
                        digest.update(data)
                        modes[name] = 0o120000
                    else:
                        descriptor = os.open(
                            filename,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=directory,
                        )
                        with os.fdopen(descriptor, "rb") as source:
                            metadata = os.fstat(source.fileno())
                            if not stat.S_ISREG(metadata.st_mode):
                                raise SourceError("Build inputs must be files or symbolic links")
                            modes[name] = 0o100755 if metadata.st_mode & 0o111 else 0o100644
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                                captured.write(chunk)
                if digest.hexdigest() != expected:
                    raise SourceError(f"Input changed since build: {name}")
                captured.seek(0)
                if (
                    not entry
                    and gitops._run(
                        repository / Path(name).parent,
                        "check-ignore",
                        "--quiet",
                        "--",
                        Path(name).name,
                        check=False,
                    ).returncode
                    == 0
                ):
                    ignored.append(name)
                    retain_file(storage, f"ignored/{expected}", captured, expected, 0o600)
                else:
                    result = subprocess.run(
                        ["git", "-C", str(objects), "hash-object", "-w", "--stdin"],
                        stdin=captured,
                        capture_output=True,
                        check=True,
                    )
                    blob = result.stdout.decode().strip()
                    if (
                        not entry
                        or entry.split()[2] != blob
                        or int(entry.split()[0], 8) != modes[name]
                    ):
                        changes[name] = blob
        if changes:
            with tempfile.TemporaryDirectory(prefix="source-index-") as directory:
                env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
                gitops._run(objects, "read-tree", commit, env=env)
                for name, blob in changes.items():
                    for parent in Path(name).parents:
                        if parent == Path("."):
                            continue
                        entry = gitops.git(
                            objects,
                            "--literal-pathspecs",
                            "ls-tree",
                            commit,
                            "--",
                            parent.as_posix(),
                        )
                        if entry.startswith("160000 "):
                            gitops._run(
                                objects,
                                "update-index",
                                "-z",
                                "--index-info",
                                input=f"0 {entry.split()[2]}\t{parent.as_posix()}\0".encode(),
                                env=env,
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


@contextmanager
def snapshot_input(storage: Path, snapshot: SourceSnapshot, name: str) -> Iterator[BinaryIO]:
    """Stream and verify retained bytes, including large ignored build inputs."""
    storage = storage.resolve()
    if name not in snapshot.source_hashes or not policy_files.source_path(name):
        raise SourceError("Path is absent from published source")
    with tempfile.TemporaryFile() as content:
        if name in snapshot.ignored:
            with policy_files.parent_descriptor(
                storage, f"ignored/{snapshot.source_hashes[name]}"
            ) as (directory, filename):
                descriptor = os.open(
                    filename,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise SourceError("Ignored input is not a regular retained blob")
                    shutil.copyfileobj(source, content, length=1024 * 1024)
        else:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(storage / "objects.git"),
                    "cat-file",
                    "blob",
                    f"{snapshot.commit}:{name}",
                ],
                stdout=content,
                stderr=subprocess.PIPE,
                check=True,
            )
        content.seek(0)
        if hashlib.file_digest(content, "sha256").hexdigest() != snapshot.source_hashes[name]:
            raise SourceError("Published source bytes do not match the build")
        content.seek(0)
        yield content


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
    snapshot = SourceSnapshot.model_validate_json(
        (storage / "staged" / source.published_revision / "snapshot.json").read_bytes()
    )
    if command.path in snapshot.modes and snapshot.modes[command.path] == 0o120000:
        return policy_files.FileResult(
            kind="forbidden",
            error="Symbolic links are build metadata, not editable files",
        )
    state_path = f"edits/{source.record_id}.json"

    with canonical_lock(storage):
        path = storage / state_path
        state = (
            SourceEdits.model_validate_json(path.read_bytes()) if path.exists() else SourceEdits()
        )
        if command.action == "write":
            fingerprint = SourceReceipt.identity(command.model_dump_json(), request_id)
            if request_id in state.receipts:
                previous, result = state.receipts[request_id]
                receipt = SourceReceipt(fingerprint=previous, result=result.model_dump(mode="json"))
                return policy_files.FileResult.model_validate(receipt.replay(fingerprint))
        # The same bounded editor serves canonical files and retained publications.
        # Materialize only the requested file, never the retained tree or large inputs.
        with tempfile.TemporaryDirectory(prefix="source-edit-") as directory:
            root = Path(directory).resolve()
            if command.path in source.files:
                target = root / command.path
                target.parent.mkdir(parents=True, exist_ok=True)
                if command.path in state.files:
                    target.write_text(state.files[command.path])
                else:
                    with snapshot_input(storage, snapshot, command.path) as content:
                        target.write_bytes(content.read(60001))
                target.chmod(snapshot.modes[command.path] & 0o777)
            elif (
                command.action == "tree"
                and command.path
                and any(name.startswith(command.path + "/") for name in source.files)
            ):
                (root / command.path).mkdir(parents=True)
            result = policy_files.execute(root, command, source=source)
            result.modified_at = None  # Temporary materialization time is not source history.
        if command.action == "write":
            if result.kind == "ok":
                state.files[command.path] = command.content
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
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with snapshot_input(storage, snapshot, name) as source:
                if mode == 0o120000:
                    target.symlink_to(os.fsdecode(source.read()))
                else:
                    with target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
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
                with snapshot_input(storage, original, name) as source:
                    if original.modes[name] == 0o120000:
                        target.symlink_to(os.fsdecode(source.read()))
                    else:
                        with target.open("wb") as output:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
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
