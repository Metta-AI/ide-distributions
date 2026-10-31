"""The acceptance scenario on a real Hermes gateway with a real model (plan Task 6.4, the
local stand-in for the release canary).

Both profiles are installed into a temporary home and prepared; ``hermes gateway run`` serves
them with one API key; the fake platform's manifest for ``cow_fake`` points at the fakegame
fixture repository; the seam server runs in this process against the real gateway; the watcher
ticks run in this process as the cron job would. "Submit the starter as my champion" on the new
workspace must end in a submission on the fake platform within ``SCENARIO_TIMEOUT_S``.

Needs ``HERMES_BIN`` and ``OPENROUTER_API_KEY`` (see ``test_spike_s1.py``). Run with::

    HERMES_BIN=<hermes> OPENROUTER_API_KEY=<key> \\
        uv run pytest -m hermes core/tests/hermes/test_seam_real_hermes.py -s

The test prints where the gateway log, the profile homes and the workspace are, so a failed
run can be read.

The same harness is the local end-to-end setup of ``docs/local-end-to-end.md``. Four knobs, all
off by default so CI is unchanged:

* ``IDE_E2E_KEEP=1`` keeps the gateway and the seam server up after the scenario (pass or
  fail), prints the URLs and ready-to-paste ``curl`` lines, ticks the watcher every minute as
  the cron job would, and stops both on Ctrl-C;
* ``IDE_E2E_SCENARIO=0`` sends no request (with ``IDE_E2E_KEEP=1``: an empty workspace to talk
  to);
* ``IDE_E2E_PLATFORM=real`` runs against the real Softmax platform with the caller's
  ``SOFTMAX_PLAYER_ID``, ``SOFTMAX_PLAYER_CREDENTIAL``, ``COGAMES_API_URL`` and
  ``IDE_DEFAULT_COWORLD`` written into every ``.env``; the scenario then waits for a submission
  by that Player that did not exist when it started;
* ``IDE_E2E_TIMEOUT_S`` replaces the scenario timeout (a real build takes longer than the fake).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from research_core import layout
from research_core.client import ScriptedClient
from research_core.platform.fake import FakePlatform
from research_core.serve import SeamServer
from research_core.store import coworlds, notices
from research_core.store.db import Store
from research_core.watcher import context as watcher_context
from research_core.watcher import tick as watcher_tick
from research_core.watcher.hermes_api import HermesRuns, mint_session_id
from tests.fixtures.fakegame_repo import COWORLD_ID, make_fakegame_repo
from tests.hermes.test_spike_s1 import REPO, Host, free_port, hermes_python, openrouter_key, run

REQUEST = "Submit the starter as my champion."
SCENARIO_TIMEOUT_S = int(os.environ.get("IDE_E2E_TIMEOUT_S") or 600)
TICK_EVERY_S = 5
CRON_TICK_EVERY_S = 60  # the Orchestrator's watcher job runs every minute
FAKE_PLAYER_ID = "ply_canary"

KEEP = os.environ.get("IDE_E2E_KEEP") == "1"
SCENARIO = os.environ.get("IDE_E2E_SCENARIO", "1") != "0"
PLATFORM = os.environ.get("IDE_E2E_PLATFORM", "fake")
REAL_REQUIRED = ("SOFTMAX_PLAYER_ID", "SOFTMAX_PLAYER_CREDENTIAL")
REAL_OPTIONAL = ("COGAMES_API_URL", "IDE_DEFAULT_COWORLD")

__all__ = ["openrouter_key"]  # the module-scoped fixture is reused from the spike


def platform_dotenv() -> dict[str, str]:
    """What the harness adds to the host's and every profile's ``.env`` beyond the gateway and
    model settings: the fake platform with its canary Player, or the real platform with the
    caller's Player (``IDE_E2E_PLATFORM=real``)."""
    if PLATFORM == "fake":
        return {
            "IDE_PLATFORM": "fake",
            "SOFTMAX_PLAYER_ID": FAKE_PLAYER_ID,
            "IDE_RESEARCHER_PROFILES": "researcher",
        }
    if PLATFORM != "real":
        pytest.fail(f"IDE_E2E_PLATFORM must be fake or real, not {PLATFORM!r}")
    missing = [name for name in REAL_REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.fail(f"IDE_E2E_PLATFORM=real needs {', '.join(missing)} in the environment")
    values = {"IDE_PLATFORM": "real", "IDE_RESEARCHER_PROFILES": "researcher"}
    for name in (*REAL_REQUIRED, *REAL_OPTIONAL):
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


@pytest.fixture(scope="module")
def host(tmp_path_factory, hermes_bin, openrouter_key) -> Host:
    if not SCENARIO and not KEEP:
        pytest.skip("IDE_E2E_SCENARIO=0 without IDE_E2E_KEEP=1 would start a gateway for nothing")
    root = tmp_path_factory.mktemp("acceptance")
    host = Host(
        hermes_bin=hermes_bin,
        home=root / "host",
        workspace=root / "workspace",
        port=free_port(),
        key=secrets.token_hex(16),
        openrouter_key=openrouter_key,
        log=root / "gateway.log",
        dotenv_extra=platform_dotenv(),
    )
    host.home.mkdir()
    host.write_host_dotenv()
    install_env = {**os.environ, "HERMES_HOME": str(host.home)}
    for role in ("orchestrator", "researcher"):
        argv = [hermes_bin, "profile", "install", REPO / role, "--name", role, "--yes"]
        result = run(argv, install_env)
        assert result.returncode == 0, result.stdout + result.stderr
        host.write_dotenv(role)
        script = host.profile(role) / "scripts" / "prepare.py"
        argv = [hermes_python(hermes_bin), script, "--source", REPO / role, "--hermes", hermes_bin]
        result = run(argv, scenario_env(host, role), timeout=900)
        assert result.returncode == 0, f"prepare {role}:\n{result.stdout}\n{result.stderr}"
    yield host
    host.stop_gateway()


def scenario_env(host: Host, role: str = "orchestrator") -> dict[str, str]:
    """The profile's process environment plus what its ``.env`` carries: prepare.py, the
    in-process watcher ticks and the stores read the same values the gateway's turns see."""
    return {**host.profile_env(role), **host.dotenv_extra}


class Submissions:
    """Where the scenario looks for the submission.

    Fake platform: every submission in its state file, counted per Player id. The tools must
    have run as the profile's ``SOFTMAX_PLAYER_ID``; a count under the fake's default Player
    means the profile's ``.env`` did not reach them. Real platform: the Player's submissions
    on the ladder less those that existed when the scenario started, so an earlier submission
    by the same test Player never passes a run (the Player filter is the ladder's, so the
    per-Player check is implied there).
    """

    def __init__(self, ws: layout.Workspace, env: dict[str, str]) -> None:
        self.ws = ws
        self.env = env
        self.player_id = env["SOFTMAX_PLAYER_ID"]
        self.before = set(self._real_ids()) if PLATFORM == "real" else set()

    def new(self) -> dict[str, int]:
        """Submissions that count, per Player id; empty while there is none."""
        if PLATFORM == "real":
            fresh = [sid for sid in self._real_ids() if sid not in self.before]
            return {self.player_id: len(fresh)} if fresh else {}
        path = self.ws.state / "fake_platform.json"
        if not path.exists():
            return {}
        state = json.loads(path.read_text())
        rows = state.get("submissions") or {}
        rows = list(rows.values()) if isinstance(rows, dict) else list(rows)
        counts: dict[str, int] = {}
        for row in rows:
            counts[str(row.get("player_id"))] = counts.get(str(row.get("player_id")), 0) + 1
        return counts

    def _real_ids(self) -> list[str]:
        from research_core.platform.real import RealPlatform

        with RealPlatform(self.env) as platform:
            return [row.id for row in platform.submissions(player_id=self.player_id)]


def one_tick(env: dict[str, str], hermes: HermesRuns) -> watcher_tick.TickReport:
    """One watcher tick in this process, as the cron job would run it."""
    ctx = watcher_context.build(env, hermes=hermes)
    try:
        return watcher_tick.tick(ctx)
    finally:
        ctx.wstore.close()
        if ctx.store is not None:
            ctx.store.close()


def test_submit_the_starter_on_a_real_gateway(host: Host, capsys):
    env = scenario_env(host)
    ws = layout.from_env(env)
    ws.ensure_dirs()
    if PLATFORM == "fake":
        source, commit = make_fakegame_repo(host.workspace.parent / "upstream")
        FakePlatform(ws.state / "fake_platform.json", env).configure(
            source_url=source.as_uri(), source_commit=commit
        )
    print(f"\ngateway log: {host.log}\nprofiles: {host.home / 'profiles'}\nworkspace: {ws.root}")

    host.start_gateway()
    hermes = HermesRuns.from_profile(
        host.profile("orchestrator"), researcher_profiles=("researcher",)
    )
    server = SeamServer(ws=ws, hermes=hermes)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = ScriptedClient(server.url, timeout_s=60)
    stay = KEEP
    try:
        try:
            if SCENARIO:
                run_scenario(host, ws, env, hermes, client)
            else:
                print("IDE_E2E_SCENARIO=0: no request was sent")
        except KeyboardInterrupt:
            stay = False  # Ctrl-C during the scenario ends the run, it does not open a session
            raise
        finally:
            if stay:
                keep_running(host, ws, env, hermes, server, client, capsys)
    finally:
        server.shutdown()


def run_scenario(
    host: Host,
    ws: layout.Workspace,
    env: dict[str, str],
    hermes: HermesRuns,
    client: ScriptedClient,
) -> None:
    """The acceptance scenario: one request, watcher ticks until a submission exists."""
    player_id = env["SOFTMAX_PLAYER_ID"]
    client.open("canary", "web-ide")
    reply = client.say(REQUEST, {"contract_version": 1, "on_screen": []}, wait=True)
    print(f"chat run {reply['run_id']} ({reply['delivered']})")

    watch = Submissions(ws, env)
    started = time.monotonic()
    ticks = 0
    while time.monotonic() - started < SCENARIO_TIMEOUT_S:
        if watch.new():
            break
        report = one_tick(env, hermes)
        ticks += 1
        if ticks % 6 == 1:
            print(f"tick {ticks}: {json.dumps(report.to_dict())[:400]}")
        time.sleep(TICK_EVERY_S)
    elapsed = round(time.monotonic() - started)
    print(f"{ticks} ticks, {elapsed} s")

    wstore = Store.open(ws.store_path, kind="workspace")
    try:
        active = coworlds.active(wstore)
        texts = [n["text"] for n in notices.unread(wstore, limit=50)]
    finally:
        wstore.close()
    submissions = watch.new()
    print(f"active Coworld: {active and active['coworld_id']}\nnotices: {texts}")
    print(f"submissions by Player: {submissions}")
    assert submissions, f"no submission after {elapsed} s; gateway log {host.log}, notices {texts}"
    assert submissions.get(player_id), (
        f"the submission was not made as the profile's Player {player_id}: {submissions}; "
        "the profile .env did not reach the tools"
    )
    assert active is not None, "no active Coworld after the scenario"
    expected = COWORLD_ID if PLATFORM == "fake" else env.get("IDE_DEFAULT_COWORLD")
    if expected:
        assert active["coworld_id"] == expected, active


def keep_running(
    host: Host,
    ws: layout.Workspace,
    env: dict[str, str],
    hermes: HermesRuns,
    server: SeamServer,
    client: ScriptedClient,
    capsys,
) -> None:
    """``IDE_E2E_KEEP=1``: stay up like the hosted sandbox. Prints where everything is and how
    to talk to it, ticks the watcher every minute as the cron job would, and returns on Ctrl-C
    so the caller can stop the seam server and the gateway."""
    with capsys.disabled():
        if client.session_id is None:
            client.open("keep", "web-ide")
        print(banner(host, ws, env, server, client), flush=True)
        ticks = 0
        try:
            while True:
                ticks += 1
                stamp = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%H:%M:%S %Z")
                try:
                    report = one_tick(env, hermes)
                except Exception as error:  # the next minute's tick runs anyway, as in cron
                    print(f"[{stamp}] watcher tick {ticks} failed: {error!r}", flush=True)
                else:
                    errors = ", ".join(report.errors) or "none"
                    print(
                        f"[{stamp}] watcher tick {ticks}: has_work={report.has_work}"
                        f" paused={report.paused} errors={errors}",
                        flush=True,
                    )
                time.sleep(CRON_TICK_EVERY_S)
        except KeyboardInterrupt:
            print("\nCtrl-C: stopping the seam server and the gateway", flush=True)


def banner(
    host: Host,
    ws: layout.Workspace,
    env: dict[str, str],
    server: SeamServer,
    client: ScriptedClient,
) -> str:
    """Where everything is and ready-to-paste ``curl`` lines. Never prints a secret: the API
    key is read from the profile's ``.env`` by the shell, the Player credential stays in the
    caller's environment."""
    orchestrator = host.profile("orchestrator")
    seam, gateway, sid = server.url, host.base_url, client.session_id
    exports = " ".join(
        f"{name}={env[name]}"
        for name in ("IDE_WORKSPACE", "IDE_PLATFORM", "SOFTMAX_PLAYER_ID", "COGAMES_API_URL")
        if env.get(name)
    )
    json_header = "-H 'Content-Type: application/json'"
    message = json.dumps(
        {
            "session_id": sid,
            "op_id": str(uuid4()),
            "message": "What is the state of the workspace?",
            "actor": "user:local",
            "context": {"contract_version": 1, "on_screen": []},
        }
    )
    lines = [
        "",
        "=== interactive session (IDE_E2E_KEEP=1); Ctrl-C stops the gateway ===",
        f"seam (research serve routes):  {seam}",
        f"gateway (Hermes API server):   {gateway}  routes under /p/<profile>/",
        f"API key:                       {orchestrator / '.env'}  (API_SERVER_KEY, same in every"
        " profile)",
        f"gateway log:                   {host.log}",
        f"profile homes:                 {host.home / 'profiles'}",
        f"workspace (IDE_WORKSPACE):     {ws.root}",
        f"workspace environment:         {ws.venv}",
        f"chat session:                  {sid}",
        "",
        "research CLI from the workspace environment (the Player credential, when real, is"
        " already in your shell):",
        f"  export HERMES_HOME={orchestrator} {exports} IDE_RESEARCHER_PROFILES=researcher",
        f"  {ws.venv / 'bin' / 'research'} watcher status",
        f"  {ws.venv / 'bin' / 'research'} store snapshot --store workspace",
        f"  {ws.venv / 'bin' / 'research'} store events --after 0 --store workspace --follow",
        f'  {ws.venv / "bin" / "research"} tool workspace --args \'{{"action": "doctor"}}\'',
        "",
        "curl against the seam:",
        f"  curl -s -X POST {seam}/session-open {json_header} -d '"
        + json.dumps(
            {
                "op_id": str(uuid4()),
                "client": {"id": "curl", "kind": "web-ide"},
                "actor": "user:local",
            }
        )
        + "'",
        f"  curl -s -X POST {seam}/message {json_header} -d '{message}'",
        f"  curl -s {seam}/runs/<run_id>",
        f"  curl -s '{seam}/snapshot?store=workspace'",
        f"  curl -sN '{seam}/stream?store=workspace&after=0'",
        "",
        "curl against the gateway (a fresh session id, so it never races the chat Session):",
        f"  KEY=$(grep '^API_SERVER_KEY=' {orchestrator / '.env'} | cut -d= -f2-)",
        f'  curl -s -X POST {gateway}/p/orchestrator/v1/runs -H "Authorization: Bearer $KEY"'
        f" {json_header} -d '"
        + json.dumps({"input": "Reply with READY and stop.", "session_id": mint_session_id()})
        + "'",
        f'  curl -s {gateway}/p/orchestrator/v1/runs/<run_id> -H "Authorization: Bearer $KEY"',
        "",
        f"The watcher ticks every {CRON_TICK_EVERY_S} s below, as the cron job would.",
    ]
    return "\n".join(lines)


def test_transcripts_are_where_the_output_says(host: Host):
    """A failed canary must be readable: the gateway log and the profile state exist."""
    assert host.log.exists()
    assert (host.profile("orchestrator") / "state.db").exists() or any(
        host.profile("orchestrator").glob("*.db")
    ), sorted(p.name for p in host.profile("orchestrator").iterdir())
    assert Path(host.workspace).exists()
