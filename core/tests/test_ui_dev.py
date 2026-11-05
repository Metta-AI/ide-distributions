"""``tools/ui_dev.py``, the page's model-free stack, run as a person runs it."""

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from research_core.client import ScriptedClient
from research_core.platform.fake import COWORLD_ID
from research_core.scripted_agent import REQUEST

REPO = Path(__file__).resolve().parents[2]
CONTEXT = {"contract_version": 1, "on_screen": []}


@pytest.fixture
def seam_url():
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    env.pop("HERMES_HOME", None)
    process = subprocess.Popen(
        [sys.executable, REPO / "tools" / "ui_dev.py", "--port", "0", "--tick-seconds", "0.2"],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    first_line = process.stdout.readline()
    url = re.match(r"client page:\s+(\S+?)/\?coworld=", first_line)
    assert url, first_line
    # Keep reading, so a full pipe can never stall the tool's prints.
    threading.Thread(target=process.stdout.read, daemon=True).start()
    yield url.group(1)
    process.terminate()
    process.wait(timeout=30)


def test_the_page_scenario_ends_in_a_submission(seam_url):
    """The submit scenario `docs/local-end-to-end.md` describes, on the URL the tool prints."""
    client = ScriptedClient(seam_url)
    client.open("test", "web-ide", session_context={"contract_version": 1, "coworld": COWORLD_ID})
    client.say(REQUEST, CONTEXT, wait=True)
    events = client.events(
        "coworld",
        0,
        idle=30.0,
        until=lambda event: event["kind"] == "version.submitted",
        coworld=COWORLD_ID,
    )
    assert events[-1]["kind"] == "version.submitted", events
    assert events[-1]["subject"] == "V-1"


def test_a_session_without_a_coworld_is_told_so_and_the_seam_lives(seam_url):
    client = ScriptedClient(seam_url)
    client.open("test", "web-ide", session_context={"contract_version": 1, "scope": "workspace"})
    reply = client.say(REQUEST, CONTEXT, wait=True)
    assert "no Coworld" in client.run(reply["run_id"])["output"]
    assert client.health()["ok"] is True
