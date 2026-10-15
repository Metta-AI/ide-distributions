"""A scripted stand-in for ``subprocess.run`` that replays recorded CLI responses."""

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ide_research_core.softmax_tool.platform_cli import DEFAULT_SERVER

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "softmax"
RECORDED = FIXTURES / "recorded"
SYNTHETIC = FIXTURES / "synthetic"

PLAYER_ID = "ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1"
LEAGUE_ID = "league_3c60897b-25cf-4b37-9d1a-8554c1198f28"
TOKEN = "ply_secret_token_value_that_must_never_be_logged_0001"
AGENT_TOKEN = "cga_secret_agent_token_that_must_never_be_logged_0001"

Response = subprocess.CompletedProcess | Callable[[list[str], dict], subprocess.CompletedProcess]


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def recorded(name: str) -> str:
    return (RECORDED / name).read_text()


def synthetic(name: str) -> str:
    return (SYNTHETIC / name).read_text()


class ServerOptionMissing(BaseException):
    """A CLI call without the explicit ``--server`` (see ``FakeRunner``)."""


class FakeRunner:
    """Answers ``run(argv, **kwargs)`` by the first registered argv prefix that matches.

    A prefix is the tool name (``softmax``/``coworld``) followed by leading arguments. Every call is
    recorded with its argv (tool name instead of the full executable path) and keyword arguments.

    Every call must end with ``--server <server>`` (``server``: the default API server unless a
    test sets it), or the runner raises ``ServerOptionMissing``; so every test checks that each
    CLI call names its server explicitly. That is a ``BaseException``, so code under test that
    catches ``Exception`` (a command's broad error handling) cannot swallow it. The
    recorded argv leaves that pair out, to keep assertions short.
    """

    def __init__(self, server: str = DEFAULT_SERVER):
        self.server = server
        self.responses: list[tuple[tuple[str, ...], Response]] = []
        self.calls: list[tuple[list[str], dict]] = []

    def on(self, *prefix: str, response: Response):
        self.responses.insert(0, (prefix, response))
        return self

    def argvs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]

    def __call__(self, argv, **kwargs):
        server_option = ["--server", self.server]
        if list(argv[-2:]) != server_option:
            raise ServerOptionMissing(f"CLI call does not end with {server_option}: {argv}")
        short = [Path(argv[0]).name, *argv[1:-2]]
        self.calls.append((short, kwargs))
        for prefix, response in self.responses:
            if tuple(short[: len(prefix)]) == prefix:
                return response(short, kwargs) if callable(response) else response
        raise AssertionError(f"unexpected CLI call: {short}")


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner().on("softmax", "status", response=completed(recorded("softmax_status.txt")))


def credential_value(expires_at: datetime | None = None) -> str:
    expires_at = expires_at or datetime.now(UTC) + timedelta(hours=20)
    return json.dumps({"token": TOKEN, "expires_at": expires_at.isoformat()})


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    return {
        "HERMES_HOME": str(tmp_path / "hermes"),
        "SOFTMAX_PLAYER_CREDENTIAL": credential_value(),
        "RESEARCH_PLAYER_ID": PLAYER_ID,
        "RESEARCH_SOFTMAX": "real",
    }

