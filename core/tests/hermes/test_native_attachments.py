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
    (home / "config.yaml").write_text("attachments:\n  storage: workspace\n")
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
