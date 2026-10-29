"""``github``: the broker listing over a loopback server, clones from a local mirror
(``IDE_GITHUB_MIRROR``), the commit-keyed cache, and the capability report on failure."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import research_core.tools.github as github_tool  # noqa: F401  (registers the tool)
from research_core import gitops
from research_core.store import capabilities

TOKEN = "cga_test_token"


@pytest.fixture
def broker():
    """A loopback broker: ``/github/repositories?page=N`` answers JSON, page 9 answers 500."""

    class State:
        url = ""
        requests: list[tuple[str, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            State.requests.append((self.path, dict(self.headers)))
            if "page=9" in self.path:
                self.send_response(500)
                body = b"broker exploded"
            else:
                self.send_response(200)
                body = json.dumps(
                    {"repositories": [{"full_name": "acme/game"}], "nextPage": None}
                ).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    State.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield State
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def mirror(tmp_path):
    """``<mirror>/acme/game``: a repository with one committed file; returns (dir, sha)."""
    repo = gitops.init_repo(tmp_path / "mirror" / "acme" / "game")
    (repo / "README.md").write_text("# game\n")
    sha = gitops.commit_all(repo, "Add README")
    return tmp_path / "mirror", sha


def test_orchestrator_only(call):
    assert github_tool.SPEC.roles == {"orchestrator"}
    assert github_tool.SPEC.executor == "workspace"
    result = call("github", {"action": "repositories"}, role="researcher")
    assert result["ok"] is False and "unknown tool" in result["error"]


def test_repositories_through_the_broker_with_the_player_token(call, workspace_env, broker):
    workspace_env["IDE_GITHUB_BROKER_URL"] = broker.url
    workspace_env["SOFTMAX_PLAYER_CREDENTIAL"] = TOKEN
    result = call("github", {"action": "repositories", "page": 2})
    assert result["ok"] is True
    assert result["page"] == 2
    assert result["repositories"]["repositories"] == [{"full_name": "acme/game"}]
    (path, headers) = broker.requests[-1]
    assert path == "/github/repositories?page=2"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/json"


def test_broker_failure_marks_github_broken(call, ctx_factory, workspace_env, broker):
    workspace_env["IDE_GITHUB_BROKER_URL"] = broker.url
    workspace_env["SOFTMAX_PLAYER_CREDENTIAL"] = TOKEN
    result = call("github", {"action": "repositories", "page": 9})
    assert result["ok"] is False
    assert result["context"]["status"] == 500
    assert result["context"]["capability"] == "github"
    assert "broker exploded" in result["error"]
    record = capabilities.read(ctx_factory().store, "github")
    assert record["status"] == "broken"
    assert record["command"] == f"GET {broker.url}/github/repositories?page=9"


def test_broker_must_be_https_unless_loopback(call, workspace_env):
    workspace_env["IDE_GITHUB_BROKER_URL"] = "http://example.com"
    workspace_env["SOFTMAX_PLAYER_CREDENTIAL"] = TOKEN
    result = call("github", {"action": "repositories"})
    assert result["ok"] is False and "https" in result["error"]


def test_missing_player_credential_is_a_refusal(call, workspace_env, broker):
    workspace_env["IDE_GITHUB_BROKER_URL"] = broker.url
    result = call("github", {"action": "repositories"})
    assert result["ok"] is False and "Player identity not provisioned" in result["error"]


def test_clone_from_the_mirror_is_cached_by_commit(call, ctx_factory, workspace_env, mirror):
    mirror_dir, sha = mirror
    workspace_env["IDE_GITHUB_MIRROR"] = str(mirror_dir)
    repos = ctx_factory().coworld().documents / "repos"

    tip = call("github", {"action": "clone", "repository": "acme/game"})
    assert tip["ok"] is True
    assert tip["commit"] == sha and tip["cached"] is False
    assert tip["path"] == str(repos / f"acme-game-{sha[:7]}")
    assert tip["repository"] == "https://github.com/acme/game"
    assert (repos / f"acme-game-{sha[:7]}" / "README.md").read_text() == "# game\n"
    assert gitops.current_branch(repos / f"acme-game-{sha[:7]}") is None  # detached

    pinned = call("github", {"action": "clone", "repository": "acme/game", "commit": sha})
    assert pinned["cached"] is True and pinned["path"] == tip["path"]

    again = call("github", {"action": "clone", "repository": "https://github.com/acme/game.git"})
    assert again["cached"] is True and again["path"] == tip["path"]
    assert sorted(p.name for p in repos.iterdir()) == [f"acme-game-{sha[:7]}"]


def test_clone_a_later_commit_lands_in_its_own_checkout(call, ctx_factory, workspace_env, mirror):
    mirror_dir, first = mirror
    workspace_env["IDE_GITHUB_MIRROR"] = str(mirror_dir)
    repo = mirror_dir / "acme" / "game"
    (repo / "NOTES.md").write_text("later\n")
    second = gitops.commit_all(repo, "Add notes")

    result = call("github", {"action": "clone", "repository": "acme/game", "commit": second})
    assert result["ok"] is True and result["commit"] == second
    assert result["path"].endswith(f"acme-game-{second[:7]}")
    older = call("github", {"action": "clone", "repository": "acme/game", "commit": first})
    assert older["ok"] is True and older["commit"] == first
    assert older["path"].endswith(f"acme-game-{first[:7]}")


def test_clone_arguments_are_validated(call, workspace_env, mirror):
    workspace_env["IDE_GITHUB_MIRROR"] = str(mirror[0])
    bad_name = call("github", {"action": "clone", "repository": "not a repo"})
    assert bad_name["ok"] is False and "owner/name" in bad_name["error"]
    bad_sha = call("github", {"action": "clone", "repository": "acme/game", "commit": "abc"})
    assert bad_sha["ok"] is False and "40-character" in bad_sha["error"]


def test_clone_failure_reports_the_capability(call, ctx_factory, workspace_env, mirror):
    workspace_env["IDE_GITHUB_MIRROR"] = str(mirror[0])
    result = call("github", {"action": "clone", "repository": "acme/missing"})
    assert result["ok"] is False
    assert result["context"]["capability"] == "github"
    assert "cloning acme/missing failed" in result["error"]
    assert capabilities.read(ctx_factory().store, "github")["status"] == "broken"
    repos = ctx_factory().coworld().documents / "repos"
    assert not repos.exists() or list(repos.iterdir()) == []


def test_broker_clone_env_carries_the_header_once_and_never_to_git_config(
    ctx_factory, workspace_env
):
    workspace_env["SOFTMAX_PLAYER_CREDENTIAL"] = TOKEN
    workspace_env["COGAMES_API_URL"] = "https://softmax.test/api"
    source, env, token = github_tool._source(ctx_factory(), "acme/game")
    assert source == "https://softmax.test/api/github/repositories/acme/game/git"
    assert token == TOKEN
    keys = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert keys[f"http.{source}.extraHeader"] == f"Authorization: Bearer {TOKEN}"
    assert keys["http.followRedirects"] == "false"
    assert keys["credential.helper"] == ""
    assert env["GIT_LFS_SKIP_SMUDGE"] == "1"
