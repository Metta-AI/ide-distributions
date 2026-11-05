"""A tool module that does not import takes the profile's plugin down, not the gateway.

``catalog._load_tool_modules`` lets the failure propagate; Hermes's plugin loader catches it. This
checks that on a real gateway: the chat still gets an answer, it has none of the research tools
rather than all of them but one, and the log says why. It needs no model key. See the docstring of
``test_frozen_sessions_real.py`` for how to run it in the pinned image.
"""

from __future__ import annotations

from pathlib import Path

from research_core.watcher.hermes_api import mint_session_id
from tests.hermes.test_frozen_sessions_real import Workspace


def break_xp(checkout: Path) -> None:
    (checkout / "core/src/research_core/tools/xp.py").write_text(
        "def f():\n        x = 1\n    y = 2\n"
    )


def test_a_broken_tool_module_is_logged_and_the_gateway_stays_up(tmp_path, hermes_bin, monkeypatch):
    workspace = Workspace(tmp_path, hermes_bin, monkeypatch)
    workspace.publish(break_xp)
    try:
        workspace.boot()
        request = workspace.turn("orchestrator", mint_session_id())
    finally:
        workspace.host.stop_gateway()
    tools = {tool["function"]["name"] for tool in request["tools"]}
    assert not tools & {"task", "goal", "xp"}, sorted(tools)
    logs = workspace.host.home / "profiles" / "orchestrator" / "logs"
    log_text = "\n".join(path.read_text() for path in logs.glob("*.log"))
    log_text += workspace.host.log.read_text()
    assert "Failed to load plugin 'orchestrator'" in log_text
    assert "xp.py" in log_text
