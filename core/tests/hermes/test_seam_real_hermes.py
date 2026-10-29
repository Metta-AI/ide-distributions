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
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

import pytest

from research_core import layout
from research_core.client import ScriptedClient
from research_core.platform.fake import FakePlatform
from research_core.serve import SeamServer
from research_core.store import coworlds, notices
from research_core.store.db import Store
from research_core.watcher import context as watcher_context
from research_core.watcher import tick as watcher_tick
from research_core.watcher.hermes_api import HermesRuns
from tests.fixtures.fakegame_repo import COWORLD_ID, make_fakegame_repo
from tests.hermes.test_spike_s1 import REPO, Host, free_port, hermes_python, openrouter_key, run

REQUEST = "Submit the starter as my champion."
SCENARIO_TIMEOUT_S = 600
TICK_EVERY_S = 5
PLAYER_ID = "ply_canary"

__all__ = ["openrouter_key"]  # the module-scoped fixture is reused from the spike


@pytest.fixture(scope="module")
def host(tmp_path_factory, hermes_bin, openrouter_key) -> Host:
    root = tmp_path_factory.mktemp("acceptance")
    host = Host(
        hermes_bin=hermes_bin,
        home=root / "host",
        workspace=root / "workspace",
        port=free_port(),
        key=secrets.token_hex(16),
        openrouter_key=openrouter_key,
        log=root / "gateway.log",
    )
    host.home.mkdir()
    host.write_host_dotenv()
    install_env = {**os.environ, "HERMES_HOME": str(host.home)}
    for role in ("orchestrator", "researcher"):
        argv = [hermes_bin, "profile", "install", REPO / role, "--name", role, "--yes"]
        result = run(argv, install_env)
        assert result.returncode == 0, result.stdout + result.stderr
        host.write_dotenv(role)
        with (host.profile(role) / ".env").open("a") as dotenv:
            dotenv.write(f"SOFTMAX_PLAYER_ID={PLAYER_ID}\nIDE_RESEARCHER_PROFILES=researcher\n")
        script = host.profile(role) / "scripts" / "prepare.py"
        argv = [hermes_python(hermes_bin), script, "--source", REPO / role, "--hermes", hermes_bin]
        result = run(argv, scenario_env(host, role), timeout=900)
        assert result.returncode == 0, f"prepare {role}:\n{result.stdout}\n{result.stderr}"
    yield host
    host.stop_gateway()


def scenario_env(host: Host, role: str = "orchestrator") -> dict[str, str]:
    return {
        **host.profile_env(role),
        "IDE_PLATFORM": "fake",
        "IDE_RESEARCHER_PROFILES": "researcher",
        "SOFTMAX_PLAYER_ID": PLAYER_ID,
    }


def submission_count(ws: layout.Workspace, env: dict[str, str]) -> int:
    platform = FakePlatform(ws.state / "fake_platform.json", env)
    return len(platform.submissions(mine=True))


def test_submit_the_starter_on_a_real_gateway(host: Host):
    env = scenario_env(host)
    ws = layout.from_env(env)
    ws.ensure_dirs()
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
    try:
        client.open("canary", "web-ide")
        reply = client.say(REQUEST, {"contract_version": 1, "on_screen": []})
        print(f"chat run {reply['run_id']} ({reply['delivered']})")

        started = time.monotonic()
        ticks = 0
        while time.monotonic() - started < SCENARIO_TIMEOUT_S:
            if submission_count(ws, env) >= 1:
                break
            ctx = watcher_context.build(env, hermes=hermes)
            try:
                report = watcher_tick.tick(ctx)
            finally:
                ctx.wstore.close()
                if ctx.store is not None:
                    ctx.store.close()
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
        print(f"active Coworld: {active and active['coworld_id']}\nnotices: {texts}")
        assert submission_count(ws, env) >= 1, (
            f"no submission after {elapsed} s; gateway log {host.log}, notices {texts}"
        )
        assert active is not None and active["coworld_id"] == COWORLD_ID
    finally:
        server.shutdown()


def test_transcripts_are_where_the_output_says(host: Host):
    """A failed canary must be readable: the gateway log and the profile state exist."""
    assert host.log.exists()
    assert (host.profile("orchestrator") / "state.db").exists() or any(
        host.profile("orchestrator").glob("*.db")
    ), sorted(p.name for p in host.profile("orchestrator").iterdir())
    assert Path(host.workspace).exists()
