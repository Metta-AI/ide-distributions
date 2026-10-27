from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from ide_research_core.softmax_tool.platform_cli import PlatformCli, SoftmaxCliError


def test_source_reads_are_byte_bounded_and_do_not_follow_redirects(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/credential-target")
                self.end_headers()
                return
            body = b"hello" if self.path == "/ok" else b"x" * 50
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cli = PlatformCli({
            "HERMES_HOME": str(tmp_path), "SOFTMAX_PLAYER_CREDENTIAL": "cga_test",
            "COGAMES_API_URL": f"http://127.0.0.1:{server.server_port}",
        })
        assert cli.api_get_bytes("/ok", max_bytes=5) == b"hello"
        with pytest.raises(SoftmaxCliError, match="exceeds"):
            cli.api_get_bytes("/large", max_bytes=5)
        with pytest.raises(SoftmaxCliError, match="HTTP 302"):
            cli.api_get_bytes("/redirect", max_bytes=5)
        assert [path for path, _ in requests] == ["/ok", "/large", "/redirect"]
        assert all(authorization == "Bearer cga_test" for _, authorization in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
