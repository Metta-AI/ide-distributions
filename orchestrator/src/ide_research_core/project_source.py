"""Unpublished projects in the retained canonical repository, with exact source identities."""

import base64
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ide_research_core.policy_files import (
    FileResult,
    check_path,
    parent_descriptor,
    read_file,
)
from ide_research_core.source_io import replace_file
from ide_research_core.source_schema import HermesProjectConfig, HermesProjectSource


class ProjectIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    league_id: str
    conversation_id: str
    config: HermesProjectConfig
    seed_hash: str
    archive_sha256: str


class ProjectMetadata(ProjectIdentity):
    files: list[str]


class ProjectFile(BaseModel):
    path: str
    sha256: str
    size: int
    content: str | None = None


class ProjectResult(BaseModel):
    kind: Literal[
        "ok",
        "not_found",
        "forbidden",
        "conflict",
        "request_conflict",
        "invalid_source",
        "too_large",
        "not_text",
    ]
    error: str | None = None
    project: ProjectMetadata | None = None
    source_revision: str | None = None
    files: list[ProjectFile] = Field(default_factory=list)
    content: str | None = None


def unpack(command: HermesProjectSource) -> dict[str, bytes] | ProjectResult:
    data = base64.b64decode(command.archive_base64, validate=True)
    if hashlib.sha256(data).hexdigest() != command.archive_sha256:
        return ProjectResult(
            kind="invalid_source",
            error="Starter archive hash does not match its receipt.",
        )
    files: dict[str, bytes] = {}
    total = 0

    def add(name: str, content: bytes) -> ProjectResult | None:
        nonlocal total
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or "\\" in name
            or "\x00" in name
            or any(not part or part in {".", "..", ".git"} for part in name.split("/"))
        ):
            return ProjectResult(
                kind="invalid_source", error="Starter contains a unsafe source path."
            )
        if name in files:
            return ProjectResult(
                kind="invalid_source", error="Starter contains duplicate source paths."
            )
        total += len(content)
        if (
            len(name) > 240
            or len(files) >= 256
            or len(content) > 200000
            or total > 500000
        ):
            return ProjectResult(
                kind="too_large", error="Starter exceeds bounded project source limits."
            )
        files[name] = content
        return None

    if command.archive_format == "file":
        rejected = add(command.path or "player/starter", data)
        if rejected:
            return rejected
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 512:
                return ProjectResult(
                    kind="too_large", error="Starter archive has too many entries."
                )
            for member in archive.infolist():
                if member.is_dir():
                    continue
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in {0, stat.S_IFREG}:
                    return ProjectResult(
                        kind="invalid_source",
                        error="Starter links and special files are unsupported.",
                    )
                if member.file_size > 200000:
                    return ProjectResult(
                        kind="too_large",
                        error="Starter file exceeds the source size limit.",
                    )
                prefix = (
                    ""
                    if command.config is not None
                    and command.config.runtime == "platform-hosted"
                    else "player/"
                )
                rejected = add(prefix + member.filename, archive.read(member))
                if rejected:
                    return rejected
    if not files:
        return ProjectResult(
            kind="invalid_source", error="Starter has no source files."
        )
    return files


def snapshot(
    root: Path, metadata: ProjectIdentity, path: str, include_contents: bool = False
) -> ProjectResult:
    files: list[ProjectFile] = []
    content = None
    rejected = check_path(root, "files", allow_hidden=True)
    if rejected is not None:
        return ProjectResult(kind="invalid_source", error=rejected.error)
    names = []
    for entry in (root / "files").rglob("*"):
        if entry.is_symlink():
            return ProjectResult(
                kind="invalid_source", error="Project source cannot contain links."
            )
        if entry.is_file():
            name = entry.relative_to(root / "files").as_posix()
            if len(name) > 240 or len(names) >= 256:
                return ProjectResult(
                    kind="too_large", error="Project source exceeds its file limits."
                )
            names.append(name)
    if path and path not in names:
        return ProjectResult(
            kind="forbidden",
            error="The requested file is not in this project's source tree.",
        )
    metadata = ProjectMetadata(**metadata.model_dump(), files=sorted(names))
    total = 0
    for name in metadata.files:
        value = read_file(root, "files/" + name, allow_hidden=True)
        if isinstance(value, FileResult):
            return ProjectResult(kind="invalid_source", error=value.error)
        total += len(value.data)
        if total > 500000:
            return ProjectResult(
                kind="too_large", error="Project source exceeds its total byte limit."
            )
        files.append(
            ProjectFile(
                path=name,
                sha256=hashlib.sha256(value.data).hexdigest(),
                size=len(value.data),
            )
        )
        if include_contents:
            text = value.data.decode("utf8", errors="replace")
            if text.encode("utf8") != value.data:
                return ProjectResult(
                    kind="not_text", error="Build source must be UTF-8 text."
                )
            files[-1].content = text
        if name == path:
            if len(value.data) > 60000:
                return ProjectResult(
                    kind="too_large", error="Source exceeds the text editor limit."
                )
            content = value.data.decode("utf8", errors="replace")
            if content.encode("utf8") != value.data:
                return ProjectResult(
                    kind="not_text", error="The selected source is not UTF-8 text."
                )
    revision = hashlib.sha256(
        json.dumps(
            [item.model_dump(exclude={"content"}) for item in files],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return ProjectResult(
        kind="ok",
        project=metadata,
        source_revision=revision,
        files=files,
        content=content,
    )


def execute(root: Path, command: HermesProjectSource) -> ProjectResult:
    project = root / "projects" / str(command.project_id)
    if project.is_symlink():
        return ProjectResult(
            kind="forbidden", error="Project directories cannot be links."
        )
    operation = hashlib.sha256(
        command.model_dump_json(
            exclude={"old_text", "new_text"} if command.action != "patch" else set()
        ).encode()
    ).hexdigest()
    if command.action == "seed" and not project.exists():
        files = unpack(command)
        if isinstance(files, ProjectResult):
            return files
        assert command.config is not None
        project.mkdir(parents=True)
        metadata = ProjectIdentity(
            project_id=str(command.project_id),
            league_id=command.league_id,
            conversation_id=command.conversation_id,
            config=command.config,
            seed_hash=operation,
            archive_sha256=command.archive_sha256,
        )
        for name, data in files.items():
            target = project / "files" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (project / "project.json").write_text(metadata.model_dump_json())
    if not project.exists():
        return ProjectResult(
            kind="not_found", error="Project does not exist in the canonical workspace."
        )
    value = read_file(root, f"projects/{command.project_id}/project.json")
    if isinstance(value, FileResult):
        return ProjectResult(kind="invalid_source", error=value.error)
    metadata = ProjectIdentity.model_validate_json(value.data)
    if (metadata.project_id, metadata.league_id, metadata.conversation_id) != (
        str(command.project_id),
        command.league_id,
        command.conversation_id,
    ):
        return ProjectResult(
            kind="forbidden",
            error="Project scope does not match the original conversation.",
        )
    if command.action == "seed" and metadata.seed_hash != operation:
        return ProjectResult(
            kind="request_conflict", error="Project ID already names another seed."
        )
    current = snapshot(project, metadata, command.path, command.include_contents)
    if current.kind != "ok" or command.action == "read":
        return current
    if command.action in {"write", "patch"}:
        selected = next(item for item in current.files if item.path == command.path)
        if (
            current.source_revision != command.source_revision
            or selected.sha256 != command.base_sha256
        ):
            return ProjectResult(
                kind="conflict",
                error="Project changed. Read its current source before saving.",
            )
        if command.action == "patch":
            assert current.content is not None
            if current.content.count(command.old_text) != 1:
                return ProjectResult(
                    kind="conflict", error="oldText must match exactly once."
                )
            desired = current.content.replace(
                command.old_text, command.new_text, 1
            ).encode()
        else:
            desired = command.content.encode()
        if len(desired) > 60000:
            return ProjectResult(
                kind="too_large", error="Source edit exceeds the text editor limit."
            )
        with parent_descriptor(project, "files/" + command.path, allow_hidden=True) as (
            directory,
            name,
        ):
            replace_file(directory, name, desired, 0o600)
    return snapshot(project, metadata, command.path, command.include_contents)
