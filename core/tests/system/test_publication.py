"""Native cron -> installed CLI -> real smart HTTP Git: no lost retries or secret files.

The local broker stands in for GitHub and the platform's authenticated repository proxy.
No model, cloud resource, or GitHub account is used.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

CHECKOUT = Path(__file__).resolve().parents[3]


def command(*args, cwd=None, env=None, check=True):
    return subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, check=check, timeout=60
    )


@pytest.fixture
def publication(tmp_path):
    remote = tmp_path / "repo.git"
    command("git", "init", "--bare", str(remote))
    command("git", "--git-dir", str(remote), "config", "http.receivepack", "true")
    state = {"fail_push": False, "authorized": True}

    class Broker(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_git()

        def do_POST(self):
            self.handle_git()

        def handle_git(self):
            if not state["authorized"] or self.headers.get("Authorization") != "Bearer test-player":
                self.send_error(401)
                return
            if self.path == "/github/workspace-repository":
                body = json.dumps({"repository": "player/snapshot", "branch": "workspace"}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
                return
            route = urlsplit(self.path)
            operation = route.path.removeprefix("/github/repositories/player/snapshot/git/")
            if state["fail_push"] and operation == "git-receive-pack":
                self.send_error(503)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            result = subprocess.run(
                ["git", "http-backend"],
                input=body,
                capture_output=True,
                check=True,
                env={
                    **os.environ,
                    "GIT_PROJECT_ROOT": str(tmp_path),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "PATH_INFO": "/repo.git/" + operation,
                    "QUERY_STRING": route.query,
                    "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "REMOTE_USER": "player",
                },
                timeout=30,
            )
            headers, response = result.stdout.split(b"\r\n\r\n", 1)
            self.send_response(200)
            for line in headers.decode().splitlines():
                key, value = line.split(":", 1)
                self.send_header(key, value.strip())
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    workspace = tmp_path / "workspace"
    code = workspace / "game" / "code"
    code.mkdir(parents=True)
    (code.parent / ".state").mkdir()
    (code.parent / ".state" / "store.db").write_bytes(b"SQLite format 3\0")
    command("git", "init", str(code))
    (code / "policy.py").write_text("policy = 1\n")
    (code / ".gitignore").write_text("ignored-secret.txt\n")
    (code / "ignored-secret.txt").write_text("secret")
    (code / ".env").write_text("secret")
    (code / "db-with-no-extension").write_bytes(b"SQLite format 3\0" + b"secret")
    (code / "state.db-wal").write_text("secret")
    (code / "outside").symlink_to(tmp_path / "secret")
    (tmp_path / "secret").write_text("secret")
    profile = tmp_path / "profiles" / "orchestrator"
    profile.mkdir(parents=True)
    (profile / "SOUL.md").write_text("My soul\n")
    (profile / ".env").write_text("SOFTMAX_PLAYER_CREDENTIAL=test-player\n")
    (profile / "config.yaml").write_text("secret")
    env = {
        **os.environ,
        "IDE_WORKSPACE": str(workspace),
        "HERMES_HOME": str(profile),
        "IDE_WORKSPACE_ENV": str(Path(sys.executable).parent.parent),
        "IDE_GITHUB_BROKER_URL": f"http://127.0.0.1:{server.server_port}",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@localhost",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@localhost",
    }
    try:
        yield workspace, code, remote, state, env
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def tick(env, check=True):
    return command(
        sys.executable, str(CHECKOUT / "orchestrator/scripts/publication.py"), env=env, check=check
    )


def test_cron_publishes_private_files_and_preserves_pending_commit(publication):
    workspace, code, remote, state, env = publication
    tick(env)
    first = command("git", "--git-dir", str(remote), "rev-parse", "workspace").stdout.strip()
    paths = command(
        "git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "workspace"
    ).stdout.splitlines()
    assert paths == [
        "profiles/orchestrator/SOUL.md",
        "workspace/game/code/.gitignore",
        "workspace/game/code/policy.py",
    ]
    tick(env)
    assert (
        command("git", "--git-dir", str(remote), "rev-parse", "workspace").stdout.strip() == first
    )
    (code / "policy.py").write_text("policy = 2\n")
    state["fail_push"] = True
    assert tick(env, check=False).returncode != 0
    local = workspace / ".state/publication/repository.git"
    pending = command("git", "--git-dir", str(local), "rev-parse", "workspace").stdout.strip()
    assert pending != first
    assert (
        command("git", "--git-dir", str(remote), "rev-parse", "workspace").stdout.strip() == first
    )
    state["fail_push"] = False
    tick(env)
    assert (
        command("git", "--git-dir", str(remote), "rev-parse", "workspace").stdout.strip() == pending
    )
    assert "test-player" not in (local / "config").read_text()
    state["authorized"] = False
    assert tick(env, check=False).returncode != 0
    assert (
        command("git", "--git-dir", str(local), "rev-parse", "workspace").stdout.strip() == pending
    )


def test_workspace_tree_wins_without_discarding_remote_history(publication, tmp_path):
    _, code, remote, _, env = publication
    tick(env)
    external = tmp_path / "external"
    command("git", "clone", "-b", "workspace", str(remote), str(external))
    (external / "workspace/game/code/policy.py").write_text("external edit\n")
    command("git", "add", ".", cwd=external)
    command("git", "commit", "-m", "External edit", cwd=external, env=env)
    external_commit = command("git", "rev-parse", "HEAD", cwd=external).stdout.strip()
    command("git", "push", cwd=external)
    (code / "policy.py").unlink()
    tick(env)
    command(
        "git", "--git-dir", str(remote), "merge-base", "--is-ancestor", external_commit, "workspace"
    )
    assert (
        "policy.py"
        not in command(
            "git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "workspace"
        ).stdout
    )


def test_snapshot_includes_uncommitted_task_worktrees_without_git_metadata(publication):
    _, code, remote, _, env = publication
    command("git", "add", "policy.py", cwd=code)
    command("git", "commit", "-m", "Policy base", cwd=code, env=env)
    task = code.parent / "worktrees" / "task-1"
    command("git", "worktree", "add", "-b", "task-1", str(task), cwd=code)
    (task / "policy.py").write_text("task in progress\n")
    tick(env)
    assert (
        command(
            "git",
            "--git-dir",
            str(remote),
            "show",
            "workspace:workspace/game/worktrees/task-1/policy.py",
        ).stdout
        == "task in progress\n"
    )
    tree = command(
        "git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "workspace"
    ).stdout.splitlines()
    assert not any(Path(path).name == ".git" for path in tree)
