"""Descriptor-bound replacement of a validated source file."""

import os
from pathlib import Path
from uuid import uuid4


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


def write_file(root: Path, relative: str, content: bytes | None) -> None:
    """Publish without following links, including swapped ancestors or existing hardlinks."""
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
        name = parts[-1]
        exists = name in os.listdir(descriptor)
        if content is None:
            if exists:
                os.unlink(name, dir_fd=descriptor)
        else:
            mode = (
                os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode & 0o777
                if exists
                else 0o644
            )
            replace_file(descriptor, name, content, mode)
    finally:
        os.close(descriptor)
