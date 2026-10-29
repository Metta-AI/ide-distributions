"""Upload with the locked profile CLI and a real player session against a local platform stack.

Set RESEARCH_UPLOAD_SYSTEM_SERVER to the website API root. The normal local API must
accept USER debug credentials; the test creates its own player and logs in through HTTP.
"""

import json
import os
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
