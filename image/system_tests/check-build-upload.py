"""Opt-in packaged BuildKit/archive/upload/recovery proof; creates one uniquely named policy.

Run inside the workspace image with BUILDKIT_HOST and a writable /workspace.
Supply SOFTMAX_PLAYER_CREDENTIAL and SOFTMAX_PLAYER_ID as JSON on stdin.
No evaluation, submission, workspace mutation, or existing policy edit is performed.
"""

import json
import os
import subprocess
import sys
from uuid import uuid4

import yaml

from research_core import gitops
from research_core.store import coworlds, intents
from research_core.store.actors import Actor
from research_core.tools.base import ToolContext
from research_core.tools.version import artifact_copy_path

os.environ.update(json.load(sys.stdin))
name = "container-build-proof-" + uuid4().hex[:12]
os.environ.update(IDE_WORKSPACE=f"/workspace/{name}", IDE_PLATFORM="real", IDE_ROLE="orchestrator")
ctx = ToolContext.from_env("orchestrator")
ctx.workspace.ensure_dirs()
coworld = ctx.workspace.coworld("container-proof")
coworld.ensure_dirs()
actor = Actor.parse("harness")
coworlds.register(ctx.wstore, actor, coworld.coworld_id, folder=coworld.folder_name)
coworlds.activate(ctx.wstore, actor, coworld.coworld_id)
code = coworld.code
(code / "context").mkdir(parents=True)
(code / "mixin").mkdir()
(code / ".gitignore").write_text("build/\n")
(code / "context/Dockerfile").write_text(
    'FROM busybox:1.37.0\nCOPY payload /payload\nRUN cat /payload > /built\nCMD ["cat", "/built"]\n'
)
(code / "context/payload").write_text("first image")
(code / "mixin/MIXIN.md").write_text(
    "---\n"
    + yaml.safe_dump(
        {
            "coworld": coworld.coworld_id,
            "coworld_version": "1",
            "commands": {
                "build": "coworld build-policy context --output build/player.tar",
                "check": "true",
                "evaluate": "true",
            },
            "artifact": {"kind": "image", "path": "build/player.tar", "policy_name": name},
            "metrics": [{"name": "score", "direction": "up", "primary": True}],
            "cost_per_episode": 0,
        }
    )
    + "---\n"
)
subprocess.run(["git", "init", "-q", "-b", "main", str(code)], check=True)
for key, value in (("user.name", "Container proof"), ("user.email", "proof@example.com")):
    subprocess.run(["git", "-C", str(code), "config", key, value], check=True)
gitops.commit_all(code, "Build fixture")


def tool(name, args):
    result = subprocess.run(
        ["research", "tool", name, "--args", json.dumps(args)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["ok"], payload
    return payload


first = tool("version", {"action": "create", "summary": "Packaged image build"})["record"]
normal = tool("upload", {"version": first["id"]})
assert normal["record"]["submission_status"] == "uploaded", normal
assert tool("upload", {"version": first["id"]})["already_uploaded"]
# Distinct bytes exercise a second version. Simulate losing the response after the real server
# commits: save the normal intent, call the real upload, and omit the caller's local completion.
(code / "context/payload").write_text("second image")
gitops.commit_all(code, "Change fixture payload")
second = tool("version", {"action": "create", "summary": "Lost response fixture"})["record"]
archive = artifact_copy_path(ctx, second["artifact"]["digest"])
with ctx.platform() as platform:
    inputs = platform.upload_inputs(name=name, artifact=archive, image_archive=True)
    intent = intents.begin(
        ctx.store,
        ctx.actor,
        kind="upload",
        key=second["artifact"]["digest"],
        subject=second["id"],
        request=inputs,
    )
    uploaded = platform.upload_image_policy(name=name, archive_path=archive)
    archive.unlink()
    recovered = tool("upload", {"version": second["id"]})
    assert recovered["reconciled"], recovered
    assert recovered["policy_version"]["id"] == uploaded.id
    assert tool("upload", {"version": second["id"]})["already_uploaded"]
    versions = platform.policy_versions(name=name)
    assert len(versions) == 2, versions
print(
    json.dumps(
        {
            "policy": name,
            "player": os.environ["SOFTMAX_PLAYER_ID"],
            "versions": [normal["policy_version"]["id"], uploaded.id],
            "image_client_hash": inputs["image_client_hash"],
            "archive_sha256": second["artifact"]["digest"],
            "recovered_without_archive": True,
        }
    )
)
