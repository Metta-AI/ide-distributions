"""Component regression for the packaged probe; image CI supplies the real native registry."""

import json
import runpy
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

from research_core import transitions
from research_core.serve import SeamServer
from research_core.store import settings
from research_core.store.actors import Actor
from research_core.tools.base import ToolContext, run_tool
from research_core.watcher.hermes_api import FakeHermesRuns

PROBE = Path(__file__).resolve().parents[2] / "image/system_tests/check-transitions.py"


@pytest.mark.parametrize("fault", [None, "pending", "paused", "sleep_started", "wrong_owner"])
def test_transition_probe_checks_awake_state_report_authority_and_actual_owner(
    tmp_path, monkeypatch, capsys, fault
):
    for key, value in {
        "IDE_WORKSPACE": str(tmp_path),
        "IDE_SESSION_ID": "component-probe",
        "IDE_ACTOR": "",
        "IDE_LEASE_EPOCH": "",
    }.items():
        monkeypatch.setenv(key, value)
    if fault == "pending":
        monkeypatch.setattr(transitions, "REQUEST_WAIT_SECONDS", 0)
    ctx = ToolContext.from_env("researcher")
    ctx.workspace.ensure_dirs()
    if fault == "paused":
        settings.update(ctx.wstore, Actor.harness(), "paused", True, cites="probe regression")
    if fault == "sleep_started":
        ctx.wstore.set_meta("sleep_started", "2026-11-03T00:00:00Z")
    server = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns(), env={})
    polled_commands = set()
    command_status = server.command_status

    def track_command(coworld_id, command_id):
        polled_commands.add(command_id)
        return command_status(coworld_id, command_id)

    monkeypatch.setattr(server, "command_status", track_command)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def local_request(request, **kwargs):
        if isinstance(request, Request):
            request = Request(
                server.url + urlsplit(request.full_url)._replace(scheme="", netloc="").geturl(),
                data=request.data,
                headers=dict(request.headers),
                method=request.get_method(),
            )
        else:
            request = server.url + urlsplit(request)._replace(scheme="", netloc="").geturl()
        return urlopen(request, **kwargs)

    def dispatch(name, args, *, session_id, task_id):
        tool_ctx = ToolContext.from_env("researcher")
        try:
            result = run_tool(name, args, tool_ctx)
            if fault == "wrong_owner" and args["operation_id"] == "packaged-proposal":
                with tool_ctx.store.tx(Actor.harness()) as tx:
                    tx.execute(
                        "UPDATE transition_commands SET owner_instance='coordinator:wrong'"
                        " WHERE completed_at IS NOT NULL"
                    )
            return result
        finally:
            tool_ctx.store.close()
            tool_ctx.wstore.close()

    plugins = ModuleType("hermes_cli.plugins")
    plugins.discover_plugins = lambda: None
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    registry = ModuleType("tools.registry")
    registry.registry = SimpleNamespace(dispatch=dispatch)
    monkeypatch.setitem(sys.modules, "tools", ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", registry)
    monkeypatch.setattr("urllib.request.urlopen", local_request)
    try:
        if fault in (None, "pending"):
            runpy.run_path(str(PROBE))
            result = json.loads(capsys.readouterr().out)
            assert result["commands"] == 3
            if fault == "pending":
                assert len(polled_commands) == 3
            assert result["owner_instances"] == [server.coordinator.instance_id]
            assert result["worker_report"] == "queued fixture attempt and explicit lease epoch"
            assert result["stale_epoch"] == "refused before command acceptance"
        else:
            with pytest.raises(AssertionError):
                runpy.run_path(str(PROBE))
    finally:
        server.shutdown()
        thread.join(5)
        server._http.server_close()
        ctx.wstore.close()
    assert not thread.is_alive()
