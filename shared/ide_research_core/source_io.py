"""Descriptor-bound replacement of a validated source file."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class SourceError(ValueError):
    pass


@contextmanager
def parent_directory(root: Path, relative: str) -> Iterator[tuple[int, str]]:
    """Create and open a source parent without following any directory links."""
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        parts = Path(relative).parts
        for part in parts[:-1]:
            if part not in os.listdir(descriptor):
                os.mkdir(part, dir_fd=descriptor)
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def replace_file(directory: int, name: str, content: bytes, mode: int) -> None:
    """Replace an authorized file through its already-validated parent descriptor."""
    temporary = ".source-" + uuid4().hex
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    os.fsync(directory)


def retain_file(
    root: Path, relative: str, source: BinaryIO, expected_sha256: str, mode: int
) -> None:
    """Stream and hash a build input before exposing it; never overwrite retained edits."""
    with parent_directory(root, relative) as (directory, name):
        temporary = ".source-" + uuid4().hex
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as target:
                os.fchmod(target.fileno(), mode)
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
                if digest.hexdigest() != expected_sha256:
                    raise SourceError(
                        f"Build source changed since compilation: {relative}"
                    )
                target.flush()
                os.fsync(target.fileno())
            if name in os.listdir(directory):
                existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                    raise SourceError(
                        "Retained source must be a regular, unlinked file"
                    )
            else:
                # Linking rather than replacing cannot overwrite a concurrent user edit.
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)


def write_file(root: Path, relative: str, content: bytes | None) -> None:
    """Publish without following links, including swapped ancestors or existing hardlinks."""
    with parent_directory(root, relative) as (directory, name):
        exists = name in os.listdir(directory)
        if content is None:
            if exists:
                os.unlink(name, dir_fd=directory)
        else:
            mode = (
                os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode & 0o777
                if exists
                else 0o644
            )
            replace_file(directory, name, content, mode)
