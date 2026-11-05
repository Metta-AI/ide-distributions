"""Probe native attachment ownership without the profile's content middleware.

This proves staging, native reference expansion and file-tool access. It does not
claim the hosted runs endpoint accepts OpenAI file parts or proves a model turn.
"""

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

from research_core import layout
from research_core.attachments import prepare
from research_core.messages import UserMessage


def test_public_message_schema_matches_pinned_hermes(hermes_bin):
    source = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [
            str(hermes_bin.parent / "python"),
            "-c",
            "import sys,json,importlib.metadata;sys.path.insert(0,sys.argv[1]);"
            "from research_core.messages import UserMessage;"
            "print(json.dumps({'sdk':importlib.metadata.version('openai'),'schema':UserMessage.model_json_schema()}))",
            source,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    native = json.loads(result.stdout)
    assert native["schema"] == UserMessage.model_json_schema(), native


@pytest.mark.parametrize("prefix", ["data:text/plain;base64,", ""])
def test_openai_file_parts_use_native_ingestion(hermes_bin, tmp_path, prefix):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("attachments:\n  storage: workspace\n")
    workspace = tmp_path / "workspace"
    env = {**os.environ, "HERMES_HOME": str(home), "HERMES_REAL_HOME": str(home)}
    message = UserMessage.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Read the attachment"},
                {
                    "type": "file",
                    "file": {
                        "filename": "notes with spaces.txt",
                        "file_data": prefix + base64.b64encode(b"retained-file-marker").decode(),
                    },
                },
            ],
        }
    )
    prepared = prepare(
        message,
        ws=layout.Workspace(workspace, tmp_path / "env"),
        env=env,
        hermes_bin=str(hermes_bin),
    )
    assert prepared.root["content"][0] == {"type": "text", "text": "Read the attachment"}
    assert "saved at:" in prepared.root["content"][1]["text"]
    assert next(workspace.rglob("notes with spaces.txt")).read_text() == "retained-file-marker"
    assert message.root["content"][1]["type"] == "file"


def test_openai_audio_uses_native_transcription(hermes_bin, tmp_path):
    # Root CI must not silently install a missing package that a read-only hosted image lacks.
    subprocess.run([str(hermes_bin.parent / "python"), "-c", "import faster_whisper"], check=True)
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "attachments:\n  storage: workspace\nstt:\n  local:\n    model: /opt/models/whisper-base\n"
    )
    audio = Path(__file__).resolve().parents[1] / "fixtures" / "attachments" / "voice.wav"
    message = UserMessage.model_validate(
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": base64.b64encode(audio.read_bytes()).decode(),
                        "format": "wav",
                    },
                }
            ],
        }
    )
    prepared = prepare(
        message,
        ws=layout.Workspace(tmp_path / "workspace", tmp_path / "env"),
        env={**os.environ, "HERMES_HOME": str(home), "HERMES_REAL_HOME": str(home)},
        hermes_bin=str(hermes_bin),
    )
    transcript = prepared.root["content"][-1]["text"].lower()
    assert "hello world" in transcript and "secret number" in transcript, prepared.root


def test_pinned_hermes_stages_and_reads_native_attachment(hermes_bin, tmp_path):
    home = tmp_path / "hermes"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text("attachments:\n  storage: workspace\n")
    script = r"""
import base64
import json
import os
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
assert target.is_relative_to(workspace), attachment
expanded = preprocess_context_references(
    "Read this attachment: " + attachment["ref_text"],
    cwd=workspace, context_length=32000,
)
assert not expanded.blocked, expanded
assert not expanded.warnings, expanded
assert marker in expanded.message, expanded
read = json.loads(read_file_tool(str(target)))
assert marker in json.dumps(read), read

# Images and PDFs use the native vision queue, rather than document-shaped prompt text.
png = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
image = call("image.attach_bytes", {
    "session_id": sid, "filename": "pixel.png",
    "content_base64": base64.b64encode(png).decode(),
})
assert image["attached"] and image["count"] == 1, image
assert Path(image["path"]).read_bytes() == png
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents 4 0 R >>",
    b"<< /Length 0 >>\nstream\n\nendstream",
]
pdf = bytearray(b"%PDF-1.4\n")
offsets = [0]
for number, obj in enumerate(objects, 1):
    offsets.append(len(pdf))
    pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
xref = len(pdf)
pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
for offset in offsets[1:]:
    pdf.extend(f"{offset:010d} 00000 n \n".encode())
pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
document = call("pdf.attach", {
    "session_id": sid, "filename": "one-page.pdf",
    "content_base64": base64.b64encode(pdf).decode(),
})
assert document["attached"] and document["pages_attached"] == 1, document
assert document["count"] == 2, document
assert Path(document["pages"][0]["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
assert call("session.close", {"session_id": sid})["closed"]
resumed = call("session.resume", {"session_id": stored, "lazy": True})
assert resumed["resumed"] == stored, resumed
history = call("session.history", {"session_id": resumed["session_id"]})
assert "Existing conversation stays intact." in json.dumps(history), history
assert target.read_text() == marker
assert call("session.close", {"session_id": resumed["session_id"]})["closed"]
print("PASS native attachment staging, reference expansion, file read and session resume")
# Hermes's server threads can keep a normal exit waiting until the timeout. Shutdown is not under
# test here, and a hung exit failed CI on main and on several branches.
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
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
    assert "PASS native attachment staging" in result.stdout + result.stderr
