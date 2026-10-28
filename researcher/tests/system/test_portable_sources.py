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
