"""Copy the shared sources into each distribution that uses them.

Hermes installs a distribution by copying its one folder (``hermes profile install <dir>``),
so code and documents shared by several distributions must sit inside each folder. Edit the
source of truth, run this script, and commit the source and the copies together:

    python3 tools/sync_shared.py

Sources and their copies:

- ``shared/ide_research_core/`` -> ``<distribution>/src/ide_research_core/``
- ``ontology/ONTOLOGY.md``       -> ``<distribution>/ontology/ONTOLOGY.md``

A distribution takes part when its folder exists. Each copy of the package gets a
``GENERATED.md`` that says where to edit instead. Files in a package copy that no longer
exist in the source are removed, so a copy always equals its source.
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DISTRIBUTIONS = ("researcher", "orchestrator")
PACKAGE = "ide_research_core"
SKIPPED = {"__pycache__", ".pytest_cache", ".ruff_cache"}
MARKER = "GENERATED.md"
MARKER_TEXT = (
    "# Generated copy\n\n"
    f"This folder is a copy of `shared/{PACKAGE}/` made by `tools/sync_shared.py`.\n"
    "Edit the source under `shared/`, then run the script; changes made here are overwritten.\n"
)


def source_files(source: Path) -> dict[Path, Path]:
    """Relative path -> absolute path of every file to copy."""
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_file() and not SKIPPED.intersection(relative.parts):
            files[relative] = path
    return files


def mirror(source: Path, target: Path) -> list[str]:
    """Make ``target`` equal ``source`` plus the marker file; return what changed."""
    wanted = source_files(source)
    changes = []
    for relative, path in wanted.items():
        destination = target / relative
        if destination.is_file() and destination.read_bytes() == path.read_bytes():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        changes.append(f"wrote {destination.relative_to(REPO)}")
    marker = target / MARKER
    if not marker.is_file() or marker.read_text() != MARKER_TEXT:
        marker.write_text(MARKER_TEXT)
        changes.append(f"wrote {marker.relative_to(REPO)}")
    for path in sorted(target.rglob("*"), reverse=True):
        relative = path.relative_to(target)
        if SKIPPED.intersection(relative.parts):
            continue
        if path.is_file() and relative not in wanted and relative != Path(MARKER):
            path.unlink()
            changes.append(f"removed {path.relative_to(REPO)}")
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return changes


def copy_file(source: Path, target: Path) -> list[str]:
    if target.is_file() and target.read_bytes() == source.read_bytes():
        return []
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return [f"wrote {target.relative_to(REPO)}"]


def main() -> int:
    changes = []
    for name in DISTRIBUTIONS:
        distribution = REPO / name
        if not (distribution / "distribution.yaml").is_file():
            continue
        changes += mirror(REPO / "shared" / PACKAGE, distribution / "src" / PACKAGE)
        ontology = REPO / "ontology" / "ONTOLOGY.md"
        if ontology.is_file():
            changes += copy_file(ontology, distribution / "ontology" / "ONTOLOGY.md")
    print("\n".join(changes) if changes else "copies are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
