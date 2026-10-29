"""Upload with the locked profile CLI and a real player session against a local platform stack.

Set RESEARCH_UPLOAD_SYSTEM_SERVER to the website API root. The normal local API must
accept USER debug credentials; the test creates its own player and logs in through HTTP.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
from ide_research_core.softmax_tool.client import SoftmaxClient
from ide_research_core.softmax_tool.identity import IdentityError


def test_upload_uses_workspace_player_with_installed_cli(tmp_path):
    server = os.environ.get("RESEARCH_UPLOAD_SYSTEM_SERVER")
    if not server:
        pytest.skip("Requires a real local platform API and website proxy")
    assert urlparse(server).hostname in {"127.0.0.1", "localhost"}
    owner = "profile-upload-" + uuid4().hex
    headers = {"Authorization": f"Bearer debug:{owner}:{owner}@example.com:false"}
    with httpx.Client(base_url=server + "/observatory", headers=headers, timeout=30) as api:
        created = api.post("/players", json={"name": owner})
        created.raise_for_status()
        player = created.json()["id"]
        login = api.post(f"/players/{player}/login")
        login.raise_for_status()
        session = login.json()
        client = SoftmaxClient(
            {
                "HERMES_HOME": str(tmp_path / "profile"),
                "RESEARCH_PLAYER_ID": player,
                "COGAMES_API_URL": server,
                "SOFTMAX_PLAYER_CREDENTIAL": json.dumps(
                    {"token": session["token"], "expires_at": session["expires_at"]}
                ),
            }
        )
        artifact = tmp_path / "policy.bas"
        artifact.write_text("10 PRINT 1\n")
        label = client.upload(artifact, owner, player_id=player)
        assert label == owner + ":v1"
        versions = api.get(
            "/stats/policy-versions", params={"name_exact": owner, "player_id": player}
        )
        versions.raise_for_status()
        assert len(versions.json()["entries"]) == 1
        with pytest.raises(IdentityError, match="workspace credential"):
            client.upload(artifact, owner + "-wrong-player", player_id="ply_" + str(uuid4()))
        absent = api.get("/stats/policy-versions", params={"name_exact": owner + "-wrong-player"})
        absent.raise_for_status()
        assert absent.json()["entries"] == []


@pytest.mark.parametrize("artifact_type", ["file", "oci-image"])
def test_publication_recovers_after_uploader_exit_without_source_checkout(tmp_path, artifact_type):
    """Real CLI/API upload commits, uploader dies, fresh runtime recovers and promotes once."""
    from ide_research_core.io import gitops
    from ide_research_core.softmax_tool.upload import artifact_sha256
    from ide_research_core.source_snapshots import stage_sources

    server = os.environ.get("RESEARCH_UPLOAD_SYSTEM_SERVER")
    if not server:
        pytest.skip("Requires a real local platform API and website proxy")
    assert urlparse(server).hostname in {"127.0.0.1", "localhost"}
    base_image = os.environ.get("RESEARCH_UPLOAD_SYSTEM_BASE_IMAGE")
    if artifact_type == "oci-image" and not base_image:
        pytest.skip("Requires a cached native container base image")
    owner = "recovery-" + uuid4().hex
    headers = {"Authorization": f"Bearer debug:{owner}:{owner}@example.com:false"}
    with httpx.Client(base_url=server + "/observatory", headers=headers, timeout=30) as api:
        created = api.post("/players", json={"name": owner})
        created.raise_for_status()
        player = created.json()["id"]
        login = api.post(f"/players/{player}/login")
        login.raise_for_status()
        session = login.json()
        source = tmp_path / "source"
        source.mkdir()
        gitops.git(source, "init", "-q", "-b", "main")
        gitops.git(source, "config", "user.name", "Publication proof")
        gitops.git(source, "config", "user.email", "proof@example.com")
        artifact = source / "policy.bas"
        artifact.write_text("10 PRINT 1\n")
        inputs = {"policy.bas": artifact_sha256(artifact)}
        if artifact_type == "oci-image":
            dockerfile = source / "Dockerfile"
            dockerfile.write_text(
                f"FROM {base_image}\nCOPY policy.bas /policy.bas\n"
                "RUN test -s /policy.bas\n"
                'ENTRYPOINT ["/bin/cat", "/policy.bas"]\n'
            )
            inputs["Dockerfile"] = artifact_sha256(dockerfile)
            artifact = tmp_path / "policy-image.tar"
            image = owner + ":proof"
            subprocess.run(
                ["docker", "build", "--platform=linux/amd64", "-t", image, str(source)],
                check=True,
                capture_output=True,
            )
            try:
                subprocess.run(
                    ["docker", "save", "-o", str(artifact), image], check=True, capture_output=True
                )
            finally:
                subprocess.run(["docker", "image", "rm", image], check=True, capture_output=True)
        gitops.git(source, "add", ".")
        gitops.git(source, "commit", "-qm", "policy")
        storage = tmp_path / "policy-sources"
        snapshot = stage_sources(
            storage,
            source,
            commit=gitops.head(source),
            source_path="policy.bas",
            source_hashes=inputs,
        )
        env = dict(os.environ) | {
            "HERMES_HOME": str(tmp_path / "profile"),
            "RESEARCH_PLAYER_ID": player,
            "COGAMES_API_URL": server,
            "SOFTMAX_PLAYER_CREDENTIAL": json.dumps(
                {"token": session["token"], "expires_at": session["expires_at"]}
            ),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        }
        # Exit after the real upload succeeds, before the runtime receives its acknowledgement.
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os, sys
from pathlib import Path
from ide_research_core.publication import PublicationIntent, publish
from ide_research_core.softmax_tool.client import SoftmaxClient
from ide_research_core.softmax_tool.upload import artifact_sha256
class LostAcknowledgement(SoftmaxClient):
    def upload(self, *args, **kwargs):
        super().upload(*args, **kwargs)
        os._exit(23)
artifact = Path(sys.argv[2])
intent = PublicationIntent(player_id=os.environ['RESEARCH_PLAYER_ID'], policy_name=sys.argv[3],
    artifact_type=sys.argv[5], artifact_sha256=artifact_sha256(artifact), source_snapshot=sys.argv[4])
publish(Path(sys.argv[1]), intent, artifact, LostAcknowledgement(os.environ))
""",
                str(storage),
                str(artifact),
                owner,
                snapshot.digest,
                artifact_type,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        (tmp_path / "uploader.stderr").write_text(child.stderr)
        assert child.returncode == 23, child.stderr
        (receipt_path,) = (storage / "operations").glob("*.json")
        pending = json.loads(receipt_path.read_text())
        assert pending["kind"] == "pending"
        shutil.rmtree(source)
        # The public recovery command needs neither the worker checkout nor artifact bytes.
        clone = tmp_path / "clone"
        clone.mkdir()
        gitops.git(clone, "init", "-q", "-b", "main")
        gitops.git(clone, "remote", "add", "origin", str(tmp_path / "canonical.git"))
        args = [
            sys.executable,
            "-m",
            "ide_research_core",
            "workspace",
            "publication",
            "--root",
            str(clone),
            "--operation-id",
            pending["operation_id"],
        ]
        result = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
        (recovered,) = json.loads(result.stdout)["data"]["publications"]
        assert recovered["kind"] == "completed"
        pointer = json.loads(
            (storage / "versions" / (recovered["policy"]["id"] + ".json")).read_text()
        )
        assert pointer["snapshot"] == snapshot.digest
        replay = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
        assert json.loads(replay.stdout) == json.loads(result.stdout)
        versions = api.get(
            "/stats/policy-versions", params={"name_exact": owner, "player_id": player}
        )
        versions.raise_for_status()
        assert len(versions.json()["entries"]) == 1

        # Old completed builds remain publishable when their original source is gone.
        legacy = tmp_path / "legacy-build"
        legacy.mkdir()
        old_artifact = legacy / "policy.bas"
        old_artifact.write_text("10 PRINT 2\n")
        (legacy / "version.json").write_text(
            json.dumps(
                {
                    "artifact": "policy.bas",
                    "gates": {"build": {"passed": True}},
                    "strategy_sha256": "a" * 64,
                    "source_commit": snapshot.commit,
                    "source_path": str(source / "STRATEGY.md"),
                    "source_hashes": {str(source / "STRATEGY.md"): "a" * 64},
                }
            )
        )
        legacy_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ide_research_core",
                "softmax",
                "upload-build",
                "--name",
                "legacy-" + uuid4().hex[:8],
                "--build-dir",
                str(legacy),
            ],
            env=env | {"RESEARCH_WORKSPACE": str(clone), "HERMES_KANBAN_WORKSPACE": str(clone)},
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Source unavailable" in legacy_result.stderr
        legacy_policy = json.loads(legacy_result.stdout)["data"]["platform_policy_version_id"]
        legacy_versions = api.get(
            "/stats/policy-versions",
            params={"name_exact": legacy_policy.rsplit(":v", 1)[0], "player_id": player},
        )
        legacy_versions.raise_for_status()
        (legacy_entry,) = legacy_versions.json()["entries"]
        assert not (storage / "versions" / (legacy_entry["id"] + ".json")).exists()
