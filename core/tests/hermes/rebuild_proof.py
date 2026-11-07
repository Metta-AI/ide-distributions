"""A real Session, driven from the seam through a real gateway, its lines fed to a fake Datadog.

``test_rebuild_real.py`` compares what the rebuild command gets from the fake Datadog with what the
stub model received and what the profile's ``state.db`` holds.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from research_core import layout
from research_core.agent_telemetry.stream import STREAM_VARIABLE
from research_core.serve import SeamServer
from research_core.store import admissions, sessions
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.watcher.hermes_api import HermesRuns, mint_session_id
from tests.hermes.capture_rig import (
    API_KEY,
    PROOF_WORKSPACE,
    RealGateway,
    StubModel,
    start_gateway,
    tool_call,
)
from tests.hermes.test_spike_s1 import free_port, hermes_python
from tests.telemetry.conftest import raw_lines
from tests.telemetry.fake_datadog import FakeDatadog

HARNESS = Actor.harness()
CORE_SRC = Path(__file__).resolve().parents[2] / "src"
_REDACT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from research_core.agent_telemetry import hermes_redactor
print(json.dumps(hermes_redactor().value(json.load(sys.stdin))))
"""


def redacted_by_hermes(hermes_bin: Path, value: object) -> object:
    """``value`` through Hermes's own scanner, so that "apart from redaction" is tested too."""
    result = subprocess.run(
        [hermes_python(hermes_bin), "-c", _REDACT, str(CORE_SRC)],
        input=json.dumps(value),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@dataclass
class Proof:
    profile: str
    session_id: str
    stub: StubModel
    gateway: RealGateway
    datadog: FakeDatadog
    run_ids: list[str]

    @property
    def state_db(self) -> Path:
        return self.gateway.home / "profiles" / self.profile / "state.db"


def _with_store(workspace: Path) -> None:
    Store.open(
        layout.from_env({"IDE_WORKSPACE": str(workspace)}).store_path, kind="workspace"
    ).close()


class _Turns:
    """Turns through ``research serve``, so that admissions and store events are real too."""

    def __init__(self, gateway: RealGateway, profile: str) -> None:
        self.gateway = gateway
        self.profile = profile
        self.workspace = layout.from_env({"IDE_WORKSPACE": str(gateway.workspace)})
        env = {
            "IDE_WORKSPACE": str(gateway.workspace),
            "IDE_PLATFORM": "fake",
            "IDE_WORKSPACE_ID": PROOF_WORKSPACE,
            STREAM_VARIABLE: str(gateway.stream),
        }
        native = HermesRuns(
            base_url=f"http://127.0.0.1:{gateway.port}",
            keys={"orchestrator": API_KEY, "researcher": API_KEY},
            timeout_s=30,
        )
        # This process stands in for ``research serve``, whose writer reads its own environment.
        with patch.dict(os.environ, {STREAM_VARIABLE: str(gateway.stream)}):
            self.server = SeamServer(ws=self.workspace, hermes=native, env=env)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.session_id = mint_session_id()
        self.count = 0

    def open(self) -> None:
        self.gateway.request("POST", self.profile, "/api/sessions", {"id": self.session_id})
        with Store.open(self.workspace.store_path, kind="workspace") as store:
            sessions.register(
                store, HARNESS, session_id=self.session_id, profile=self.profile, kind="chat"
            )

    def turn(self, text: str) -> str:
        """One admission, until the seam has seen its run end."""
        self.count += 1
        deadline = time.monotonic() + 180
        with Store.open(self.workspace.store_path, kind="workspace") as store:
            op_id = admissions.enqueue(
                store,
                HARNESS,
                key=f"turn-{self.count}",
                session_id=self.session_id,
                profile=self.profile,
                text=text,
                origin="continuation",
            )
            while time.monotonic() < deadline:
                row = store.one(
                    "SELECT status, run_id, error FROM client_admissions WHERE op_id = ?", (op_id,)
                )
                if row["status"] == "completed":
                    return row["run_id"]
                assert row["status"] in ("queued", "running"), (dict(row), self.gateway.log_tail())
                time.sleep(0.05)
        raise AssertionError(f"{dict(row)}: {self.gateway.log_tail()}")

    def close(self) -> None:
        self.server.shutdown()


def run_session(
    root: Path,
    python: str,
    profile: str,
    *,
    before_restart: int,
    after_restart: int,
    result_chars: int,
    first_text: str = "Turn 0: read the settings.",
    tool_calls: list[dict] | None = None,
) -> Proof:
    """Turns with a padded tool result, enough to make Hermes prune, a restart, and more turns."""
    stub = StubModel(
        tool_calls or [tool_call(0, "call_read", "settings", json.dumps({"action": "read"}))],
        usage_from_size=True,
    )
    gateway = start_gateway(
        root,
        python,
        stub.url,
        free_port(),
        prepare_workspace=_with_store,
        result_chars=result_chars,
        prune_tokens=64000,
    )
    turns = _Turns(gateway, profile)
    run_ids = []
    try:
        turns.open()
        run_ids.append(turns.turn(first_text))
        for number in range(1, before_restart):
            run_ids.append(turns.turn(f"Turn {number}: read the settings."))
        if after_restart:
            gateway.restart()
            for number in range(after_restart):
                run_ids.append(turns.turn(f"After the restart, turn {number}."))
    finally:
        turns.close()
        gateway.stop()
        stub.close()
    # Everything, including what the stops wrote, as the Datadog Agent reads it from the stream.
    datadog = FakeDatadog()
    datadog.take_lines(raw_lines(gateway.stream))
    return Proof(profile, turns.session_id, stub, gateway, datadog, run_ids)
