"""Owner-requested source edits bound to native task and immutable upload provenance.

The bridge supplies a task already authorized for the originating session. Files are
limited to that policy record's source manifest, opened without following symlinks.
The workspace command supplies an isolated committed snapshot and publishes the resulting edit.
"""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from ide_research_core.source_io import replace_file
from ide_research_core.source_schema import HermesPolicyFiles


class BuildSource(BaseModel):
    source_path: str
    source_hashes: dict[str, str] = Field(default_factory=dict)


class VersionSource(BaseModel):
    id: str
    policy_name: str
    build: BuildSource


class UploadData(BaseModel):
    policy_version_id: str


class VersionEvent(BaseModel):
    kind: str
    data: dict[str, object]
    version: str


class PolicySource(BaseModel):
    record_id: str
    policy_name: str
    version_ids: list[str]
    path: str
    files: list[str]


class FileEntry(BaseModel):
    path: str
    name: str
    type: Literal["file", "directory"]


class FileResult(BaseModel):
    kind: Literal[
        "ok",
        "not_found",
        "forbidden",
        "conflict",
        "too_large",
        "request_conflict",
        "invalid_path",
        "not_text",
    ]
    error: str | None = None
    sources: list[PolicySource] = Field(default_factory=list)
    entries: list[FileEntry] = Field(default_factory=list)
    next_offset: int | None = None
    content: str | None = None
    encoding: Literal["utf8"] = "utf8"
    sha256: str | None = None
    size: int | None = None
    modified_at: str | None = None


def source_path(value: str, *, allow_hidden: bool = False) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and len(value) <= 1024
        and not path.is_absolute()
        and "\\" not in value
        and "\x00" not in value
        and all(
            bool(part)
            and part not in {".", ".."}
            and (allow_hidden or not part.startswith("."))
            for part in value.split("/")
        )
    )


def check_path(
    root: Path, relative: str, *, allow_hidden: bool = False
) -> FileResult | None:
    """Reject static unsafe paths before opening; descriptor walks also prevent swap escapes."""
    if not source_path(relative, allow_hidden=allow_hidden):
        return FileResult(
            kind="invalid_path", error="Expected a visible relative source path."
        )
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            return FileResult(kind="forbidden", error="Symlinks are not source files.")
        if not current.exists():
            return FileResult(kind="not_found", error="Source path does not exist.")
        metadata = current.lstat()
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1 or index != len(parts) - 1:
                return FileResult(
                    kind="forbidden", error="Hardlinked files are not editable sources."
                )
        elif not stat.S_ISDIR(metadata.st_mode):
            return FileResult(
                kind="forbidden",
                error="Only regular source files and directories are supported.",
            )
    return None


@contextmanager
def parent_descriptor(
    root: Path, relative: str, *, allow_hidden: bool = False
) -> Iterator[tuple[int, str]]:
    """Open all ancestors without following links, including ancestors of the task root."""
    assert root.is_absolute() and source_path(relative, allow_hidden=allow_hidden)
    parts = (*root.parts[1:], *PurePosixPath(relative).parts)
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


class FileSnapshot(BaseModel):
    data: bytes
    mode: int
    modified_at: str


def read_file(
    root: Path, relative: str, limit: int = 200000, *, allow_hidden: bool = False
) -> FileSnapshot | FileResult:
    rejected = check_path(root, relative, allow_hidden=allow_hidden)
    if rejected is not None:
        return rejected
    with parent_descriptor(root, relative, allow_hidden=allow_hidden) as (
        directory,
        name,
    ):
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                return FileResult(
                    kind="forbidden",
                    error="Only regular, unlinked source files can be opened.",
                )
            data = stream.read(limit + 1)
            if len(data) > limit:
                return FileResult(
                    kind="too_large", error="Source file exceeds the editor size limit."
                )
            return FileSnapshot(
                data=data,
                mode=stat.S_IMODE(metadata.st_mode),
                modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC).isoformat(),
            )


def sources(root: Path, command: HermesPolicyFiles) -> list[PolicySource] | FileResult:
    """Read the original upload records, not caller-supplied directories or policy labels."""
    versions = root / "versions"
    if not versions.is_dir() or versions.is_symlink():
        return []
    directories = []
    candidates = (
        [versions / command.record_id] if command.record_id else versions.iterdir()
    )
    for index, path in enumerate(candidates):
        if index >= 1000:
            return FileResult(
                kind="too_large",
                error="Too many provenance records; select a record directly.",
            )
        if (
            path.name.startswith("V-")
            and path.is_dir()
            and not path.is_symlink()
            and (not command.record_id or path.name == command.record_id)
        ):
            directories.append(path)
    directories.sort()
    result: list[PolicySource] = []
    for directory in directories:
        if command.record_id and directory.name != command.record_id:
            continue
        file = directory / "version.json"
        if not file.is_file() or file.is_symlink():
            continue
        snapshot = read_file(root, file.relative_to(root).as_posix())
        if isinstance(snapshot, FileResult):
            return snapshot
        record = VersionSource.model_validate_json(snapshot.data)
        if record.id != directory.name or record.policy_name != command.policy_name:
            continue
        if (
            not source_path(record.build.source_path)
            or record.build.source_path.split("/")[0] == "versions"
        ):
            continue
        versions_ids: list[str] = []
        events = directory / "events"
        if events.is_dir() and not events.is_symlink():
            for index, event_file in enumerate(events.glob("*.json")):
                if index >= 1000:
                    return FileResult(
                        kind="too_large", error="Too many upload events in this record."
                    )
                snapshot = read_file(root, event_file.relative_to(root).as_posix())
                if isinstance(snapshot, FileResult):
                    return snapshot
                event = VersionEvent.model_validate_json(snapshot.data)
                if event.kind == "uploaded" and event.version == record.id:
                    versions_ids.append(
                        UploadData.model_validate(event.data).policy_version_id
                    )
        if command.version_id and command.version_id not in versions_ids:
            continue
        files = sorted(
            {
                record.build.source_path,
                *(
                    name
                    for name in record.build.source_hashes
                    if source_path(name) and name.split("/")[0] != "versions"
                ),
            }
        )
        result.append(
            PolicySource(
                record_id=record.id,
                policy_name=record.policy_name,
                version_ids=versions_ids,
                path=record.build.source_path,
                files=files,
            )
        )
    return result


def execute(root: Path, command: HermesPolicyFiles) -> FileResult:
    if command.path and not source_path(command.path):
        return FileResult(
            kind="invalid_path", error="Expected a visible relative source path."
        )
    matched = sources(root, command)
    if isinstance(matched, FileResult):
        return matched
    if command.action == "sources":
        page: list[PolicySource] = []
        size = 0
        for source in matched[command.offset : command.offset + 100]:
            source_size = len(source.model_dump_json())
            if size + source_size > 500000:
                break
            page.append(source)
            size += source_size
        if not page and command.offset < len(matched):
            return FileResult(
                kind="too_large",
                error="The source manifest exceeds the response limit.",
            )
        next_offset = command.offset + len(page)
        return FileResult(
            kind="ok",
            sources=page,
            next_offset=next_offset if next_offset < len(matched) else None,
        )
    if len(matched) != 1:
        return FileResult(
            kind="not_found",
            error="The original policy source record is unavailable in the canonical workspace.",
        )
    source = matched[0]
    if command.action == "tree":
        if command.path:
            rejected = check_path(root, command.path)
            if rejected is not None:
                return rejected
        prefix = command.path + "/" if command.path else ""
        children: dict[str, FileEntry] = {}
        for file in source.files:
            if not file.startswith(prefix):
                continue
            relative = file[len(prefix) :]
            name = relative.split("/")[0]
            children[name] = FileEntry(
                path=prefix + name,
                name=name,
                type="directory" if "/" in relative else "file",
            )
        entries = sorted(
            children.values(), key=lambda entry: (entry.type != "directory", entry.name)
        )
        return FileResult(
            kind="ok",
            entries=entries[command.offset : command.offset + 200],
            next_offset=command.offset + 200
            if len(entries) > command.offset + 200
            else None,
        )
    if command.path not in source.files or not source_path(command.path):
        return FileResult(
            kind="forbidden",
            error="This file is not part of the policy's recorded source manifest.",
        )
    if command.action == "read":
        snapshot = read_file(root, command.path, 60000)
        if isinstance(snapshot, FileResult):
            return snapshot
        content = snapshot.data.decode("utf8", errors="replace")
        if content.encode("utf8") != snapshot.data:
            return FileResult(kind="not_text", error="The source is not UTF-8 text.")
        return FileResult(
            kind="ok",
            content=content,
            sha256=hashlib.sha256(snapshot.data).hexdigest(),
            size=len(snapshot.data),
            modified_at=snapshot.modified_at,
        )
    desired = command.content.encode("utf8")
    if len(desired) > 60000:
        return FileResult(
            kind="too_large", error="Source edits are limited to 60000 UTF-8 bytes."
        )
    snapshot = read_file(root, command.path, 60000)
    if isinstance(snapshot, FileResult):
        return snapshot
    if hashlib.sha256(snapshot.data).hexdigest() != command.base_sha256:
        return FileResult(
            kind="conflict", error="The source changed. Reload before saving."
        )
    with parent_descriptor(root, command.path) as (directory, name):
        replace_file(directory, name, desired, snapshot.mode & 0o777)
    return FileResult(
        kind="ok",
        content=command.content,
        sha256=hashlib.sha256(desired).hexdigest(),
        size=len(desired),
        modified_at=datetime.now(UTC).isoformat(),
    )
