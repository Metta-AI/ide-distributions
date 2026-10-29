import hashlib

import pytest

from ide_research_core.build_sources import SourceError, retain


def test_external_starter_is_retained_without_absolute_editor_paths(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    starter = tmp_path / "installed/game/player.bas"
    starter.parent.mkdir(parents=True)
    starter.write_text("original")
    hashes = {str(starter): hashlib.sha256(starter.read_bytes()).hexdigest()}
    primary, manifest = retain(root, str(starter), hashes, source_root=starter.parent)
    assert primary.startswith("sources/") and primary.endswith("/player.bas")
    assert manifest == {primary: hashes[str(starter)]}
    assert (root / primary).read_text() == "original"
    (root / primary).write_text("user edit")
    assert retain(root, str(starter), hashes, source_root=starter.parent) == (primary, manifest)
    assert (root / primary).read_text() == "user edit"
    starter.write_text("changed")
    with pytest.raises(SourceError, match="changed since compilation"):
        retain(root, str(starter), hashes, source_root=starter.parent)


def test_workspace_source_retains_an_editable_snapshot(tmp_path):
    source = tmp_path / "player.py"
    source.write_text("source")
    hashes = {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}
    primary, manifest = retain(tmp_path, str(source), hashes)
    assert primary.startswith("sources/") and primary.endswith("/player.py")
    assert manifest == {primary: hashes[str(source)]}
    assert (tmp_path / primary).read_text() == "source"


def test_retention_rejects_linked_destination(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sources").symlink_to(outside)
    source = tmp_path / "player.bas"
    source.write_text("source")
    with pytest.raises(SourceError, match="symlinks"):
        retain(
            root,
            str(source),
            {str(source): hashlib.sha256(source.read_bytes()).hexdigest()},
            source_root=source.parent,
        )
    assert list(outside.iterdir()) == []
