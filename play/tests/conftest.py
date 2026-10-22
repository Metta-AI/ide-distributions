import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from ide_research_core.softmax_tool.client import SoftmaxClient
from play.games import GOTA
from play.program import Program

PLAYER = "ply_test"
STARTER = b"10 REM starter\n20 GOTO 10\n"
STARTER_SHA = hashlib.sha256(STARTER).hexdigest()
PERMIT_STDERR = (
    "Error: Access denied (403) for /v2/experience-requests. "
    '{"detail": {"type": "permit_required", "message": "Your coach asks first.", '
    '"reason": "the ask waits for the coach", "ask": {"key": "ask_1"}}}. Ask your coach.\n'
)
LEAGUE_STDERR = (
    "Error: Access denied (403): Your coach has not chosen a league for this cogent yet; "
    "ask them to choose one.\n"
)
CREDIT_STDERR = "Error: Request failed (402) for /v2/experience-requests: no credits left\n"


class FakeCli:
    """The pinned coworld/softmax CLIs, as ``PlatformCli`` runs them: argv in, stdout out."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.player = PLAYER
        self.create = ("ok", None)  # or ("refuse", stderr)
        self.submit = ("ok", None)
        self.submissions: list[dict] = []
        self.xp_status = "completed"

    def __call__(self, argv, input=None, cwd=None, **kwargs):
        tool, args = Path(argv[0]).name, argv[1:-2]  # drop "--server <server>"
        assert argv[-2] == "--server"
        assert (Path(cwd) / ".softmax/credentials.yaml").is_file()
        self.calls.append([tool, *args])
        self.inputs.append(input)

        def done(stdout="", returncode=0, stderr=""):
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        match [tool, *args]:
            case ["softmax", "status"]:
                return done(f"subject_type: player\nsubject_id: {self.player}\n")
            case ["coworld", "leagues", league, "--json"]:
                assert league == GOTA.league_id
                return done(json.dumps({"game": {"coworld_id": "cow_gota"}}))
            case ["coworld", "show", "cow_gota", "--json"]:
                players = [
                    {"id": "image-player", "image": "ghcr.io/x"},
                    {"id": "baseline", "name": "Baseline", "file": f"sha256:{STARTER_SHA}"},
                    {"id": "other", "file": "sha256:" + "0" * 64},
                ]
                return done(json.dumps({"id": "cow_gota", "manifest": {"player": players}}))
            case ["coworld", "upload-policy", "--file", _, "--name", name]:
                return done(f"Uploading...\nUpload complete: {name}:v3\n")
            case ["coworld", "xp-request", "create", "-", "--json"]:
                kind, stderr = self.create
                if kind == "refuse":
                    return done(returncode=1, stderr=stderr)
                return done(json.dumps({"id": "xreq_1"}))
            case ["coworld", "xp-request", "get", request_id, "--json"]:
                return done(
                    json.dumps(
                        {
                            "id": request_id,
                            "status": self.xp_status,
                            "completed_count": 2,
                            "episode_count": 2,
                            "episodes": [
                                {"id": "ereq_1", "episode_id": "ep-1"},
                                {"id": "ereq_2", "episode_id": None},
                            ],
                        }
                    )
                )
            case ["coworld", "xp-request", "download", _, *rest]:
                dest = Path(rest[rest.index("-d") + 1])
                data = b'{"scores": [1, 2]}'
                path = dest / "abc/results.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                receipt = {
                    "kind": "artifact",
                    "state": "completed",
                    "artifact_id": "a1",
                    "path": "abc/results.json",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                rows = [receipt]
                if getattr(self, "replay_state", None):
                    rows.append(
                        {"kind": "artifact", "state": self.replay_state, "artifact_id": "r1"}
                    )
                (dest / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
                return done()
            case ["coworld", "submissions", "--player", player, "--json"]:
                assert player == PLAYER
                return done(json.dumps({"entries": self.submissions}))
            case ["coworld", "submit", _, "--league", _, "--player", _, "--no-open-browser"]:
                kind, stderr = self.submit
                if kind == "refuse":
                    return done(returncode=1, stderr=stderr)
                return done("Submission: sub_1\n")
        raise AssertionError(f"unexpected CLI call {[tool, *args]}")

    def mutations(self):
        return [
            c for c in self.calls if c[1] in ("upload-policy", "submit") or c[2:3] == ["create"]
        ]


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "host/profiles/play"
    home.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return {
        "PATH": os.environ["PATH"],
        "HERMES_HOME": str(home),
        "PLAY_WORKSPACE": str(workspace),
        "RESEARCH_PLAYER_ID": PLAYER,
        "SOFTMAX_PLAYER_CREDENTIAL": "cga_fake",
        "COGAMES_API_URL": "https://example.invalid/api",
    }


@pytest.fixture
def cli():
    return FakeCli()


@pytest.fixture
def fetched():
    return []


@pytest.fixture
def program(env, cli, fetched, tmp_path):
    def fetch(url):
        fetched.append(url)
        return STARTER

    client = SoftmaxClient(env, run=cli, bin_dir=tmp_path / "bin")
    return Program(env, client=client, fetch=fetch)
