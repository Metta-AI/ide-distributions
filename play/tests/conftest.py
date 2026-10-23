import hashlib
import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from ide_research_core.softmax_tool.client import SoftmaxClient
from play.games import GOTA
from play.program import Program
from play.records import CogentRecords

PLAYER = "ply_281ba4ec-d1a2-4e37-a609-a391d30421d2"  # a real player id's shape: ply_<uuid>
SERVER = "https://example.invalid/api"
# The real GotA starter: Metta-AI/polyworld coworld/gota/players/base.bas (origin/main, sha256
# 6b226d1d36475c7b5de8de602e6447bc0c175178830ed6a458c0997b9da8a1d8), as shipped.
STARTER = (Path(__file__).parent / "support" / "gota_base.bas").read_bytes()
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
# GotA results.json as the game writes it (researcher/tests/fixtures/softmax/recorded).
RED_WIN = {
    "scores": [116, 181, 405, 452, 156, 0, 0, 0, 0, 0],
    "ticks": 28877,
    "seed": 1853398679,
    "outcome": "RedTeam",
    "banked_gold": [],
    "returned": [],
    "total_xp": [2327, 3645, 8137, 9080, 3148, 5587, 5062, 3857, 2701, 4225],
}
TIME_LIMIT = {
    "scores": [0] * 10,
    "ticks": 31200,
    "seed": 2026,
    "outcome": "time_limit",
    "banked_gold": [],
    "returned": [],
    "total_xp": [3004, 2380, 3600, 3381, 2588, 2472, 3363, 3450, 2536, 1963],
}


class FakeCli:
    """The pinned coworld/softmax CLIs, as ``PlatformCli`` runs them: argv in, stdout out. It
    also answers the API reads the client makes (``http_get``) from the same state."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.player = PLAYER
        self.create = ("ok", None)  # or ("refuse", stderr)
        self.submit = ("ok", None)
        self.submissions: list[dict] = []
        self.memberships: list[dict] = []  # this league's active memberships, any player's
        self.retired: list[str] = []
        self.xp_status = "completed"
        # (episode request id, episode id, status, results or None, error)
        self.episodes = [
            ("ereq_1", "ep-1", "completed", RED_WIN, None),
            ("ereq_2", "ep-2", "completed", TIME_LIMIT, None),
        ]
        self.taken: set[str] = set()  # policy names other users own
        self.policies: list[dict] = []  # this player's policy versions: {"name", "version"}
        self.siblings: list[dict] = []  # versions of the same user's other players
        self.roster: list[dict] = []
        self.api_reads: list[str] = []
        self.requests = 0

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
                assert league in (GOTA.league_id, "league_00000000-0000-0000-0000-000000000001")
                return done(json.dumps({"game": {"coworld_id": "cow_gota"}}))
            case ["coworld", "show", "cow_gota", "--json"]:
                players = [
                    {"id": "image-player", "image": "ghcr.io/x"},
                    {"id": "baseline", "name": "Baseline", "file": f"sha256:{STARTER_SHA}"},
                    {"id": "other", "file": "sha256:" + "0" * 64},
                ]
                return done(json.dumps({"id": "cow_gota", "manifest": {"player": players}}))
            case ["coworld", "upload-policy", "--file", _, "--name", name, "--title", _]:
                if name in self.taken:
                    message = f"Policy name '{name}' is already taken by user 'u_x'. Please choose a different name."
                    return done(returncode=1, stderr=f"Error: Request failed (409): {message}\n")
                number = 1 + sum(p["name"] == name for p in self.policies + self.siblings)
                self.policies.append({"name": name, "version": number})
                return done(
                    f"Policy name: {name}\nUploading...\nUpload complete: {name}:v{number}\n"
                )
            case ["coworld", "xp-request", "create", "-", "--json"]:
                kind, stderr = self.create
                if kind == "refuse":
                    return done(returncode=1, stderr=stderr)
                self.roster = json.loads(input)["roster"]
                self.requests += 1
                return done(json.dumps({"id": f"xreq_{self.requests}"}))
            case ["coworld", "xp-request", "get", request_id, "--json"]:
                rows = [
                    {"id": ereq, "episode_id": episode, "status": status, "error": error}
                    for ereq, episode, status, _, error in self.episodes
                ]
                finished = sum(row["status"] == "completed" for row in rows)
                return done(
                    json.dumps(
                        {
                            "id": request_id,
                            "status": self.xp_status,
                            "completed_count": finished if self.xp_status == "completed" else 1,
                            "episode_count": len(rows),
                            "episodes": rows,
                            "requested": {"roster": self.roster},
                        }
                    )
                )
            case ["coworld", "xp-request", "download", _, *rest]:
                dest = Path(rest[rest.index("-d") + 1])
                rows = []
                for ereq, _, _, results, _ in self.episodes:
                    if results is None:
                        continue
                    data = json.dumps(results).encode()
                    path = f"{ereq}-results/results.json"
                    (dest / path).parent.mkdir(parents=True, exist_ok=True)
                    (dest / path).write_bytes(data)
                    rows.append(
                        {
                            "kind": "artifact",
                            "episode_request_id": ereq,
                            "state": "completed",
                            "artifact_id": f"{ereq}:results",
                            "filename": "results.json",
                            "path": path,
                            "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
                if getattr(self, "replay_state", None):
                    rows.append(
                        {
                            "kind": "artifact",
                            "state": self.replay_state,
                            "artifact_id": "r1",
                            "filename": "replay",
                        }
                    )
                (dest / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
                return done()
            case ["coworld", "submissions", "--player", player, "--json"]:
                assert player == PLAYER
                return done(json.dumps({"entries": self.submissions}))
            case [
                "coworld",
                "memberships",
                "--league",
                _,
                "--player",
                player,
                "--active-only",
                "--json",
            ]:
                assert player == PLAYER
                return done(json.dumps({"entries": self.memberships}))
            case ["coworld", "retire-membership", membership_id, "--reason", _]:
                self.retired.append(membership_id)
                return done()
            case ["coworld", "submit", _, "--league", _, "--player", _, "--no-open-browser"]:
                kind, stderr = self.submit
                if kind == "refuse":
                    return done(returncode=1, stderr=stderr)
                return done("Submission: sub_1\n")
        raise AssertionError(f"unexpected CLI call {[tool, *args]}")

    def http_get(self, url: str, headers: dict[str, str]) -> str:
        assert headers["Authorization"] == "Bearer cga_fake"
        parsed = urllib.parse.urlsplit(url)
        assert parsed.path == "/api/observatory/stats/policy-versions", url
        query = dict(urllib.parse.parse_qsl(parsed.query))
        assert query["mine"] == "true"
        self.api_reads.append(parsed.query)
        assert (
            query.get("player_id", PLAYER) == PLAYER
        )  # only this player's own player_id is asked for
        owned = self.policies if "player_id" in query else self.policies + self.siblings
        rows = [
            p
            for p in owned
            if p["name"] == query.get("name_exact", p["name"])
            and str(p["version"]) == query.get("version", str(p["version"]))
        ]
        return json.dumps({"entries": rows, "next_cursor": None})

    def mutations(self):
        return [
            c for c in self.calls if c[1] in ("upload-policy", "submit") or c[2:3] == ["create"]
        ]


class FakeRecords:
    """The cogent records API: ``GET``/``PUT .../cogents/<player>/c/<collection>/<key>``."""

    def __init__(self):
        self.records: dict[tuple[str, str], dict] = {}
        self.puts: list[dict] = []

    def __call__(self, method, url, headers, body):
        assert headers["Authorization"] == "Bearer cga_fake"
        prefix = f"{SERVER}/observatory/v2/cogents/{PLAYER}/c/"
        assert url.startswith(prefix), url
        collection, key = url.removeprefix(prefix).split("/", 1)
        live = self.records.get((collection, key))
        if method == "GET":
            return (200, json.dumps(live).encode()) if live else (404, b'{"detail": "not found"}')
        assert method == "PUT" and collection in ("tasks", "profile")
        payload = json.loads(body)
        assert payload["base_rev"] == (live["rev"] if live else None)
        rev = (live["rev"] if live else 0) + 1
        self.records[(collection, key)] = {"key": key, "rev": rev, "body": payload["body"]}
        self.puts.append({"key": key, **payload})
        return 200, b"{}"


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
        "COGAMES_API_URL": SERVER,
    }


@pytest.fixture
def cli():
    return FakeCli()


@pytest.fixture
def records():
    return FakeRecords()


@pytest.fixture
def fetched():
    return []


@pytest.fixture
def program(env, cli, records, fetched, tmp_path):
    def fetch(url):
        fetched.append(url)
        return STARTER

    client = SoftmaxClient(env, run=cli, bin_dir=tmp_path / "bin", http_get=cli.http_get)
    play = Program(
        env, client=client, fetch=fetch, records=CogentRecords(SERVER, PLAYER, "cga_fake", records)
    )
    play._sleep = lambda seconds: None
    return play
