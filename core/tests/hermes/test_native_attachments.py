"""Probe native attachment ownership without the profile's content middleware.

This proves staging, native reference expansion and file-tool access. It does not
claim the hosted runs endpoint accepts OpenAI file parts or proves a model turn.
"""

import os
import subprocess


def test_pinned_hermes_stages_and_reads_native_attachment(hermes_bin, tmp_path):
    home = tmp_path / "hermes"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    script = r"""
import base64
import json
import sys
from pathlib import Path

from tui_gateway import server
from agent.context_references import preprocess_context_references
from tools.file_tools import read_file_tool

workspace = Path(sys.argv[1])

def call(method, params):
    response = server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    })
    assert response is not None, method
    assert "error" not in response, response
    return response["result"]

session = call("session.create", {
    "cwd": str(workspace),
    "messages": [{"role": "user", "content": "Existing conversation stays intact."}],
})
sid = session["session_id"]
stored = session["stored_session_id"]
marker = "native-attachment-proof-7419"
attachment = call("file.attach", {
    "session_id": sid,
    "name": "notes with spaces.txt",
    "data_url": "data:text/plain;base64," + base64.b64encode(marker.encode()).decode(),
})
target = Path(attachment["path"])
assert target.read_text() == marker, attachment
expanded = preprocess_context_references(
    "Read this attachment: " + attachment["ref_text"],
    cwd=workspace, context_length=32000,
)
assert not expanded.blocked, expanded
assert not expanded.warnings, expanded
assert marker in expanded.message, expanded
read = json.loads(read_file_tool(str(target)))
assert marker in json.dumps(read), read
assert call("session.close", {"session_id": sid})["closed"]
resumed = call("session.resume", {"session_id": stored, "lazy": True})
assert resumed["resumed"] == stored, resumed
history = call("session.history", {"session_id": resumed["session_id"]})
assert "Existing conversation stays intact." in json.dumps(history), history
assert target.read_text() == marker
assert call("session.close", {"session_id": resumed["session_id"]})["closed"]
print("PASS native attachment staging, reference expansion, file read and session resume")
"""
    result = subprocess.run(
        [str(hermes_bin.parent / "python"), "-c", script, str(workspace)],
        env={
            **os.environ,
            "HERMES_HOME": str(home),
            "HERMES_REAL_HOME": str(home),
            "OPENAI_API_KEY": "native-proof-not-a-real-key",
        },
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS native attachment staging" in result.stdout
