"""A scripted Orchestrator for model-free runs of the seam (plan Task 6.3, design H).

:class:`ScriptedAgent` is the ``script`` hook of :class:`FakeHermesRuns`: it receives every run
the seam server or the watcher starts and plays the Orchestrator with real tool calls
(``run_tool``) as that run's Session, then completes the run. The acceptance scenario
(``core/tests/scenarios/test_seam_end_to_end.py``) and the development stack behind the client
page (``tools/ui_dev.py``) share it, which is why it lives in the package and not in the tests.

What it does with each run:

* a chat run whose text contains :data:`REQUEST` ("Submit the starter as my champion.")
  switches to the fake Coworld with the request pending;
* a setup wake carries the pending request out: create the Version, upload, submit, notify;
* every other wake completes with "Nothing to do.";
* any other chat run renders its turn (what the plugin's hook would show the model), answers
  ``Noted: <the first 80 characters of the message>`` and, so a client has something to see,
  reacts to these words in the message:

  ==========  ====================================================================
  ``ask me``  opens a question with two options through ``ui.ask``
  ``notify``  sends a notice through ``ui.notify``
  ``goal``    creates a Goal through the ``goal`` tool (needs an active Coworld), brings
              it into view through ``ui.show`` and writes a markdown panel about it
  ==========  ====================================================================

  The reply names what it did, so the trigger words are visible in the conversation too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from research_core import contract
from research_core.platform.fake import COWORLD_ID, LEAGUE_ID
from research_core.plugin import turn
from research_core.store import coworlds
from research_core.tools.base import ToolContext, run_tool
from research_core.watcher.hermes_api import FakeHermesRuns, FakeRun

REQUEST = "Submit the starter as my champion."
CITES = "msg:m-1"
LEAGUE = LEAGUE_ID
NOTED_CHARS = 80
# The trigger words of an ordinary chat run, with what each does; the client page shows them.
TRIGGERS = {
    "ask me": "opens a question with two options (answer it in Asks)",
    "notify": "sends a notice",
    "goal": "creates a Goal (once a Coworld is active), shows it and sets a markdown panel",
}


class ScriptedAgent:
    """Plays the Orchestrator for the runs the fake gateway starts. See the module docstring.

    ``source`` and ``commit`` are the fake Coworld's source repository and the commit to pin
    (``tests.fixtures.fakegame_repo.make_fakegame_repo``); ``coworld_id`` is the Coworld that
    repository's mixin declares. Set ``hermes`` to the fake after building it.
    """

    def __init__(
        self, env: dict[str, str], source: Path, commit: str, *, coworld_id: str = COWORLD_ID
    ) -> None:
        self.env = env
        self.source = source
        self.commit = commit
        self.coworld_id = coworld_id
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
            self.chat_other(run, ctx)

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
                "coworld": self.coworld_id,
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

    def chat_other(self, run: FakeRun, ctx: ToolContext) -> None:
        """The reply to any other message: "Noted: ..." plus the triggers' side effects."""
        text = message_text(run.input)
        excerpt = text[:NOTED_CHARS]
        lower = text.lower()
        done: list[str] = []
        if "ask me" in lower:
            asked = self.call(
                ctx,
                "ui",
                {
                    "action": "ask",
                    "question": f"You said {excerpt!r}. Which option do you want?",
                    "options": ["Option A", "Option B"],
                },
            )
            done.append(f"asked {asked['ask_id']}")
        if "notify" in lower:
            self.call(
                ctx,
                "ui",
                {"action": "notify", "text": f"Notice, as you asked: {excerpt}", "urgency": "low"},
            )
            done.append("sent a notice")
        if "goal" in lower:
            if coworlds.active(ctx.wstore) is None:
                done.append("created no Goal: no Coworld is active yet")
            else:
                goal = self.call(
                    ctx,
                    "goal",
                    {
                        "action": "create",
                        "title": f"Goal from chat: {text[:60]}",
                        "end_state": "The user says it is done.",
                        "reason": text[:200],
                    },
                )
                goal_id = goal["record"]["id"]
                self.call(ctx, "ui", {"action": "show", "ref": goal_id})
                self.call(
                    ctx,
                    "ui",
                    {
                        "action": "panel_set",
                        "panel": "goal-brief",
                        "kind": "markdown",
                        "content": f"# {goal_id}\n\nMade from your message: {excerpt}\n\n"
                        "End state: the user says it is done.",
                    },
                )
                done.append(f"created {goal_id}, brought it into view and set the goal-brief panel")
        reply = f"Noted: {excerpt}"
        if done:
            reply += "\n\nI " + "; ".join(done) + "."
        self.hermes.complete(run.run_id, reply)

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
        self.hermes.complete(run.run_id, f"Set up {self.coworld_id} and submitted {vid}.")


def message_text(run_input: str) -> str:
    """The user's message without the context block the seam appends (``Context v1:``)."""
    return run_input.split(f"\n\n{contract.BLOCK_LABEL}\n", 1)[0].strip()
