"""Atomic reads and writes of Markdown-with-front-matter and JSON files."""

import json
import os
import tempfile
from pathlib import Path

import frontmatter


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    _atomic_write_text(Path(path), text)


def read_doc(path: Path) -> tuple[dict, str]:
    post = frontmatter.loads(Path(path).read_text(encoding="utf-8"))
    return dict(post.metadata), post.content


def write_doc(path: Path, meta: dict, body: str) -> None:
    _atomic_write_text(Path(path), frontmatter.dumps(frontmatter.Post(body, **meta)) + "\n")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    _atomic_write_text(Path(path), json.dumps(data, indent=2, sort_keys=True) + "\n")
