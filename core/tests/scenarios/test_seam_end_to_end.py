"""The acceptance scenario on the fake platform, end to end through the seam (plan Task 6.3,
design H "Validation"): "submit the starter as my champion" on a new workspace ends in a
submission.

No model runs. A *scripted agent* stands in for it: the ``script`` hook of
:class:`FakeHermesRuns` receives every run the seam server or the watcher starts and plays the
Orchestrator with real tool calls (``run_tool``) as that run's Session, then completes the run.
The watcher tick runs inline between the runs, as the cron job would. The seam server is
``research serve`` on a thread; the client is the :class:`ScriptedClient`.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_core import layout
from research_core.client import ScriptedClient
from research_core.platform.fake import FakePlatform
from research_core.plugin import turn
from research_core.serve import SeamServer
from research_core.store import coworlds, notices
from research_core.tools.base import ToolContext, run_tool
from research_core.watcher import context as watcher_context
from research_core.watcher import tick as watcher_tick
from research_core.watcher.hermes_api import FakeHermesRuns, FakeRun
from tests.fixtures.fakegame_repo import COWORLD_ID, make_fakegame_repo

REQUEST = "Submit the starter as my champion."
CITES = "msg:m-1"
LEAGUE = "league_fake"


class ScriptedAgent:
    """Plays the Orchestrator for the runs the fake gateway starts.

    A chat run with the user's request switches to the Coworld with the request pending; a
    setup wake carries the pending request out (create, upload, submit, notify); any other
    chat run renders its turn (what the plugin's hook would show the model) and says "Noted".
    """

    def __init__(self, env: dict[str, str], source: Path, commit: str) -> None:
        self.env = env
        self.source = source
        self.commit = commit
        self.hermes: FakeHermesRuns | None = None
        self.rendered: dict[str, str] = {}
        self.tool_results: list[dict] = []
        self.seen_sessions: set[str] = set()
        self.other_wakes: list[str] = []  # the first line of every other wake the watcher started
        self.hold_chat_runs = (
            False  # leave the chat run live (a long model turn) instead of ending it
        )

    def __call__(self, run: FakeRun) -> None:
        ctx = ToolContext.from_env(
            "orchestrator",
            {**self.env, "IDE_SESSION_ID": run.session_id, "IDE_ROLE": "orchestrator"},
        )
        if "# Setup wake" in run.input:
            self.setup(run, ctx)
            return
        if run.input.startswith("# ") and "wake" in run.input.splitlines()[0].lower():
            # Another wake (review, management, anti-stall): a background Session. The
            # plugin's turn hook renders only for chat Sessions, so this one renders nothing.
            self.other_wakes.append(run.input.splitlines()[0])
            self.hermes.complete(run.run_id, "Nothing to do.")
            return
        first = run.session_id not in self.seen_sessions
        self.seen_sessions.add(run.session_id)
        self.rendered[run.run_id] = turn.render_turn(
            ctx, run.session_id, is_first_turn=first, now=datetime.now(UTC)
        )
        if REQUEST.lower().rstrip(".") in run.input.lower():
            self.chat_request(run, ctx)
        else:
            self.hermes.complete(run.run_id, "Noted.")

    def call(self, ctx: ToolContext, tool: str, args: dict) -> dict:
        result = json.loads(run_tool(tool, args, ctx))
        self.tool_results.append({"tool": tool, "args": args, "result": result})
        assert result["ok"] is True, (tool, args, result)
        return result

    def chat_request(self, run: FakeRun, ctx: ToolContext) -> None:
        self.call(
            ctx,
            "workspace",
            {
                "action": "coworld_switch",
                "coworld": COWORLD_ID,
                "source": self.source.as_uri(),
                "commit": self.commit,
                "cites": CITES,
                "pending_request": {"text": REQUEST, "cites": CITES},
            },
        )
        if self.hold_chat_runs:
            return  # the run stays ``running`` until the test completes it
        self.hermes.complete(
            run.run_id, "Switching to fakegame; I will submit the starter as soon as it is set up."
        )

    def setup(self, run: FakeRun, ctx: ToolContext) -> None:
        payload = json.loads(run.input.split("```json\n", 1)[1].split("\n```", 1)[0])
        pending = payload["pending_request"]
        assert pending["text"] == REQUEST
        version = self.call(ctx, "version", {"action": "create", "summary": "The starter policy"})
        vid = version["record"]["id"]
        self.call(ctx, "upload", {"version": vid})
        submitted = self.call(
            ctx,
            "submit",
            {
                "version": vid,
                "league": LEAGUE,
                "user_requested": True,
                "cites": pending["cites"],
                "reason": pending["text"],
            },
        )
        assert submitted.get("refused") is None, submitted
        self.call(
            ctx,
            "ui",
            {
                "action": "notify",
                "text": f"Submitted {vid}, the starter, to {LEAGUE} as you asked.",
                "refs": [vid],
            },
        )
        self.hermes.complete(run.run_id, f"Set up {COWORLD_ID} and submitted {vid}.")


class Scenario:
    def __init__(self, tmp_path: Path) -> None:
        self.env = {
            "IDE_WORKSPACE": str(tmp_path / "ws"),
            "IDE_PLATFORM": "fake",
            "IDE_RESEARCHER_PROFILES": "researcher",
            "SOFTMAX_PLAYER_ID": "ply_test",
        }
        self.ws = layout.from_env(self.env)
        self.ws.ensure_dirs()
        source, commit = make_fakegame_repo(tmp_path / "upstream")
        self.agent = ScriptedAgent(self.env, source, commit)
        self.hermes = FakeHermesRuns(script=self.agent)
        self.agent.hermes = self.hermes
        self.server = SeamServer(ws=self.ws, hermes=self.hermes)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = ScriptedClient(self.server.url)
        self.session_id = self.client.open("scripted", "web-ide")

    def tick(self) -> dict:
        ctx = watcher_context.build(self.env, hermes=self.hermes)
        try:
            report = watcher_tick.tick(ctx)
        finally:
            ctx.wstore.close()
            if ctx.store is not None:
                ctx.store.close()
        return report.to_dict() if hasattr(report, "to_dict") else report

    def starts(self) -> int:
        return sum(1 for method, _ in self.hermes.calls if method == "start")

    def ctx(self) -> ToolContext:
        return ToolContext.from_env("orchestrator", {**self.env, "IDE_SESSION_ID": "test-harness"})

    def platform(self) -> FakePlatform:
        return FakePlatform(self.ws.state / "fake_platform.json", self.env)

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def scenario(tmp_path):
    scenario = Scenario(tmp_path)
    yield scenario
    scenario.close()


def submit_the_starter(scenario: Scenario) -> str:
    """The user asks on a new workspace; the chat run switches, the ticks run setup; returns
    the run id of the user's message."""
    reply = scenario.client.say(REQUEST, {"contract_version": 1, "on_screen": []})
    run_id = reply["run_id"]
    assert scenario.hermes.runs[run_id].status == "completed"
    for _ in range(3):  # the setup wake starts on the first tick; later ticks settle it
        scenario.tick()
    return run_id


def test_acceptance_submit_starter(scenario):
    submit_the_starter(scenario)
    ctx = scenario.ctx()

    # The fake platform holds a submission of the uploaded starter.
    submissions = scenario.platform().submissions(mine=True)
    assert len(submissions) == 1, submissions
    read = json.loads(run_tool("version", {"action": "read", "version": "V-1"}, ctx))
    assert read["ok"] is True, read
    record = read["record"]
    assert record["uploaded"]["policy_version_id"] == submissions[0].policy_version_id
    assert record["submission_status"] in ("submitted", "qualifying", "competing", "champion")

    # The Coworld is active, the request reached the user as a notice, and the submission
    # event cites the user's message.
    assert coworlds.active(ctx.wstore)["coworld_id"] == COWORLD_ID
    texts = [n["text"] for n in notices.unread(ctx.wstore, limit=20)]
    assert any("Submitted V-1" in t for t in texts), texts
    submitted = [
        json.loads(row["payload"])
        for row in ctx.store.q("SELECT payload FROM events WHERE kind = 'version.submitted'")
    ]
    assert submitted and submitted[0]["cites"] == CITES and submitted[0]["user_requested"]
    # The setup wake ran in its own Session of the Orchestrator profile, not in the chat.
    wake_runs = [r for r in scenario.hermes.runs.values() if "# Setup wake" in r.input]
    assert len(wake_runs) == 1 and wake_runs[0].session_id != scenario.session_id
    assert wake_runs[0].status == "completed"
    # Every other run the watcher started was a wake in its own Session, never the chat.
    chat_runs = [r for r in scenario.hermes.runs.values() if r.session_id == scenario.session_id]
    assert len(chat_runs) == 1, [r.input[:60] for r in chat_runs]
    assert all("wake" in line.lower() for line in scenario.agent.other_wakes), (
        scenario.agent.other_wakes
    )


def test_notice_opens_next_turn(scenario):
    submit_the_starter(scenario)
    reply = scenario.client.say("thanks")
    rendered = scenario.agent.rendered[reply["run_id"]]
    assert "[notices since your last turn]" in rendered
    assert "Submitted V-1" in rendered
    assert "[active Coworld] cow_fake" in rendered
    # Read once: the next turn does not repeat it.
    again = scenario.client.say("anything else?")
    assert "Submitted V-1" not in scenario.agent.rendered[again["run_id"]]


def test_reaction_never_starts_run(scenario):
    submit_the_starter(scenario)
    before = scenario.starts()
    result = scenario.client.react("m-1", "👍")
    assert result["accepted"] is True and result["delivered"] == "none"
    assert scenario.starts() == before
    # ...but the agent sees it at its next turn.
    reply = scenario.client.say("ok")
    assert (
        "[reactions since your last turn]\n- message m-1: 👍"
        in scenario.agent.rendered[reply["run_id"]]
    )


def test_concurrent_chat_and_wakes_never_touch_the_chat_session(scenario):
    """A6.1 and B.13: while the chat run is live, the watcher's wakes run in their own
    Sessions, and a second user message steers the live run instead of starting another."""
    scenario.agent.hold_chat_runs = True
    first = scenario.client.say(REQUEST, {"contract_version": 1, "on_screen": []})
    assert first["delivered"] == "start"
    assert scenario.hermes.runs[first["run_id"]].status == "running"

    for _ in range(3):
        scenario.tick()
    wake_runs = [r for r in scenario.hermes.runs.values() if r.session_id != scenario.session_id]
    assert any("# Setup wake" in r.input for r in wake_runs), [r.input[:40] for r in wake_runs]
    assert all(r.status == "completed" for r in wake_runs)
    assert len(scenario.platform().submissions(mine=True)) == 1  # the pending request ran

    second = scenario.client.say("Also: who is the champion right now?")
    assert second["delivered"] == "steer" and second["run_id"] == first["run_id"]
    chat_runs = [r for r in scenario.hermes.runs.values() if r.session_id == scenario.session_id]
    assert len(chat_runs) == 1
    assert any("champion" in text for text in scenario.hermes.steered[first["run_id"]])

    scenario.hermes.complete(first["run_id"], "Done.")
    scenario.agent.hold_chat_runs = False
    third = scenario.client.say("thanks")
    assert third["delivered"] == "start" and third["run_id"] != first["run_id"]
    assert "Submitted V-1" in scenario.agent.rendered[third["run_id"]]
