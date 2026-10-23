"""Real canonical source operations, including publication loss and native edits."""

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from ide_research_core import workspace
from ide_research_core.io import gitops
from ide_research_core.source_schema import HermesPolicyFiles, HermesProjectSource
from ide_research_core.workspace_sources import execute

DISTRIBUTION = Path(__file__).resolve().parents[2]


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "profile/workspace"
    bare = tmp_path / "canonical.git"
    workspace.bootstrap(root, bare, "fakegame", DISTRIBUTION)
    return root, bare


def seed_command(runtime="game-hosted"):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as files:
        files.writestr("player.py", "original\n")
        files.writestr("Dockerfile", "FROM scratch\n")
    data = archive.getvalue()
    return HermesProjectSource(
        action="seed",
        project_id=uuid4(),
        league_id="league_11111111-1111-1111-1111-111111111111",
        conversation_id="ide_11111111-1111-1111-1111-111111111111",
        archive_format="zip",
        archive_base64=base64.b64encode(data).decode(),
        archive_sha256=hashlib.sha256(data).hexdigest(),
        config={
            "coworld_id": "cow_11111111-1111-1111-1111-111111111111",
            "manifest_hash": "sha256:" + "a" * 64,
            "starter_id": "starter",
            "policy_name": "policy",
            "runtime": runtime,
        },
    )


@pytest.mark.parametrize("runtime", ["game-hosted", "platform-hosted"])
def test_project_patch_replay_and_native_edits_survive_reclone(source, tmp_path, runtime):
    root, bare = source
    seed = seed_command(runtime)
    created = execute(root, seed, "seed")
    assert created["kind"] == "ok"
    path = "player.py" if runtime == "platform-hosted" else "player/player.py"
    common = seed.model_dump(include={"project_id", "league_id", "conversation_id"})
    before = execute(root, HermesProjectSource(action="read", path=path, **common), "")
    selected = next(item for item in before["files"] if item["path"] == path)
    patch = HermesProjectSource(
        action="patch",
        path=path,
        source_revision=before["source_revision"],
        base_sha256=selected["sha256"],
        old_text="original",
        new_text="edited",
        **common,
    )
    saved = execute(root, patch, "edit")
    assert saved["kind"] == "ok" and saved["content"] == "edited\n"
    source_path = f"projects/{seed.project_id}/files/{path}"
    workspace.publish(root, {source_path: b"native edit\n"}, "native research")
    recovered = tmp_path / "recovered/workspace"
    workspace.bootstrap(recovered, bare, "fakegame", DISTRIBUTION)
    assert execute(recovered, patch, "edit") == saved
    assert (
        execute(recovered, HermesProjectSource(action="read", path=path, **common), "")["content"]
        == "native edit\n"
    )
    assert execute(recovered, patch, "stale")["kind"] == "conflict"
    with pytest.raises(workspace.WorkspaceError, match="already names"):
        execute(recovered, patch.model_copy(update={"new_text": "different"}), "edit")
    wrong = HermesProjectSource(
        action="read", **{**common, "league_id": "league_22222222-2222-2222-2222-222222222222"}
    )
    assert execute(recovered, wrong, "")["kind"] == "forbidden"


def test_rejected_publication_never_returns_or_replays_unpublished_source(source, monkeypatch):
    root, bare = source
    seed = seed_command()
    original = gitops.git(bare, "rev-parse", "main")
    monkeypatch.setattr(gitops, "push_with_rejections", lambda *_: (False, ["main"]))
    with pytest.raises(workspace.WorkspaceError, match="rejected"):
        execute(root, seed, "seed")
    assert gitops.git(bare, "rev-parse", "main") == original
    assert (
        gitops.show(
            bare, "main", f"control/ide/requests/{hashlib.sha256(b'seed').hexdigest()}.json"
        )
        is None
    )
    monkeypatch.undo()
    saved = execute(root, seed, "seed")
    assert saved["kind"] == "ok" and execute(root, seed, "seed") == saved


@pytest.mark.parametrize("source_path", ["src/player.py", "strategy/candidates/example/player.py"])
def test_policy_source_is_bound_to_upload_provenance_and_committed_content(source, source_path):
    root, _ = source
    workspace.publish(
        root,
        {
            "versions/V-123/version.json": json.dumps(
                {
                    "id": "V-123",
                    "policy_name": "policy",
                    "build": {
                        "source_path": source_path,
                        "source_hashes": {source_path: "a" * 64},
                    },
                }
            ).encode(),
            "versions/V-123/events/VE-123.json": json.dumps(
                {"version": "V-123", "kind": "uploaded", "data": {"policy_version_id": "policy:v1"}}
            ).encode(),
            source_path: b"original\n",
            "strategy/README.md": b"Strategy documentation\n",
            "strategy/candidates/manifest.json": b"{}\n",
        },
        "uploaded source",
    )
    command = HermesPolicyFiles(
        action="read",
        policy_name="policy",
        record_id="V-123",
        version_id="policy:v1",
        path=source_path,
    )
    before = execute(root, command, "")
    (root / source_path).write_text("unpublished\n")
    assert execute(root, command, "")["content"] == "original\n"
    write = command.model_copy(
        update={"action": "write", "content": "saved\n", "base_sha256": before["sha256"]}
    )
    saved = execute(root, write, "write")
    assert saved["kind"] == "ok"
    workspace.publish(root, {source_path: b"native\n"}, "native edit")
    assert execute(root, write, "write") == saved
    assert execute(root, command, "")["content"] == "native\n"
    assert (
        execute(root, command.model_copy(update={"version_id": "other"}), "")["kind"] == "not_found"
    )
    assert (
        execute(root, command.model_copy(update={"path": "versions/V-123/version.json"}), "")[
            "kind"
        ]
        == "forbidden"
    )


def test_publication_rejects_traversal_without_touching_outside(source, tmp_path):
    root, _ = source
    outside = root.parent / "outside"
    outside.write_text("keep")
    with pytest.raises(workspace.WorkspaceError, match="relative"):
        workspace.publish(root, {"../outside": b"bad"}, "unsafe")
    assert outside.read_text() == "keep"


@pytest.mark.parametrize("ancestor", ["projects", "sources", "sources/candidates"])
def test_source_rejects_committed_parent_symlink(source, tmp_path, ancestor):
    root, _ = source
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / ancestor
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    gitops.git(root, "add", ancestor)
    gitops.git(root, "commit", "-qm", "unsafe link")
    gitops.git(root, "push", "-q", "origin", "main")
    with pytest.raises(workspace.WorkspaceError, match="ancestors"):
        command = (
            seed_command()
            if ancestor == "projects"
            else HermesPolicyFiles(
                action="read",
                policy_name="policy",
                record_id="V-123",
                version_id="policy:v1",
                path="sources/candidates/example/player.py",
            )
        )
        execute(root, command, "unsafe-seed")
    assert list(outside.iterdir()) == []


def test_publication_does_not_modify_an_uncommitted_external_hardlink(source, tmp_path):
    import os

    root, _ = source
    workspace.publish(root, {"source.py": b"original"}, "initial")
    outside = tmp_path / "outside.py"
    outside.write_text("private")
    (root / "source.py").unlink()
    os.link(outside, root / "source.py")
    with pytest.raises(workspace.WorkspaceError, match="linked"):
        workspace.publish(root, {"source.py": b"edited"}, "edit")
    assert outside.read_text() == "private"


def test_project_inventory_tracks_native_file_additions_and_removals(source):
    root, _ = source
    seed = seed_command()
    original = execute(root, seed, "seed")
    common = seed.model_dump(include={"project_id", "league_id", "conversation_id"})
    new_path = f"projects/{seed.project_id}/files/player/helper.py"
    workspace.publish(root, {new_path: b"native helper\n"}, "add native source")
    read = HermesProjectSource(action="read", include_contents=True, **common)
    added = execute(root, read, "")
    assert added["source_revision"] != original["source_revision"]
    assert "player/helper.py" in added["project"]["files"]
    assert (
        next(file for file in added["files"] if file["path"] == "player/helper.py")["content"]
        == "native helper\n"
    )
    gitops.git(root, "rm", new_path)
    gitops.git(root, "commit", "-qm", "remove native source")
    gitops.git(root, "push", "-q", "origin", "main")
    removed = execute(root, read, "")
    assert removed["source_revision"] == original["source_revision"]
    assert "player/helper.py" not in removed["project"]["files"]
