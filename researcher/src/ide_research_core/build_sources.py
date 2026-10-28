"""Retain hash-checked build inputs as editable, workspace-relative source files."""

import hashlib
import json
import os
from pathlib import Path

from ide_research_core import policy_files
from ide_research_core.source_io import write_file


class SourceError(ValueError):
    pass


def retain(root: Path, source_path: str, hashes: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Keep local source paths; copy external compiler inputs into the canonical workspace.

    The manifest hash names the imported source directory. Never overwrite it: edits there
    belong to the user, even when the same starter is published again.
    """
    root = root.resolve()
    manifest_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    external = [
        Path(name).resolve()
        for name in hashes
        if Path(name).is_absolute() and not Path(name).resolve().is_relative_to(root)
    ]
    # Preserve each external input's relative layout, including sibling strategy files.
    common = Path(os.path.commonpath([str(path.parent) for path in external])) if external else root
    mapped: dict[str, str] = {}
    result: dict[str, str] = {}
    for name, expected in hashes.items():
        original = Path(name) if Path(name).is_absolute() else root / name
        snapshot = policy_files.read_file(
            Path(original.anchor),
            original.relative_to(original.anchor).as_posix(),
            limit=20_000_000,
            allow_hidden=True,
        )
        if isinstance(snapshot, policy_files.FileResult):
            raise SourceError(f"Cannot retain build source {name}: {snapshot.error}")
        data = snapshot.data
        if hashlib.sha256(data).hexdigest() != expected:
            raise SourceError(f"Build source changed since compilation: {name}")
        if original.resolve().is_relative_to(root):
            relative = original.resolve().relative_to(root).as_posix()
        else:
            relative = (
                f"sources/{manifest_hash}/{original.resolve().relative_to(common).as_posix()}"
            )
        if not policy_files.source_path(relative) or relative.split("/")[0] == "versions":
            raise SourceError(f"Build source must have a visible workspace-relative path: {name}")
        target = root / relative
        for ancestor in (target, *target.parents):
            if ancestor == root:
                break
            if ancestor.is_symlink():
                raise SourceError("Retained source paths cannot contain symlinks")
        if not target.exists():
            write_file(root, relative, data)
        mapped[name] = relative
        result[relative] = expected
    if source_path not in mapped:
        raise SourceError("The build's primary source must be included in its source hashes")
    return mapped[source_path], result
