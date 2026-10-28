"""Retain hash-checked build inputs as editable, workspace-relative source files."""

import hashlib
import json
import os
import stat
from pathlib import Path

from ide_research_core import policy_files
from ide_research_core.source_io import SourceError, parent_directory, retain_file


def retain(
    root: Path, source_path: str, hashes: dict[str, str]
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
    originals = {
        name: Path(name) if Path(name).is_absolute() else root / name for name in hashes
    }
    modes = {
        name: path.lstat().st_mode & (0o170000 | 0o777)
        for name, path in originals.items()
    }
    manifest_hash = hashlib.sha256(
        json.dumps({"hashes": hashes, "modes": modes}, sort_keys=True).encode()
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
                    raise SourceError(
                        f"Build source must be a regular file: {name}"
                    )
                if metadata.st_mode & (0o170000 | 0o777) != modes[name]:
                    raise SourceError(
                        f"Build source mode changed during retention: {name}"
                    )
                retain_file(root, relative, stream, expected, modes[name] & 0o777)
        mapped[name] = relative
        result[relative] = expected
    return mapped[source_path], result
