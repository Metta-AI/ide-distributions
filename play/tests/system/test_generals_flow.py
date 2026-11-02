"""Real Play -> local Observatory -> Generals BASIC workflow.

Opt in with GENERALS_PLAY_SYSTEM=1 and an isolated, already-uploaded local
Generals league whose default variant has a short smoke-test turn cap. Set
GENERALS_PLAY_LEAGUE, SOFTMAX_PLAYER_CREDENTIAL and RESEARCH_PLAYER_ID for a
new test-owned Player, and COGAMES_API_URL to the local frontend proxy.
No league submission or production credential discovery occurs here.
"""

import os
import time
import uuid
from urllib.parse import urlsplit

import pytest

from play.__main__ import run
from play.journal import Journal
from play.program import Program

pytestmark = pytest.mark.skipif(
    os.environ.get("GENERALS_PLAY_SYSTEM") != "1", reason="requires an isolated local hosted stack"
)


def test_generals_basic_writes_uploads_and_finishes_real_practice(tmp_path):
    assert urlsplit(os.environ["COGAMES_API_URL"]).hostname in ("localhost", "127.0.0.1")
    league = os.environ["GENERALS_PLAY_LEAGUE"]
    program = Program(
        {
            **os.environ,
            "PLAY_WORKSPACE": str(tmp_path / "workspace"),
            "HERMES_HOME": str(tmp_path / "profile"),
        }
    )
    starter = run("starter_fetch", {"league": league}, program)
    assert starter["ok"], starter
    assert starter["data"]["game"] == "generals"
    shown = run("program_show", {}, program)
    assert shown["ok"], shown
    source = shown["data"]["text"] + "\n' Play system workflow: preserve frontier strategy.\n"
    changed = run("program_change", {"text": source, "change": "first Generals program"}, program)
    assert changed["ok"], changed
    uploaded = run("program_upload", {"name": "play-generals-" + uuid.uuid4().hex[:12]}, program)
    assert uploaded["ok"], uploaded
    request = run("practice_request", {"league": league, "episodes": 1}, program)
    assert request["ok"] and request["data"]["outcome"] == "requested", request
    request_id = request["data"]["request_id"]
    deadline = time.monotonic() + 300
    while True:
        status = run("practice_status", {"request_id": request_id}, program)
        assert status["ok"], status
        if "summary" in status["data"]:
            break
        assert time.monotonic() < deadline, status
        time.sleep(2)
    result = status["data"]
    assert result["summary"]["games"] == 1 and result["summary"]["failed"] == 0, result
    assert result["games"][0]["your_timeouts"] == 0, result
    assert "average_score" in result["summary"] and "average_xp" not in result["summary"]
    assert result["games"][0]["watch_url"], result
    journal = Journal.load(program.workspace)
    assert journal.latest().practices[0].request_id == request_id
    refused = run(
        "program_release",
        {"program": uploaded["data"]["program"], "league": league, "user_said_yes": False},
        program,
    )
    assert not refused["ok"] and refused["error"]["reason"] == "bad_arguments", refused
    assert Journal.load(program.workspace).latest().release is None
