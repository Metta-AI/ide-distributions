"""Model tool calls through a real Session: a real Hermes gateway serving both profiles.

The direct tests (``write_actions.py``) ask OpenRouter themselves. Here the model is called by
Hermes, so the request is the real one: the stored system prompt, the frozen tool list, Hermes's
own request shaping, the plugin's hooks, the real tools answering on the fake platform. The
judging is the same (``write_actions.is_judged`` and ``judge``), read from the Session's stored
messages. A run is stopped as soon as the judged call is seen, so a case costs what it took to
get there, and what is judged is the call a model composes, not what the real tool then answered:
that also depends on the seeded records (a Researcher's Session holds its Task but has no worker
execution, so the real ``task.ask`` refuses it). ``test_real_session.py`` runs the cases.

A recovery case needs refused calls already in the history, and the runs API takes only text
history, so those rows are written into the profile's ``state.db`` before the first run, in the
shape Hermes stores them.

That history also differs from a refusal inside one turn: the run's input adds a user message
after the refused calls. The direct tests' recovery cases send the mid-turn shape, with the real
context when ``IDE_TOOL_CALL_CONTEXT`` is set. Both are worth knowing; they do not measure the same
thing, and the mid-turn rate is much lower.

No ``research serve`` runs: its coordinator would start Researchers for the Tasks these cases
create, and a chat turn reaches the gateway the same way. Each case gets its own Coworld with
freshly seeded records (``seed_records``). Sharing one lets runs answer each other: with a shared
store a repeat of the claim case rightly finds the earlier run's claim and writes nothing, which
measured 0/5 on correct behaviour. Workspace records are still shared, so a case that writes one
(``<run>`` in its instruction) is made different for each run.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from research_core import layout
from research_core.platform.fake import FakePlatform
from research_core.store import claims, coworlds, goals, sessions, tasks
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.tools import catalog
from research_core.watcher.hermes_api import HermesRuns, mint_session_id
from tests.fixtures.fakegame_repo import COWORLD_ID, make_fakegame_repo
from tests.hermes.test_spike_s1 import REPO, Host, SecretEnv, free_port, hermes_python, run
from tests.model import harness, write_actions
from tests.model.write_actions import Case

HOST_LABEL = "real session"
RUN_TIMEOUT_S = 600
STOP_TIMEOUT_S = 60
POLL_S = 2
RUN_NUMBERS = itertools.count(100)
# Calls before the judged one, as a Session makes them: reading a skill or the record first is
# normal here, so this is wider than the direct tests' limit.
MAX_DETOURS = 12
FAKE_PLAYER_ID = "ply_model_tests"
# How a run's error text gives a host's refusal: the SDK's "Error code: 400", or "HTTP 400".
REFUSED_REQUEST = re.compile(r"(?:Error code:?|HTTP)\s*(4\d\d)\b")
GOAL = "G-1"
TASK = "T-1"  # open, for the Orchestrator's cases
HELD_TASK = "T-2"  # held by the Researcher's Session
CLAIM = "C-1"
EARLIER_DOC = "https://softmax.com/leagues/L-7 (retrieved 2026-10-20): champion pv-39"

TASK_CREATE = write_actions.TASK_CREATE
ORCHESTRATOR_CASES = [
    Case("orchestrator", "claim", "write", write_actions.CHAMPION_CLAIM),
    Case(
        "orchestrator",
        "claim",
        "add_evidence",
        f"Add league page evidence (type doc, ref '{write_actions.CHAMPION_DOC}') to claim "
        f"{CLAIM}, supporting it.",
    ),
    Case(
        "orchestrator",
        "goal",
        "create",
        "Create a Goal 'Reach the top 3 of league L-7': done when our policy ranks in the top 3; "
        "I am asking for it now.",
    ),
    Case("orchestrator", "task", "create", TASK_CREATE),
    Case(
        "orchestrator",
        "task",
        "assign",
        f"Assign Task {TASK} to the researcher profile: start from league L-7's standings, "
        "because it is the only worker.",
    ),
    Case(
        "orchestrator",
        "task",
        "message",
        f"Tell the Researcher on Task {TASK}: use league L-7's practice division.",
    ),
    Case(
        "orchestrator",
        "directive",
        "add",
        "From now on, never submit to league L-<run> without asking me first.",
    ),
    Case("orchestrator", "settings", "update", "Set max_researchers to 2, please."),
    Case("orchestrator", "ui", "notify", write_actions.UI_NOTIFY),
]
RESEARCHER_CASES = [
    Case("researcher", "claim", "write", write_actions.CHAMPION_CLAIM),
    Case(
        "researcher",
        "task",
        "ask",
        f"Ask the Orchestrator on Task {HELD_TASK} whether to use the practice or ranked division.",
    ),
    Case(
        "researcher",
        "task",
        "wait",
        f"Mark Task {HELD_TASK} as waiting for xp request req-1c0 to finish.",
    ),
]
RECOVERY_CASES = [
    Case(role, tool, action, instruction, refused=refused, refusals=refusals)
    for role, tool, action, instruction, refused in (
        (
            "orchestrator",
            "claim",
            "write",
            write_actions.CHAMPION_CLAIM,
            write_actions.INCIDENT_CLAIM_ARGS,
        ),
        (
            "researcher",
            "claim",
            "write",
            write_actions.CHAMPION_CLAIM,
            write_actions.INCIDENT_CLAIM_ARGS,
        ),
        (
            "orchestrator",
            "task",
            "create",
            TASK_CREATE,
            {"action": "create", "title": "Why do we lose early fights?", "goal": GOAL},
        ),
        (
            "orchestrator",
            "task",
            "message",
            f"Tell the Researcher on Task {TASK}: use league L-7's practice division.",
            {"action": "message", "task": TASK},
        ),
    )
    for refusals in write_actions.RECOVERY_DEPTHS
]
CASES = [*ORCHESTRATOR_CASES, *RESEARCHER_CASES, *RECOVERY_CASES]


@dataclass
class RealGateway:
    """Both profiles installed and prepared in ``root``, as hosting sets them up, on one gateway
    with the fake platform and the seeded records."""

    root: Path
    hermes_bin: Path
    # Not in the repr: pytest prints a failing test's fixtures with it.
    openrouter_key: str = field(repr=False)
    model: str
    host: Host | None = field(default=None, repr=False)
    runs: HermesRuns | None = None

    def start(self) -> None:
        self.host = Host(
            hermes_bin=self.hermes_bin,
            home=self.root / "host",
            workspace=self.root / "workspace",
            port=free_port(),
            key=secrets.token_hex(16),
            openrouter_key=self.openrouter_key,
            log=self.root / "gateway.log",
            dotenv_extra={
                "IDE_PLATFORM": "fake",
                "SOFTMAX_PLAYER_ID": FAKE_PLAYER_ID,
                "IDE_RESEARCHER_PROFILES": "researcher",
            },
        )
        self.host.home.mkdir(parents=True)
        self.host.write_host_dotenv()
        for role in catalog.ROLES:
            self._install(role)
        self._seed()
        self.host.start_gateway()
        self.runs = HermesRuns.from_profile(
            self.host.profile("orchestrator"), researcher_profiles=("researcher",)
        )

    def stop(self) -> None:
        """Stop the gateway and remove the ``.env`` files: pytest keeps its temporary folders,
        and these hold the OpenRouter key."""
        self.host.stop_gateway()
        for dotenv in self.host.home.rglob(".env"):
            dotenv.unlink()

    def env(self, role: str = "orchestrator") -> dict[str, str]:
        return SecretEnv({**self.host.profile_env(role), **self.host.dotenv_extra})

    def state_db(self, role: str) -> Path:
        return self.host.profile(role) / "state.db"

    def _install(self, role: str) -> None:
        hermes = self.hermes_bin
        install_env = SecretEnv({**os.environ, "HERMES_HOME": str(self.host.home)})
        result = run(
            [hermes, "profile", "install", REPO / role, "--name", role, "--yes"], install_env
        )
        assert result.returncode == 0, result.stdout + result.stderr
        self.host.write_dotenv(role)
        script = self.host.profile(role) / "scripts" / "prepare.py"
        argv = [hermes_python(hermes), script, "--source", REPO / role, "--hermes", hermes]
        result = run(argv, self.env(role), timeout=900)
        assert result.returncode == 0, f"prepare {role}:\n{result.stdout}\n{result.stderr}"
        result = run([hermes, "config", "set", "model.default", self.model], self.env(role))
        assert result.returncode == 0, result.stdout + result.stderr

    def _seed(self) -> None:
        env = self.env()
        ws = layout.from_env(env)
        ws.ensure_dirs()
        source, commit = make_fakegame_repo(self.root / "upstream")
        FakePlatform(ws.state / "fake_platform.json", env).configure(
            source_url=source.as_uri(), source_commit=commit
        )
        with Store.open(ws.store_path, kind="workspace") as wstore:
            # A running watcher's mark, so the briefing does not say background work is down.
            wstore.set_meta("heartbeat", datetime.now(UTC).isoformat())

    def open_session(self, role: str) -> str:
        """A native session and its Session row, bound to a new seeded Coworld, as the seam or a
        wake makes them. A Researcher's Session holds the seeded Task."""
        session_id = mint_session_id()
        ws = layout.from_env(self.env(role))
        actor = Actor.harness()
        coworld_id = f"{COWORLD_ID}_{secrets.token_hex(4)}"
        task_id = int(HELD_TASK.split("-")[1]) if role == "researcher" else None
        with Store.open(ws.store_path, kind="workspace") as wstore:
            coworlds.register(wstore, actor, coworld_id, folder=layout.folder_name_for(coworld_id))
            sessions.register(
                wstore,
                actor,
                session_id=session_id,
                profile=role,
                kind="chat" if role == "orchestrator" else "task",
                coworld_id=coworld_id,
                task_id=task_id,
                session_context={"contract_version": 1, "scope": "coworld", "coworld": coworld_id},
            )
        coworld = ws.coworld(coworld_id)
        coworld.ensure_dirs()
        with Store.open(coworld.store_path, kind="coworld") as store:
            holder = session_id if role == "researcher" else "another-session"
            seed_records(store, actor, holder)
        self.runs.create_session(session_id, profile=role)
        return session_id

    def seed_refusals(self, session_id: str, case: Case, instruction: str) -> None:
        """Write the case's first user turn and refused calls into ``state.db``. The instruction
        is stored here, so the run's input only asks the model to continue."""
        schemas = harness.ShippedSchemas()
        rows = [{"role": "user", "content": instruction}]
        rows += write_actions.refused_history(case, schemas)
        connection = sqlite3.connect(self.state_db(case.role), timeout=30)
        try:
            for index, row in enumerate(rows):
                tool_calls = row.get("tool_calls")
                connection.execute(
                    "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, "
                    "tool_name, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        row["role"],
                        row.get("content"),
                        row.get("tool_call_id"),
                        json.dumps(tool_calls) if tool_calls else None,
                        case.tool if row["role"] == "tool" else None,
                        time.time() + index * 0.001,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def stored_prompt_and_tools(self, role: str, session_id: str) -> harness.SessionContext:
        """The system prompt Hermes stored for a Session, and its frozen Hermes tools."""
        connection = sqlite3.connect(f"file:{self.state_db(role)}?mode=ro", uri=True)
        try:
            prompt, pin = connection.execute(
                "SELECT sp.prompt, tp.prompt FROM sessions s "
                "JOIN system_prompts sp ON sp.hash = s.system_prompt_hash "
                "JOIN system_prompts tp ON tp.hash = s.tool_names WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
        ide_tools = {spec.name for spec in catalog.tools_for(role)}
        hermes_tools = [
            tool for tool in json.loads(pin)["tools"] if tool["function"]["name"] not in ide_tools
        ]
        return harness.SessionContext(system=prompt, hermes_tools=hermes_tools)

    def run_to_completion(self, role: str, session_id: str, text: str) -> None:
        run_id = self.runs.start(
            profile=role, session_id=session_id, text=text, idempotency_key=f"capture:{session_id}"
        )
        deadline = time.monotonic() + RUN_TIMEOUT_S
        while not self.runs.status(run_id, profile=role).terminal:
            assert time.monotonic() < deadline, f"run {run_id} did not end"
            time.sleep(POLL_S)

    def run_case(self, model: str, case: Case) -> harness.Result:
        """Start a run for the case and read the Session until the judged call shows up."""
        result = harness.Result(model, HOST_LABEL, case.label, "wrong_tool")
        session_id = self.open_session(case.role)
        text = case.instruction.replace("<run>", str(next(RUN_NUMBERS)))
        seeded = 0
        if case.refused is not None:
            self.seed_refusals(session_id, case, case.instruction)
            seeded = 1 + 2 * case.refusals
            text = "Please go on with that request."
        started = time.monotonic()
        run_id = self.runs.start(
            profile=case.role,
            session_id=session_id,
            text=text,
            idempotency_key=f"model-test:{session_id}",
        )
        while time.monotonic() - started < RUN_TIMEOUT_S:
            status = self.runs.status(run_id, profile=case.role)
            stored = self.runs.session_messages(session_id, profile=case.role)
            if self._judge_new_calls(case, stored[seeded:], result):
                break
            if status.terminal:
                self._judge_end(status, stored[seeded:], result)
                break
            time.sleep(POLL_S)
        else:
            result.outcome, result.detail = "host_error", f"no judged call in {RUN_TIMEOUT_S} s"
        self._stop_and_wait(case.role, run_id)
        result.replies = self._billed_replies(case.role, session_id, time.monotonic() - started)
        return result

    def _stop_and_wait(self, role: str, run_id: str) -> None:
        """Stop the run and let it end, so its usage is stored before it is read."""
        if not self.runs.status(run_id, profile=role).terminal:
            self.runs.stop(run_id, profile=role)
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while not self.runs.status(run_id, profile=role).terminal:
            if time.monotonic() > deadline:
                return
            time.sleep(POLL_S)

    def _judge_new_calls(self, case: Case, stored: list[dict], result: harness.Result) -> bool:
        """True when the judged call was found or there were too many other calls first."""
        detours: list[str] = []
        for message in stored:
            for raw in message.get("tool_calls") or []:
                call = harness._call(raw)
                args = call.arguments or {}
                if write_actions.is_judged(case, call.name, args):
                    result.outcome, result.detail = write_actions.judge(case, call, args)
                    result.reasoning_gaps = write_actions.gaps_in_reasoning(
                        case, args, harness.reasoning_of(message)
                    )
                    result.detours = detours
                    return True
                detours.append(f"{call.name} {call.raw[:120]}")
                if len(detours) > MAX_DETOURS:
                    result.detours = detours
                    result.detail = f"never called {case.tool}.{case.action}; called {detours}"
                    return True
        result.detours = detours
        return False

    def _judge_end(self, status, stored: list[dict], result: harness.Result) -> None:
        """A run that ended without the judged call: text, or a host problem."""
        if status.status != "completed":
            http_status = REFUSED_REQUEST.search(status.error or "")
            refused = http_status and harness._is_rejection(int(http_status[1]))
            result.outcome = "rejected" if refused else "host_error"
            result.detail = f"run ended {status.status}: {status.error or ''}"[:300]
            return
        last = next((m for m in reversed(stored) if m.get("role") == "assistant"), {})
        result.outcome = "stopped" if result.detours else "no_call"
        result.detail = f"text: {str(last.get('content'))[:150]!r}"

    def _billed_replies(self, role: str, session_id: str, seconds: float) -> list[harness.Reply]:
        """One reply per billed model call, from the usage Hermes stored for the Session. It
        stores totals, so each reply gets an even share: requests and cost are exact, tokens
        are the mean per request."""
        connection = sqlite3.connect(f"file:{self.state_db(role)}?mode=ro", uri=True)
        try:
            calls, uncached, cached, output, cost = connection.execute(
                "SELECT api_call_count, input_tokens, cache_read_tokens, output_tokens, "
                "COALESCE(actual_cost_usd, estimated_cost_usd, 0) FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
        calls = max(calls, 1)
        return [
            harness.Reply(
                message={},
                calls=[],
                text="",
                provider="moonshotai through Hermes",
                prompt_tokens=(uncached + cached) // calls,
                cached_tokens=cached // calls,
                completion_tokens=output // calls,
                cost=cost / calls,
                seconds=seconds / calls,
            )
            for _ in range(calls)
        ]


def seed_records(store: Store, actor: Actor, holder: str) -> None:
    """The records the instructions name: Goal G-1, Tasks T-1 and T-2 (held by the Session
    ``holder``), claim C-1."""
    goals.create(
        store,
        actor,
        title="Understand early fights",
        end_state="We know why we lose early fights.",
        reason="The user asked for it.",
    )
    tasks.create(
        store,
        actor,
        type="research",
        title="Survey league L-7",
        description="Survey the field of league L-7 and report the leading strategies.",
        goal_id=GOAL,
    )
    tasks.create(
        store,
        actor,
        type="research",
        title="Compare the two divisions",
        description="Compare the practice and ranked divisions of league L-7.",
        goal_id=GOAL,
    )
    # Held, so a Researcher's cases find their Task in progress instead of first claiming it.
    tasks.claim(store, Actor.session(holder), HELD_TASK, run_id=None, worktree="w", branch="b")
    claims.write(
        store,
        actor,
        summary="pv-39 led league L-7 until this week.",
        body="The league page showed pv-39 at rank 1.",
        tags=["champion"],
        # Not CHAMPION_DOC: the evidence case adds that one, and a model rightly declines to add
        # a link that is already there.
        evidence=[{"type": "doc", "ref": EARLIER_DOC, "stance": "supports"}],
    )


def hermes_bin_from_env() -> Path | None:
    value = os.environ.get("HERMES_BIN")
    return Path(value) if value else None
