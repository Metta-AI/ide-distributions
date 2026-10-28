"""Retain hash-checked build inputs as editable, workspace-relative source files."""

import hashlib
import json
import os
import stat
from pathlib import Path

from pydantic import BaseModel

from ide_research_core import policy_files
from ide_research_core.source_io import (
    SourceError,
    parent_directory,
    retain_file,
    write_file,
)


def retain(
    root: Path,
    source_path: str,
    hashes: dict[str, str],
    *,
    source_root: Path | None = None,
    build_identity: str = "",
) -> tuple[str, dict[str, str]]:
    """Snapshot build inputs into the canonical workspace, including nested or ignored sources.

    The manifest hash names the imported source directory. Never overwrite it: edits there
    belong to the user, even when the same starter is published again.
    """
    if source_path not in hashes:
        raise SourceError(
            "The build's primary source must be included in its source hashes"
        )
    root = root.resolve()
    source_root = root if source_root is None else source_root.resolve()
    originals = {
        name: Path(name) if Path(name).is_absolute() else source_root / name
        for name in hashes
    }
    modes = {
        name: path.lstat().st_mode & (0o170000 | 0o777)
        for name, path in originals.items()
    }
    manifest_hash = hashlib.sha256(
        json.dumps(
            {"hashes": hashes, "modes": modes, "build": build_identity}, sort_keys=True
        ).encode()
    ).hexdigest()
    common = Path(os.path.commonpath([str(path.parent) for path in originals.values()]))
    mapped: dict[str, str] = {}
    result: dict[str, str] = {}
    for name, expected in hashes.items():
        original = originals[name]
        relative = f"sources/{manifest_hash}/{original.relative_to(common).as_posix()}"
        if (
            not policy_files.source_path(relative)
            or relative.split("/")[0] == "versions"
        ):
            raise SourceError(
                f"Build source must have a visible workspace-relative path: {name}"
            )
        target = root / relative
        for ancestor in target.parents:
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise SourceError("Retained source paths cannot contain symlinks")
        with policy_files.parent_descriptor(
            Path(original.anchor),
            original.relative_to(original.anchor).as_posix(),
            allow_hidden=True,
        ) as (directory, filename):
            if stat.S_ISLNK(modes[name]):
                link = os.readlink(filename, dir_fd=directory)
                if hashlib.sha256(os.fsencode(link)).hexdigest() != expected:
                    raise SourceError(f"Build source changed since compilation: {name}")
                # Keep Docker context links as metadata; never follow their targets.
                with parent_directory(root, relative) as (destination, leaf):
                    if leaf in os.listdir(destination):
                        if os.readlink(leaf, dir_fd=destination) != link:
                            raise SourceError(
                                f"Retained source link changed: {relative}"
                            )
                    else:
                        os.symlink(link, leaf, dir_fd=destination)
                        os.fsync(destination)
                mapped[name] = relative
                result[relative] = expected
                continue
            descriptor = os.open(
                filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise SourceError(f"Build source must be a regular file: {name}")
                if metadata.st_mode & (0o170000 | 0o777) != modes[name]:
                    raise SourceError(
                        f"Build source mode changed during retention: {name}"
                    )
                retain_file(root, relative, stream, expected, modes[name] & 0o777)
        mapped[name] = relative
        result[relative] = expected
    return mapped[source_path], result


def capture(build_dir: Path, source_root: Path) -> dict:
    """Finish a build with self-contained inputs before its directory can be relocated."""
    receipt = build_dir / "version.json"
    build = json.loads(receipt.read_bytes())
    primary, hashes = retain(
        build_dir, build["source_path"], build["source_hashes"], source_root=source_root
    )
    build.update(source_root=".", source_path=primary, source_hashes=hashes)
    receipt.write_text(json.dumps(build, sort_keys=True, indent=2) + "\n")
    return build


class StagedSources(BaseModel):
    """Build inputs preserved before a completed task checkout can be removed."""

    receipt_sha256: str
    artifact_sha256: str
    source_path: str
    source_hashes: dict[str, str]

    def verify(self, root: Path, receipt: bytes, artifact_sha256: str) -> None:
        if (
            self.receipt_sha256 != hashlib.sha256(receipt).hexdigest()
            or self.artifact_sha256 != artifact_sha256
        ):
            raise SourceError(
                "Retained sources belong to a different build receipt or artifact"
            )
        if self.source_path not in self.source_hashes:
            raise SourceError("Retained sources do not include the primary build input")
        for relative, expected in self.source_hashes.items():
            if not policy_files.source_path(relative) or not relative.startswith(
                "sources/"
            ):
                raise SourceError(
                    "Retained build inputs must be in the workspace source snapshot"
                )
            with policy_files.parent_descriptor(root.resolve(), relative) as (
                directory,
                filename,
            ):
                metadata = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    digest = hashlib.sha256(
                        os.fsencode(os.readlink(filename, dir_fd=directory))
                    ).hexdigest()
                else:
                    descriptor = os.open(
                        filename,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=directory,
                    )
                    with os.fdopen(descriptor, "rb") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise SourceError(
                                "Retained build inputs must be files or links"
                            )
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != expected:
                    raise SourceError(f"Retained build source changed: {relative}")


def stage(worktree: Path, base: str) -> list[str]:
    """Preserve inputs of this task's new build receipts before Git-only handoff."""
    from ide_research_core.io import gitops
    from ide_research_core.softmax_tool.upload import _gate_failures, checked_build

    changed = gitops._run(worktree, "diff", "--name-only", "-z", base, "--").stdout
    untracked = gitops._run(
        worktree, "ls-files", "--others", "--exclude-standard", "-z"
    ).stdout
    paths = sorted(
        {name.decode() for name in (changed + untracked).split(b"\0") if name}
    )
    preserved = []
    for relative in paths:
        receipt = worktree / relative
        if receipt.name != "version.json" or not receipt.is_file():
            continue
        data = receipt.read_bytes()
        # Ordinary project metadata and version records are not compiler receipts.
        fields = {"artifact", "gates", "source_path", "source_hashes"}
        build = json.loads(data)
        if (
            not isinstance(build, dict)
            or not fields.issubset(build)
            or _gate_failures(build)
        ):
            continue
        build, _artifact, artifact_hash = checked_build(receipt.parent)
        handoff = receipt.parent / "retained-sources.json"
        receipt_hash = hashlib.sha256(data).hexdigest()
        retained = (
            StagedSources.model_validate_json(handoff.read_bytes())
            if handoff.exists()
            else None
        )
        if (
            retained is None
            or retained.receipt_sha256 != receipt_hash
            or retained.artifact_sha256 != artifact_hash
        ):
            source_path, source_hashes = retain(
                worktree,
                build["source_path"],
                build["source_hashes"],
                source_root=receipt.parent / build["source_root"]
                if "source_root" in build
                else worktree,
                build_identity=f"{receipt_hash}:{artifact_hash}",
            )
            retained = StagedSources(
                receipt_sha256=receipt_hash,
                artifact_sha256=artifact_hash,
                source_path=source_path,
                source_hashes=source_hashes,
            )
            write_file(
                worktree.resolve(),
                handoff.relative_to(worktree).as_posix(),
                retained.model_dump_json().encode(),
            )
        preserved.extend(
            [handoff.relative_to(worktree).as_posix(), *retained.source_hashes]
        )
    return preserved
