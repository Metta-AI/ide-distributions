"""Capture compiler inputs in the workspace's immutable source store."""

import json
import os
from pathlib import Path

from ide_research_core.source_io import SourceError


def capture(build_dir: Path, source_root: Path) -> dict:
    """Finish a build with self-contained inputs before its directory can be relocated."""
    receipt = build_dir / "version.json"
    build = json.loads(receipt.read_bytes())
    source_root = source_root.resolve()
    primary = Path(build["source_path"])
    primary = primary.relative_to(source_root) if primary.is_absolute() else primary
    # Installed compiler inputs are already recorded under compiler provenance. They are
    # not editable project files and must not contribute host paths to source identity.
    hashes = {}
    for name, digest in build["source_hashes"].items():
        path = Path(name)
        if path.is_absolute():
            if not path.is_relative_to(source_root):
                if build["compiler"]["installed_source_hashes"][name] != digest:
                    raise SourceError("Installed compiler provenance disagrees with build inputs")
                continue
            path = path.relative_to(source_root)
        hashes[path.as_posix()] = digest
    # HERMES_HOME identifies a profile, not a configured research workspace.
    # Standalone builds retain source only when workspace storage is configured.
    if "RESEARCH_CANONICAL" not in os.environ:
        build.update(source_path=primary.as_posix(), source_hashes=hashes)
        receipt.write_text(json.dumps(build, sort_keys=True, indent=2) + "\n")
        return build
    from ide_research_core.io import gitops
    from ide_research_core.source_snapshots import stage_sources

    repository = gitops._run(source_root, "rev-parse", "--show-toplevel", check=False)
    repo = (
        Path(repository.stdout.decode().strip()).resolve()
        if repository.returncode == 0
        else source_root
    )
    prefix = source_root.relative_to(repo)
    logical = {(prefix / name).as_posix(): digest for name, digest in hashes.items()}
    primary_name = (prefix / primary).as_posix()
    snapshot = stage_sources(
        Path(os.environ["RESEARCH_CANONICAL"]).resolve().parent / "policy-sources",
        repo,
        commit=gitops.head(repo) if repository.returncode == 0 else "",
        source_path=primary_name,
        source_hashes=logical,
    )
    build.pop("source_root", None)
    build.update(
        source_snapshot=snapshot.digest,
        source_commit=snapshot.commit,
        source_path=primary_name,
        source_hashes=logical,
    )
    receipt.write_text(json.dumps(build, sort_keys=True, indent=2) + "\n")
    return build
