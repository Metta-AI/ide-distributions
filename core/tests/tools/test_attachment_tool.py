"""``attachment_download``: inline and url artifacts, verification, the capability report.

The fake platform has no artifact route, so these tests serve the artifact answers from a
``FakePlatform`` subclass installed through ``research_core.platform.from_env``; the url path
is a local ``http.server`` that records whether an Authorization header arrived.
"""

from __future__ import annotations

import base64
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import research_core.platform as platform_pkg
import research_core.tools.attachment_download as attachment_tool  # noqa: F401
from research_core.platform.errors import PlatformError
from research_core.platform.fake import FakePlatform
from research_core.store import capabilities

CONTENT = b"hello attachment\n" * 100
SHA = hashlib.sha256(CONTENT).hexdigest()


class ArtifactPlatform(FakePlatform):
    """The fake plus the cogents artifact route, answered from ``artifacts``."""

    artifacts: dict[str, dict] = {}

    def request(self, method, path, *, params=None, json=None, identity="player"):
        parts = path.strip("/").split("/")
        if method.upper() == "GET" and parts[:2] == ["v2", "cogents"] and parts[3] == "artifacts":
            artifact = self.artifacts.get(parts[4])
            if artifact is None:
                raise PlatformError("artifact not found", route=f"GET {path}", status=404)
            return dict(artifact)
        return super().request(method, path, params=params, json=json, identity=identity)


@pytest.fixture
def artifacts(monkeypatch):
    table: dict[str, dict] = {}
    monkeypatch.setattr(ArtifactPlatform, "artifacts", table)

    def from_env(env=None, workspace=None):
        from research_core import layout

        ws = workspace or layout.from_env(env)
        return ArtifactPlatform(ws.state / "fake_platform.json", env)

    monkeypatch.setattr(platform_pkg, "from_env", from_env)
    return table


@pytest.fixture
def served():
    """A loopback HTTP server: ``served.url`` serves ``served.body``; ``served.headers`` is a
    list of the request header maps it saw."""

    class State:
        body = CONTENT
        headers: list[dict] = []
        url = ""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            State.headers.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Length", str(len(State.body)))
            self.end_headers()
            self.wfile.write(State.body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    State.url = f"http://127.0.0.1:{server.server_address[1]}/blob"
    try:
        yield State
    finally:
        server.shutdown()
        server.server_close()


def inline(content: bytes, sha: str | None = None, size: int | None = None) -> dict:
    return {
        "sha256": sha or hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content) if size is None else size,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def test_orchestrator_only():
    assert attachment_tool.SPEC.roles == {"orchestrator"}
    assert attachment_tool.SPEC.executor == "workspace"


def test_researcher_has_no_attachment_tool(call):
    result = call("attachment_download", {"sha256": SHA}, role="researcher")
    assert result["ok"] is False and "unknown tool" in result["error"]


def test_inline_artifact_is_stored_once(call, ctx_factory, artifacts):
    artifacts[SHA] = inline(CONTENT)
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is True
    path = ctx_factory().coworld().documents / "attachments" / SHA
    assert result == {
        "ok": True,
        "path": str(path),
        "sha256": SHA,
        "size_bytes": len(CONTENT),
        "existing": False,
    }
    assert path.read_bytes() == CONTENT
    assert (path.stat().st_mode & 0o777) == 0o600

    artifacts.clear()  # the second call does not need the platform
    again = call("attachment_download", {"sha256": SHA.upper()})
    assert again["existing"] is True and again["path"] == str(path)


def test_url_artifact_is_fetched_without_authorization(call, artifacts, served):
    artifacts[SHA] = {"sha256": SHA, "size_bytes": len(CONTENT), "url": served.url}
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is True and result["size_bytes"] == len(CONTENT)
    (headers,) = served.headers
    sent = {name.lower(): value for name, value in headers.items()}
    assert "authorization" not in sent
    assert sent["user-agent"] == attachment_tool.USER_AGENT


def test_hash_mismatch_is_refused_and_nothing_written(call, ctx_factory, artifacts):
    artifacts[SHA] = inline(b"not the same bytes", sha=SHA)
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is False and "verification failed" in result["error"]
    assert result["context"]["expected"]["sha256"] == SHA
    assert not (ctx_factory().coworld().documents / "attachments" / SHA).exists()


def test_size_mismatch_is_refused(call, artifacts, served):
    served.body = CONTENT + b"extra"
    artifacts[SHA] = {"sha256": SHA, "size_bytes": len(CONTENT), "url": served.url}
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is False and "verification failed" in result["error"]
    assert result["context"]["got"]["size_bytes"] == len(CONTENT) + 1


def test_platform_returning_another_attachment_is_refused(call, artifacts):
    other = b"other"
    artifacts[SHA] = inline(other)
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is False and "different attachment" in result["error"]


def test_plain_http_url_is_refused(call, artifacts):
    artifacts[SHA] = {"sha256": SHA, "size_bytes": len(CONTENT), "url": "http://example.com/x"}
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is False and "https" in result["error"]


def test_bad_sha_argument(call):
    result = call("attachment_download", {"sha256": "xyz"})
    assert result["ok"] is False and "64-character" in result["error"]


def test_missing_artifact_reports_the_capability(call, ctx_factory, artifacts):
    result = call("attachment_download", {"sha256": SHA})
    assert result["ok"] is False
    assert result["context"]["status"] == 404
    assert result["context"]["capability"] == "attachments"
    record = capabilities.read(ctx_factory().store, "attachments")
    assert record["status"] == "broken"
    assert record["command"] == f"GET /v2/cogents/ply_fake/artifacts/{SHA}"
