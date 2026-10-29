"""Source editing survives relocation without platform conversation records or lost publication bytes."""

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def test_published_project_remains_editable_after_relocation(tmp_path):
    profile = tmp_path.resolve() / "original"
    profile.mkdir()
    clone = profile / "workspace"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RESEARCH_", "HERMES_"))
    } | {
        "HERMES_HOME": str(profile),
        "RESEARCH_CANONICAL": str(profile / "canonical.git"),
        "RESEARCH_WORKSPACE": str(clone),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }

    def cli(operation, payload=None):
        process = subprocess.run(
            [sys.executable, "-m", "ide_research_core", "workspace", operation],
            input=json.dumps(payload) if payload is not None else None,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        envelope = json.loads(process.stdout)
        assert envelope["ok"], envelope
        return envelope["data"]

    cli("bootstrap")
    project, version = str(uuid4()), str(uuid4())
    original = b"original published source\n"
    seed = {
        "action": "seed",
        "project_id": project,
        "league_id": "league_" + str(uuid4()),
        "conversation_id": "ide_" + str(uuid4()),
        "config": {
            "coworld_id": "cow_" + str(uuid4()),
            "manifest_hash": "sha256:" + "1" * 64,
            "starter_id": "starter",
            "policy_name": "portable-source",
            "runtime": "game-hosted",
        },
        "archive_base64": base64.b64encode(original).decode(),
        "archive_sha256": hashlib.sha256(original).hexdigest(),
        "request_id": "seed",
    }
    seeded = cli("project-source", seed)
    assert seeded["kind"] == "ok", seeded
    source = seeded["files"][0]
    # The external publication has the old revision while the user continues editing.
    edited = cli(
        "project-source",
        {
            "action": "write",
            "project_id": project,
            "league_id": seed["league_id"],
            "conversation_id": seed["conversation_id"],
            "path": source["path"],
            "source_revision": seeded["source_revision"],
            "base_sha256": source["sha256"],
            "content": "edited before registration\n",
            "request_id": "edit-before-registration",
        },
    )
    assert edited["kind"] == "ok", edited
    publication = {
        "action": "register_project",
        "policy_name": "portable-source",
        "version_id": version,
        "project_id": project,
        "base_sha256": seeded["source_revision"],
        "request_id": "publication",
    }
    registered = cli("policy-files", publication)
    assert registered["kind"] == "ok", registered
    relocated = tmp_path.resolve() / "relocated"
    profile.rename(relocated)
    clone = relocated / "workspace"
    env.update(
        HERMES_HOME=str(relocated),
        RESEARCH_CANONICAL=str(relocated / "canonical.git"),
        RESEARCH_WORKSPACE=str(clone),
    )
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", str(relocated / "canonical.git")],
        check=True,
    )
    assert cli("policy-files", publication) == registered
    discovered = cli(
        "policy-files",
        {"action": "sources", "policy_name": "portable-source", "version_id": version},
    )
    assert len(discovered["sources"]) == 1, discovered
    reference = discovered["sources"][0]
    read = {
        "action": "read",
        "policy_name": "portable-source",
        "version_id": version,
        "record_id": reference["record_id"],
        "path": reference["files"][0],
    }
    current = cli("policy-files", read)
    assert current["content"] == "edited before registration\n", current
    write = {
        **read,
        "action": "write",
        "content": "edited after relocation\n",
        "base_sha256": current["sha256"],
        "request_id": "edit-after-relocation",
    }
    saved = cli("policy-files", write)
    assert saved["kind"] == "ok" and cli("policy-files", write) == saved
    assert cli("policy-files", read)["content"] == "edited after relocation\n"
    subprocess.run(["git", "-C", str(clone), "gc", "--prune=now"], check=True)
    immutable = subprocess.check_output(
        ["git", "-C", str(clone), "show", reference["published_commit"] + ":" + read["path"]]
    )
    assert immutable == original


def test_completed_build_can_move_before_staging(tmp_path):
    profile = tmp_path.resolve() / "before"
    profile.mkdir()
    clone = profile / "workspace"
    distribution = Path(__file__).resolve().parents[2]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RESEARCH_", "HERMES_"))
    } | {
        "HERMES_HOME": str(profile),
        "RESEARCH_CANONICAL": str(profile / "canonical.git"),
        "RESEARCH_WORKSPACE": str(clone),
        "PYTHONPATH": str(distribution / "src"),
    }
    subprocess.run(
        [sys.executable, "-m", "ide_research_core", "workspace", "bootstrap"],
        env=env,
        capture_output=True,
        check=True,
    )
    candidate = clone / "candidate"
    candidate.mkdir()
    source = candidate / "STRATEGY.md"
    source.write_text("- aggression: 3\n")
    output = clone / "build-evidence/policy"
    subprocess.run(
        [
            sys.executable,
            str(distribution / "games/fakegame/tools/fake_build.py"),
            str(source),
            "--out",
            str(output),
            "--json",
        ],
        env=env,
        capture_output=True,
        check=True,
    )
    built = json.loads((output / "version.json").read_text())
    assert built["source_path"] == "STRATEGY.md"
    assert built["source_hashes"] == {
        "STRATEGY.md": hashlib.sha256(source.read_bytes()).hexdigest()
    }
    other = tmp_path / "same-input-in-another-directory"
    other.mkdir()
    (other / "STRATEGY.md").write_bytes(source.read_bytes())
    other_output = other / "build"
    subprocess.run(
        [
            sys.executable,
            str(distribution / "games/fakegame/tools/fake_build.py"),
            str(other / "STRATEGY.md"),
            "--out",
            str(other_output),
            "--json",
        ],
        env=env,
        capture_output=True,
        check=True,
    )
    rebuilt = json.loads((other_output / "version.json").read_text())
    assert rebuilt["source_root"] == built["source_root"], (
        "Source identity must not contain host paths"
    )
    assert rebuilt["source_hashes"] == built["source_hashes"]
    relocated = tmp_path.resolve() / "after"
    profile.rename(relocated)
    clone = relocated / "workspace"
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", str(relocated / "canonical.git")],
        check=True,
    )
    env.update(
        HERMES_HOME=str(relocated),
        RESEARCH_CANONICAL=str(relocated / "canonical.git"),
        RESEARCH_WORKSPACE=str(clone),
    )
    staged = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "workspace", "stage", "--worktree", str(clone)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(staged.stdout)["ok"], staged.stdout
    receipt = json.loads((clone / "build-evidence/policy/retained-sources.json").read_text())
    assert (
        subprocess.check_output(["git", "-C", str(clone), "show", "HEAD:" + receipt["source_path"]])
        == b"- aggression: 3\n"
    )


def test_immutable_publication_survives_repository_removal_and_volume_relocation(tmp_path):
    """Published source and edit retries survive losing the build checkout and process."""
    distribution = Path(__file__).resolve().parents[2]
    volume = tmp_path.resolve() / "volume"
    volume.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RESEARCH_", "HERMES_"))
    } | {
        "HERMES_HOME": str(volume / "profile"),
        "RESEARCH_CANONICAL": str(volume / "canonical.git"),
        "RESEARCH_WORKSPACE": str(volume / "workspace"),
        "PYTHONPATH": str(distribution / "src"),
    }

    def cli(operation, payload=None):
        result = subprocess.run(
            [sys.executable, "-m", "ide_research_core", "workspace", operation],
            input=json.dumps(payload) if payload is not None else None,
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        envelope = json.loads(result.stdout)
        assert envelope["ok"], envelope
        return envelope["data"]

    cli("bootstrap")
    repository = tmp_path / "nested/repository"
    repository.mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args]).decode().strip()

    git("init", "-q")
    git("config", "user.name", "Source proof")
    git("config", "user.email", "source@example.com")
    (repository / ".gitignore").write_text("input.bin\n")
    (repository / "player.py").write_text("original\n")
    git("add", ".")
    git("commit", "-qm", "source")
    (repository / "player.py").write_text("built dirty input\n")
    (repository / "input.bin").write_bytes(b"\xff\x00ignored")
    hashes = {
        name: hashlib.sha256((repository / name).read_bytes()).hexdigest()
        for name in ("player.py", "input.bin")
    }
    request = {
        "action": "stage",
        "repository": str(repository),
        "commit": git("rev-parse", "HEAD"),
        "source_path": "player.py",
        "source_hashes": hashes,
    }
    worker_guard = Path(env["HERMES_HOME"]) / "plugins/researcher_guard"
    worker_guard.mkdir(parents=True)
    staged = cli("source-snapshot", request)
    worker_guard.rmdir()
    assert cli("source-snapshot", request) == staged
    assert staged["ignored"] == ["input.bin"]
    assert not (
        volume / "policy-sources/staged" / staged["snapshot"] / "ignored/player.py"
    ).exists()
    version = str(uuid4())
    promotion = {
        "action": "promote",
        "snapshot": staged["snapshot"],
        "version_id": version,
        "policy_name": "retained",
        "artifact_sha256": "a" * 64,
    }
    promoted = cli("source-snapshot", promotion)
    import shutil

    shutil.rmtree(repository)
    relocated = tmp_path.resolve() / "replacement-volume-mount"
    volume.rename(relocated)
    env.update(
        HERMES_HOME=str(relocated / "profile"),
        RESEARCH_CANONICAL=str(relocated / "canonical.git"),
        RESEARCH_WORKSPACE=str(relocated / "workspace"),
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(relocated / "workspace"),
            "remote",
            "set-url",
            "origin",
            str(relocated / "canonical.git"),
        ],
        check=True,
    )
    assert cli("source-snapshot", promotion) == promoted
    found = cli(
        "policy-files", {"action": "sources", "policy_name": "retained", "version_id": version}
    )
    assert found["sources"][0]["record_id"] == version
    read = {
        "action": "read",
        "policy_name": "retained",
        "version_id": version,
        "record_id": version,
        "path": "player.py",
    }
    original = cli("policy-files", read)
    assert original["content"] == "built dirty input\n"
    assert cli("policy-files", {**read, "path": "input.bin"})["kind"] == "not_text"
    write = {
        **read,
        "action": "write",
        "content": "user edit\n",
        "base_sha256": original["sha256"],
        "request_id": "edit",
    }
    saved = cli("policy-files", write)
    assert saved["kind"] == "ok"
    assert cli("policy-files", write) == saved
    assert cli("policy-files", {**write, "request_id": "stale"})["kind"] == "conflict"
    assert cli("policy-files", read)["content"] == "user edit\n"
    immutable = subprocess.check_output(
        [
            "git",
            "-C",
            str(relocated / "policy-sources/objects.git"),
            "show",
            staged["commit"] + ":player.py",
        ]
    )
    assert immutable == b"built dirty input\n"
    rebuild = cli("source-snapshot", {"action": "stage_publication", "version_id": version})
    edited_input = subprocess.check_output(
        [
            "git",
            "-C",
            str(relocated / "policy-sources/objects.git"),
            "show",
            rebuild["commit"] + ":player.py",
        ]
    )
    assert edited_input == b"user edit\n"
    assert rebuild["snapshot"] != staged["snapshot"]
