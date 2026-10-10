"""The IDE coach chat (plugin.coach): profile environment, read tools, confirm-gated
proposals, exact-line commands, the chat tool allowlist and the chat budget."""

import json
import os
import re
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from support.state_db import add_session

from autoresearch.comms.inbound import InboundProblems, check_inbound
from autoresearch.plugin import adapters, coach, proposals

CLI = "/x/bin/autoresearch"
STEER = {"text": "Stop kill-steals", "urgency": "now"}
M_ID = "M-01J9ZZZZZZZZZZZZZZZZZZZZZZ"


class FakeCLI:
    """``subprocess.run`` for coach: records each call and answers like the profile CLI.
    ``comms inbound --dry-run`` runs the real validation (``check_inbound``)."""

    def __init__(self, tmp_path: Path):
        self.calls: list[tuple[list[str], dict]] = []
        self.canonical = tmp_path / "canonical"
        self.stdout: dict[str, str] = {}  # first argv word after the CLI -> stdout override
        self.lock = threading.Lock()

    def __call__(self, argv, **kwargs):
        with self.lock:
            self.calls.append((list(argv), kwargs))
        args = list(argv[1:])
        if args[0] in self.stdout:
            return subprocess.CompletedProcess(argv, 0, self.stdout[args[0]], "")
        envelope = {"ok": True, "data": {"args": args}}
        if args[:2] == ["comms", "inbound"]:
            payload = json.loads(args[args.index("--json") + 1])
            author = args[args.index("--author") + 1]
            if "--dry-run" in args:
                try:
                    check_inbound(self.canonical, args[2], payload, author)
                    envelope = {"ok": True, "data": {"kind": args[2], "valid": True}}
                except InboundProblems as error:
                    envelope = {"ok": False, "error": {"message": str(error),
                                                       "problems": error.problems}}
            else:
                time.sleep(0.01)  # widen any race between two confirms
                number = len(self.sends()) - 1  # this call is recorded already
                envelope = {"ok": True, "data": {"id": f"I-SENT{number}", "kind": args[2]}}
        elif args[:2] == ["comms", "get"]:
            envelope = {"ok": True, "data": {"id": args[2], "title": "Which opening next?"}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(envelope), "")

    def sends(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls
                if argv[1:3] == ["comms", "inbound"] and "--dry-run" not in argv]


def _profile(root: Path, name: str, ours: bool = True, env_lines: list[str] | None = None) -> Path:
    home = root / "profiles" / name
    home.mkdir(parents=True)
    (home / "distribution.yaml").write_text(f"name: {name}\nsource: /opt/autoresearch\n")
    if ours:
        (home / "plugins" / "autoresearch").mkdir(parents=True)
        (home / "plugins" / "autoresearch" / "plugin.yaml").write_text("name: autoresearch\n")
    lines = env_lines if env_lines is not None else [
        f"AUTORESEARCH_CLI={CLI}", "SOFTMAX_PLAYER_CREDENTIAL=cga_t", "AUTORESEARCH_PLAYER_ID=ply_t",
    ]
    (home / ".env").write_text("\n".join(lines) + "\n")
    return home


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """A gateway-like process: no profile names in os.environ, Hermes's resolver pointing at an
    installed ``optimizer`` profile of ours, a fake CLI, and a fresh coach state."""
    for name in list(os.environ):
        if name.startswith(("AUTORESEARCH_", "SOFTMAX_", "HERMES_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GATEWAY_ONLY", "kept")
    home = _profile(tmp_path / "root", "optimizer")
    resolver = types.SimpleNamespace(home=home)
    module = types.ModuleType("hermes_constants")
    module.get_hermes_home = lambda: resolver.home
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    fake = FakeCLI(tmp_path)
    monkeypatch.setattr(coach, "spawn", lambda argv, env, cwd, timeout: fake(
        argv, env=env, cwd=cwd, timeout=timeout))
    monkeypatch.setattr(coach, "STORE", proposals.Store())
    monkeypatch.setattr(coach, "_sessions", {})
    monkeypatch.setattr(coach, "_rounds", {})
    monkeypatch.setattr(coach, "_sends", [])
    monkeypatch.setattr(coach, "_send_queues", {})
    return types.SimpleNamespace(home=home, root=tmp_path / "root", resolver=resolver, cli=fake)


HANDLERS = {name: handler for name, _, handler in coach.SPECS}


def _turn(session="s1", line="hi", platform="api_server", first=False):
    return adapters.pre_llm_call(
        is_first_turn=first, session_id=session, platform=platform, user_message=line,
        model="m", turn_id="t",
    )


def _settled():
    """Wait for the plugin's send workers (sends run off the hook thread)."""
    coach.wait_for_sends(10)


def _reply(session="s1", text="ok", turn="t1"):
    return adapters.transform_llm_output(
        response_text=text, session_id=session, platform="api_server", turn_id=turn, model="m",
    )


def _propose(session="s1", kind="steer", payload=None, summary="something harmless"):
    result = HANDLERS["propose_inbound"](
        {"kind": kind, "payload": STEER if payload is None else payload, "summary": summary},
        session_id=session,
    )
    return json.loads(result)


# --- 1. the routed profile's environment (I5) ---


def test_status_runs_the_routed_profile_cli_with_its_env(chat):
    _turn()
    result = json.loads(HANDLERS["status"]({}, session_id="s1"))
    assert result["ok"] is True
    [(argv, kwargs)] = chat.cli.calls
    assert argv == [CLI, "status", "--json"]
    assert kwargs.get("shell", False) is False
    env = kwargs["env"]
    assert env["HERMES_HOME"] == str(chat.home)
    assert env["SOFTMAX_PLAYER_CREDENTIAL"] == "cga_t"
    assert env["AUTORESEARCH_PLAYER_ID"] == "ply_t"
    assert env["GATEWAY_ONLY"] == "kept" and env["PATH"] == os.environ["PATH"]


def test_gateway_profile_names_never_reach_the_cli(chat, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/opt/data")
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", "/elsewhere")
    monkeypatch.setenv("SOFTMAX_PLAYER_CREDENTIAL", "cga_gateway")
    monkeypatch.setenv("PYTHONPATH", "/opt/hermes/lib")
    _turn()
    HANDLERS["status"]({}, session_id="s1")
    env = chat.cli.calls[0][1]["env"]
    assert env["HERMES_HOME"] == str(chat.home)
    assert env["SOFTMAX_PLAYER_CREDENTIAL"] == "cga_t"
    assert "AUTORESEARCH_WORKSPACE" not in env and "PYTHONPATH" not in env


@pytest.mark.parametrize("which", ["host_root", "foreign_optimizer", "no_cli"])
def test_tools_refuse_a_profile_that_is_not_ours(chat, which):
    if which == "host_root":
        chat.resolver.home = chat.root  # /opt/data: the host root, not a profile
        (chat.root / ".env").write_text(f"AUTORESEARCH_CLI={CLI}\n")
    elif which == "foreign_optimizer":  # rohit-optimizer's, also installed as optimizer
        chat.resolver.home = _profile(chat.root / "other", "optimizer", ours=False)
    else:
        chat.resolver.home = _profile(chat.root / "fresh", "optimizer", env_lines=["X=1"])
    _turn()
    result = json.loads(HANDLERS["status"]({}, session_id="s1"))
    assert result["ok"] is False
    assert _propose()["ok"] is False
    assert chat.cli.calls == []


# --- 2. read tools: fixed argv, cut output ---


@pytest.mark.parametrize(("tool", "args", "argv"), [
    ("list_messages", {"after": 3}, ["comms", "list", "--after", "3"]),
    ("list_messages", {}, ["comms", "list", "--after", "0"]),
    ("get_message", {"key": "12"}, ["comms", "get", "12"]),
    ("message_status", {"message_id": M_ID}, ["comms", "status", M_ID]),
    ("resolve_ref", {"uri": "ar://claim/C-1", "rev": "abc"},
     ["comms", "resolve", "ar://claim/C-1", "--rev", "abc"]),
    ("resolve_ref", {"uri": "ar://claim/C-1", "rev": "@result"},
     ["comms", "resolve", "ar://claim/C-1"]),
    ("list_cards", {}, ["queue", "list"]),
    ("show_card", {"task_id": "t_1"}, ["queue", "show", "t_1"]),
    ("find_records", {"entity": "claim", "where": {"status": "open"}},
     ["records", "find", "claim", "--where", "status=open"]),
    ("show_record", {"record_id": "C-1"}, ["records", "show", "C-1"]),
    ("promotion_status", {}, ["promotion", "status"]),
    ("status", {}, ["status", "--json"]),
    ("baseline_report", {"since": "2026-10-09"},
     ["report", "baseline", "--since", "2026-10-09", "--json"]),
])
def test_each_read_tool_runs_one_fixed_argv(chat, tool, args, argv):
    _turn()
    assert json.loads(HANDLERS[tool](args, session_id="s1"))["ok"] is True
    assert chat.cli.calls[-1][0] == [CLI, *argv]


@pytest.mark.parametrize(("tool", "args"), [
    ("get_message", {"key": "--root=/etc"}),
    ("show_record", {"record_id": ""}),
    ("find_records", {"entity": "claim", "where": {"x;y": "1"}}),
    ("find_records", {"entity": "claim", "where": {"status": "-v"}}),
    ("baseline_report", {"since": "yesterday"}),
    ("list_messages", {"after": "3; rm"}),
])
def test_read_tools_never_pass_an_option_or_bad_value(chat, tool, args):
    _turn()
    assert json.loads(HANDLERS[tool](args, session_id="s1"))["ok"] is False
    assert chat.cli.calls == []


def test_read_tool_output_is_cut_to_12000_characters(chat):
    chat.cli.stdout["queue"] = json.dumps({"ok": True, "data": "x" * 50_000})
    _turn()
    assert len(HANDLERS["list_cards"]({}, session_id="s1")) <= 12_000


def test_the_game_is_passed_to_resolve(chat):
    with (chat.home / ".env").open("a") as handle:
        handle.write("AUTORESEARCH_GAME=gota\n")
    _turn()
    HANDLERS["resolve_ref"]({"uri": "ar://x/y"}, session_id="s1")
    assert chat.cli.calls[-1][0][-2:] == ["--game", "gota"]


# --- 3. pre_llm_call by platform ---


def test_api_server_turn_gets_the_brief_and_records_the_session(chat):
    context = _turn()
    assert context.startswith("<!-- autoresearch coach chat brief")
    assert coach.is_chat_session("s1")
    assert _turn() is None  # the brief stays in Hermes's history: not injected again


def test_cli_turn_still_gets_the_card_briefing(chat, tmp_path, monkeypatch):
    home = tmp_path / "worker-profile"
    runtime = home / "workspace" / ".supervisor" / "runtime"
    (runtime / "briefings").mkdir(parents=True)
    (runtime / "briefings" / "t_1.md").write_text("# Card t_1\n")
    monkeypatch.setitem(sys.modules["hermes_constants"].__dict__, "get_hermes_home", lambda: home)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "r_1")
    context = _turn(session="w1", line="work kanban task t_1", platform="cli", first=True)
    assert "autoresearch-briefing task=t_1" in context and "# Card t_1" in context
    assert not coach.is_chat_session("w1")


# --- 4. proposal and confirm ---


def test_a_proposal_is_shown_in_code_words_and_sent_on_confirm(chat):
    _turn()
    result = _propose(payload=STEER | {"proposed_topic": None})
    pid = result["proposal"]
    assert result["ok"] is True and pid.startswith("P-")
    assert chat.cli.sends() == []
    on_disk = [path for path in chat.root.rglob("*") if path.is_file()
               and "kill-steals" in path.read_text(errors="replace")]
    assert on_disk == [], "a proposal is never written to disk"

    early = _turn(line=f"confirm {pid}")  # before the coach saw it
    assert "nothing was sent" in early and chat.cli.sends() == []

    reply = _reply()
    assert reply.startswith(f"Proposal {pid}: steer, exactly as it will be sent:")
    assert reply.endswith("\n\n---\n\nok")  # the model's text comes after the block
    assert '"text": "Stop kill-steals"' in reply and '"urgency": "now"' in reply
    assert "something harmless" not in reply
    assert f"To send exactly this, reply: confirm {pid}\n\n---" in reply

    context = _turn(line=f"confirm {pid}\n")
    assert f"Sending {pid}" in context
    _settled()
    payload = json.dumps(STEER | {"proposed_topic": None})
    [send] = chat.cli.sends()
    assert send == [CLI, "comms", "inbound", "steer", "--json", payload, "--author", "coach:ply_t"]
    sent_env = next(kw["env"] for argv, kw in chat.cli.calls if argv == send)
    assert sent_env["HERMES_HOME"] == str(chat.home)
    done = _reply(text="Done.")
    assert "Sent " + pid + " as I-SENT0." in done
    assert f"confirm {pid}" not in done  # taken: no longer pending

    again = _turn(line=f"confirm {pid}")
    assert "already confirmed" in again and len(chat.cli.sends()) == 1


def test_an_invalid_payload_returns_the_schema_error_and_stores_nothing(chat):
    _turn()
    result = _propose(payload={"text": "x", "urgency": "soon"})
    assert result["ok"] is False and "urgency" in json.dumps(result["error"])
    assert _reply() is None


def test_unknown_fields_and_kinds_are_refused(chat):
    _turn()
    assert _propose(payload=STEER | {"note": "hidden"})["ok"] is False
    assert _propose(kind="seen", payload={"ids": [M_ID]})["ok"] is False
    assert _propose(kind="nudge", payload={})["ok"] is False
    assert _reply() is None


def test_a_reply_proposal_names_the_question_it_answers(chat):
    _turn()
    _propose(kind="reply", payload={"in_reply_to": M_ID, "choice": "2"})
    reply = _reply()
    assert f'"in_reply_to": "{M_ID}"' in reply and '"Which opening next?"' in reply
    assert '"choice": "2"' in reply


# --- 5. a confirm cannot be forged ---


def _shown_proposal(chat):
    _turn()
    pid = _propose()["proposal"]
    _reply()
    return pid


def test_a_confirm_in_another_session_sends_nothing(chat):
    pid = _shown_proposal(chat)
    _turn(session="s2", line=f"confirm {pid}")
    _settled()
    assert chat.cli.sends() == []
    _turn(line=f"confirm {pid}")
    _settled()
    assert len(chat.cli.sends()) == 1  # still pending in its own session


def test_a_confirm_on_the_cli_platform_sends_nothing(chat):
    pid = _shown_proposal(chat)
    _turn(session="s1", line=f"confirm {pid}", platform="cli")
    assert chat.cli.sends() == []


@pytest.mark.parametrize("line", [
    "confirm {pid} please", "please confirm {pid}", "Confirm {pid}", "confirm  {pid}",
    "confirm {pid}\nconfirm {pid}", "`confirm {pid}`",
])
def test_only_the_exact_whole_line_confirms(chat, line):
    pid = _shown_proposal(chat)
    _turn(line=line.format(pid=pid))
    _settled()
    assert chat.cli.sends() == []


def test_an_expired_proposal_sends_nothing(chat, monkeypatch):
    pid = _shown_proposal(chat)
    later = time.time() + proposals.EXPIRY_SECONDS + 1
    monkeypatch.setattr(coach, "_now", lambda: later)
    assert "nothing was sent" in _turn(line=f"confirm {pid}")
    assert chat.cli.sends() == []


def test_a_cancelled_proposal_sends_nothing(chat):
    pid = _shown_proposal(chat)
    assert "cancelled" in _turn(line=f"cancel {pid}")
    _turn(line=f"confirm {pid}")
    assert chat.cli.sends() == []


def test_two_confirms_at_once_send_once(chat):
    pid = _shown_proposal(chat)
    barrier = threading.Barrier(2)

    def confirm():
        barrier.wait()
        _turn(line=f"confirm {pid}")

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    _settled()
    assert len(chat.cli.sends()) == 1


def test_a_proposal_from_a_turn_with_an_empty_reply_is_not_confirmable(chat):
    _turn()
    pid = _propose()["proposal"]
    # The turn ends with an empty reply: Hermes does not call transform_llm_output.
    context = _turn(line=f"confirm {pid}")
    assert chat.cli.sends() == []
    assert f"Proposal {pid} was never shown" in context
    assert f"confirm {pid}" in _reply()  # shown now, at the end of this turn's reply
    _turn(line=f"confirm {pid}")
    _settled()
    assert len(chat.cli.sends()) == 1


def test_a_failed_send_is_reported_and_not_retried(chat):
    pid = _shown_proposal(chat)
    chat.cli.stdout["comms"] = json.dumps({"ok": False, "error": {
        "message": "invalid inbound message", "problems": ["urgency: must be one of now"]}})
    _turn(line=f"confirm {pid}")
    _settled()
    assert ("Sending " + pid + " failed: urgency: must be one of now. Nothing was sent."
            in _reply())


# --- 6. exact-line commands ---


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_pause_and_resume_lines_send_control(chat, action):
    context = _turn(line=f"  /{action}-research  ")
    assert f"Sending /{action}-research" in context
    _settled()
    [send] = chat.cli.sends()
    assert send == [CLI, "comms", "inbound", "control", "--json", json.dumps({"action": action}),
                    "--author", "coach:ply_t"]
    assert f"Sent /{action}-research as I-SENT0." in _reply()


def test_sends_go_out_in_the_order_the_coach_typed_them(chat, monkeypatch):
    """A slow first send must not land after a later one: a final pause would otherwise leave
    research running. Sends to one profile go through one FIFO worker."""
    fake = chat.cli

    def slow_pause(argv, env, cwd, timeout):
        if '{"action": "pause"}' in argv:
            time.sleep(0.3)
        return fake(argv, env=env, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(coach, "spawn", slow_pause)
    _turn(line="/pause-research")
    _turn(line="/resume-research")
    _settled()
    actions = [json.loads(argv[argv.index("--json") + 1])["action"] for argv in fake.sends()]
    assert actions == ["pause", "resume"]


def test_a_control_line_does_not_promise_a_minute_while_a_pause_skips_routing(chat):
    """Routing waits while a preflight or provenance pause holds: the control applies on the
    next full run, or when that pause clears."""
    context = _turn(line="/pause-research")
    _settled()
    assert "within about a minute" not in context
    assert "on its next full run" in context and "`status` shows why" in context


@pytest.mark.parametrize("line", ["please /pause-research", "/pause-research now", "/pause"])
def test_a_command_inside_other_text_does_nothing(chat, line):
    _turn(line=line)
    assert chat.cli.sends() == []


# --- 7. the chat tool allowlist ---


@pytest.mark.parametrize("tool", ["terminal", "write_file", "execute_code", "kanban_create",
                                  "delegate_task", "mcp_anything"])
def test_chat_sessions_may_use_only_coach_tools(chat, tool):
    _turn()
    directive = adapters.pre_tool_call(tool_name=tool, args={"command": "ls"}, session_id="s1")
    assert directive["action"] == "block"
    assert "coach chat" in directive["message"]


@pytest.mark.parametrize("tool", ["status", "propose_inbound", "list_messages", "tool_search",
                                  "tool_describe"])
def test_coach_tools_pass_the_chat_allowlist(chat, tool):
    _turn()
    assert adapters.pre_tool_call(tool_name=tool, args={}, session_id="s1") is None


@pytest.mark.parametrize(("args", "allowed"), [
    ({"calls": [{"name": "status", "arguments": {}}]}, True),
    ({"name": "list_cards", "arguments": {}}, True),
    ({"calls": json.dumps([{"name": "status"}, {"name": "show_card"}])}, True),
    ({"calls": [{"name": "status"}, {"name": "terminal"}]}, False),
    ({"name": "mcp_github_create_issue"}, False),
    ({"calls": "not json"}, False),
    ({}, False),
])
def test_a_tool_search_call_passes_only_for_coach_tools(chat, args, allowed):
    """Hermes v0.21.6 tool search can put plugin tools behind ``tool_call``."""
    _turn()
    directive = adapters.pre_tool_call(tool_name="tool_call", args=args, session_id="s1")
    assert (directive is None) is allowed


def test_a_turn_hermes_bound_to_api_server_is_restricted_even_unrecorded(chat, monkeypatch):
    gateway = types.ModuleType("gateway")
    context = types.ModuleType("gateway.session_context")
    context.get_session_env = lambda name, default="": "api_server"
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.session_context", context)
    directive = adapters.pre_tool_call(tool_name="terminal", args={}, session_id="unknown")
    assert directive["action"] == "block"


# --- 8. tools refuse outside the chat ---


def test_coach_tools_refuse_an_unrecorded_session(chat):
    assert json.loads(HANDLERS["status"]({}, session_id="s9"))["ok"] is False
    assert _propose(session="s9")["ok"] is False
    assert chat.cli.calls == []


def test_coach_tools_refuse_in_a_kanban_card(chat, monkeypatch):
    _turn()
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    assert json.loads(HANDLERS["status"]({}, session_id="s1"))["ok"] is False
    assert _propose()["ok"] is False
    _turn(line="/pause-research")
    _settled()
    assert chat.cli.calls == []


# --- 9. the chat budget ---


def _chat_session(home: Path, usd: float):
    add_session(home, "bridge", usd, source="api_server", started_at=time.time() - 3 * 86400)


def _set_cost(home: Path, usd: float):
    import sqlite3

    connection = sqlite3.connect(home / "state.db")
    connection.execute("UPDATE sessions SET estimated_cost_usd = ? WHERE id = 'bridge'", (usd,))
    connection.commit()
    connection.close()


def test_over_the_chat_budget_only_status_works(chat):
    _chat_session(chat.home, 10.0)  # yesterday's chat spend: not today's
    with (chat.home / ".env").open("a") as handle:
        handle.write("AUTORESEARCH_CHAT_DAILY_USD=3\n")
    assert "chat budget" not in _turn()  # the day's first snapshot
    assert json.loads(HANDLERS["list_cards"]({}, session_id="s1"))["ok"] is True
    _set_cost(chat.home, 13.10)  # +$3.10 today
    context = _turn(line="what now?")
    assert "chat budget for today is spent" in context
    for name, handler in HANDLERS.items():
        args = {"kind": "steer", "payload": STEER, "summary": "x"} if name == "propose_inbound" else {}
        result = json.loads(handler(args, session_id="s1"))
        if name == "status":
            assert result["ok"] is True
        else:
            assert result["ok"] is False and "chat budget" in result["error"]["message"]
    snapshot = json.loads((chat.home / "local" / "chat-spend.json").read_text())
    assert snapshot["base_usd"] == {"bridge": 10.0}


def test_a_confirm_still_sends_over_the_budget(chat):
    pid = _shown_proposal(chat)
    _chat_session(chat.home, 0.0)
    _turn(line="x")  # snapshot: the session started days ago, based at its cost now
    _set_cost(chat.home, 5.0)
    _turn(line=f"confirm {pid}")
    _settled()
    assert len(chat.cli.sends()) == 1


def test_register_adds_the_coach_tools_in_their_toolset():
    registered = []

    class FakeCtx:
        def register_hook(self, name, callback):
            pass

        def register_tool(self, **kwargs):
            registered.append(kwargs)

    adapters.register(FakeCtx())
    assert {tool["name"] for tool in registered} == coach.TOOL_NAMES
    assert {tool["toolset"] for tool in registered} == {"autoresearch_coach"}
    assert all(tool["schema"]["name"] == tool["name"] for tool in registered)
    assert len(coach.TOOL_NAMES) == 12


# --- fix round 1 ---


def test_a_slow_send_never_holds_the_hook_and_is_sent_once(chat, monkeypatch):
    """I1: Hermes bounds pre_llm_call (30 s); a send waiting on the canonical lock runs on a
    plugin thread, and its outcome reaches the coach on a later reply."""
    pid = _shown_proposal(chat)
    gate = threading.Event()
    real = chat.cli.__call__

    def slow(argv, env, cwd, timeout):
        if argv[1:3] == ["comms", "inbound"] and "--dry-run" not in argv:
            gate.wait(10)  # the canonical lock is held elsewhere
        return real(argv, env=env, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(coach, "spawn", slow)
    started = time.monotonic()
    context = _turn(line=f"confirm {pid}")
    assert time.monotonic() - started < 1.0
    assert f"Sending {pid}" in context and "I-SENT" not in context
    again = _turn(line=f"confirm {pid}")  # typed again while the first is still sending
    assert "already confirmed" in again
    assert "Sent " not in (_reply() or "")  # not done yet: nothing claims it was sent
    gate.set()
    _settled()
    assert len(chat.cli.sends()) == 1
    assert f"Since your last reply: Sent {pid} as I-SENT0." in _turn(line="and?")
    assert f"Sent {pid} as I-SENT0." in _reply()
    assert _reply() is None  # reported once


def test_the_send_timeout_is_bounded():
    assert 0 < coach.SEND_TIMEOUT_S <= 600


def test_a_proposal_cannot_be_confirmed_through_another_profile(chat):
    """M2: state is keyed by profile home and session id."""
    pid = _shown_proposal(chat)
    chat.resolver.home = _profile(chat.root / "second", "autoresearch")
    _turn(line=f"confirm {pid}")
    _settled()
    assert chat.cli.sends() == []
    assert not coach.is_chat_session("s9")


def test_a_pending_proposal_is_shown_again_on_every_reply(chat):
    """M5: the plugin's rendering is always the last one the coach sees for a pending id."""
    _turn()
    pid = _propose()["proposal"]
    first = _reply(turn="t1")
    fake = f"Proposal {pid}: something else. To send exactly this, reply: confirm {pid}"
    second = _reply(text=fake, turn="t2")
    assert second.startswith(f"Proposal {pid}: steer, exactly as it will be sent:")
    assert second.endswith(fake)  # the fake comes after the real rendering
    assert second.count('"Stop kill-steals"') == 1 and first.count('"Stop kill-steals"') == 1
    _turn(line=f"cancel {pid}")
    assert _reply(turn="t3") is None


def test_coach_tools_are_off_when_another_plugin_owns_a_name(chat, monkeypatch):
    """M3: the allowlist matches names, so every coach name must resolve to our handler."""
    entries = {name: types.SimpleNamespace(handler=handler) for name, handler in HANDLERS.items()}
    registry_module = types.ModuleType("tools.registry")
    registry_module.registry = types.SimpleNamespace(get_entry=lambda name: entries.get(name))
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", registry_module)
    _turn()
    assert adapters.pre_tool_call(tool_name="status", args={}, session_id="s1") is None
    entries["status"] = types.SimpleNamespace(handler=lambda args: "theirs")
    directive = adapters.pre_tool_call(tool_name="status", args={}, session_id="s1")
    assert directive["action"] == "block" and "status" in directive["message"]
    assert adapters.pre_tool_call(tool_name="list_cards", args={}, session_id="s1")["action"] == "block"
    assert "another plugin owns the tool names status" in _turn(line="hi")


def test_a_chat_turn_gets_at_most_eight_tool_rounds(chat):
    """M4 (coach-chat-role §4): rounds are model calls (api_request_id)."""
    _turn()

    def call(turn, request):
        return adapters.pre_tool_call(tool_name="status", args={}, session_id="s1",
                                      turn_id=turn, api_request_id=request)

    for round_number in range(8):
        assert call("t1", f"r{round_number}") is None
        assert call("t1", f"r{round_number}") is None  # a parallel call in the same round
    blocked = call("t1", "r8")
    assert blocked["action"] == "block" and "8 tool rounds" in blocked["message"]
    assert call("t2", "r0") is None  # a new turn starts again


# --- fix round 2 ---


@pytest.mark.parametrize("tail", [
    "All set. <!-- ", "Here it is:\n<details><summary>more</summary>", "<div hidden>",
    "Look:\n```\nunclosed code", "~~~~\nunclosed tilde fence", "<span style='display:none'>",
    "- item\n  ```\n<!--",  # a list-item fence the renderer closes before the comment
    "```\nx\n```\r<!--",  # a lone CR ends a line in CommonMark
    "~~~\r~~~\r<!--",
    "a\r\n<!--\r\nb",  # CRLF
])
def test_model_text_cannot_swallow_the_plugin_block(chat, tail):
    """N1: the plugin's block comes first; the model's text after it is escaped."""
    _turn()
    pid = _propose()["proposal"]
    fake = f"Proposal {pid}: steer, harmless. To send exactly this, reply: confirm {pid}\n"
    reply = _reply(text=fake + tail)
    block, rule, model = reply.partition("\n\n---\n\n")
    assert rule and block.startswith(f"Proposal {pid}: steer, exactly as it will be sent:")
    assert '"text": "Stop kill-steals"' in block
    assert block.endswith(f"To send exactly this, reply: confirm {pid}")
    assert "<" not in model and "\r" not in model  # no HTML can open in the model's text
    assert model.startswith(f"Proposal {pid}: steer, harmless")


def test_neutralize_escapes_every_line_and_normalizes_line_ends():
    text = "a < b & c\r\n```python\rif a < b:\n```"
    assert coach.neutralize(text) == "a &lt; b &amp; c\n```python\nif a &lt; b:\n```"


def test_a_reply_with_no_plugin_lines_is_left_alone(chat):
    _turn()
    assert _reply(text="a <b>bold</b> answer") is None


def test_a_send_that_breaks_unexpectedly_is_reported_as_unknown(chat, monkeypatch):
    """N4: an outcome the plugin cannot know is never reported as "nothing was sent"."""
    pid = _shown_proposal(chat)

    def broken(where, kind, payload):
        raise OSError("disk full")

    monkeypatch.setattr(coach, "send_inbound", broken)
    _turn(line=f"confirm {pid}")
    _settled()
    reply = _reply()
    assert "may or may not have been sent" in reply and "Nothing was sent" not in reply


def test_a_timed_out_send_is_reported_as_unknown(chat, monkeypatch):
    pid = _shown_proposal(chat)
    coach_spawn = coach.spawn

    def timeout(argv, env, cwd, timeout):
        if "--dry-run" not in argv:
            raise subprocess.TimeoutExpired(argv, timeout)
        return coach_spawn(argv, env, cwd, timeout)

    monkeypatch.setattr(coach, "spawn", timeout)
    _turn(line=f"confirm {pid}")
    _settled()
    reply = _reply()
    assert "timed out" in reply and "may or may not have been sent" in reply
    assert "message_status" in reply


def _alive(pid: int) -> bool:
    """True while ``pid`` runs (a killed process waiting to be reaped counts as dead)."""
    state = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                           check=False)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def test_spawn_kills_the_whole_process_group_on_timeout(tmp_path):
    """N4: the CLI's own children (git commit, git push) die with it."""
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "cli.sh"  # a file, not `sh -c` (tests/unit/gota/conftest.py guard)
    script.write_text(f"sleep 60 & echo $! > {pid_file}.tmp && mv {pid_file}.tmp {pid_file}\nwait\n")
    with pytest.raises(subprocess.TimeoutExpired):
        coach.spawn(["/bin/sh", str(script)], {"PATH": "/bin:/usr/bin"}, str(tmp_path), 3)
    child = int(pid_file.read_text())  # written well inside the 3 s, even on a busy machine
    deadline = time.monotonic() + 15
    while _alive(child):
        assert time.monotonic() < deadline, "the grandchild survived the timeout"
        time.sleep(0.1)


def _cli_script(tmp_path, body: str) -> dict[str, str]:
    """A profile env whose AUTORESEARCH_CLI is a script file running ``body``."""
    cli = tmp_path / "cli.py"
    cli.write_text(f"import sys\n{body}\n")
    shim = tmp_path / "autoresearch"
    shim.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(cli)!r})\n")
    shim.chmod(0o755)
    return {"AUTORESEARCH_CLI": str(shim), "HERMES_HOME": str(tmp_path),
            "PATH": "/bin:/usr/bin", "AUTORESEARCH_PLAYER_ID": "ply_t"}


def test_a_cli_traceback_after_writing_is_reported_as_unknown(tmp_path):
    """N4a: only problems or a bad-arguments exit mean nothing was written."""
    inbox = tmp_path / "inbox.json"
    env = _cli_script(tmp_path, f"open({str(inbox)!r}, 'w').write('{{}}')\nraise RuntimeError('push')")
    with pytest.raises(coach.SendUnknown):
        coach.send_inbound((tmp_path, env), "steer", STEER)
    assert inbox.exists()


@pytest.mark.parametrize(("body", "error"), [
    (('print(\'{"ok": false, "error": {"message": "invalid", "problems": ["urgency: bad"]}}\')'
      '\nsys.exit(1)'), coach.Refused),
    ('print(\'{"ok": false, "error": {"message": "a card worker cannot send"}}\')\nsys.exit(2)',
     coach.Refused),
    ('print(\'{"ok": false, "error": {"message": "git push failed"}}\')\nsys.exit(1)',
     coach.SendUnknown),
    ("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)", coach.SendUnknown),
])
def test_only_a_refusal_means_nothing_was_sent(tmp_path, body, error):
    env = _cli_script(tmp_path, body)
    with pytest.raises(error):
        coach.send_inbound((tmp_path, env), "steer", STEER)


def test_note_text_cannot_form_markup_in_the_plugin_block(chat):
    """A CLI reason in a note is escaped: ``['now']`` plus a model link-reference definition
    must not become a link, and no HTML can open."""
    pid = _shown_proposal(chat)
    chat.cli.stdout["comms"] = json.dumps({"ok": False, "error": {
        "message": "invalid", "problems": ["urgency: must be one of ['now'] <b>x</b> & y"]}})
    _turn(line=f"confirm {pid}")
    _settled()
    reply = _reply(text="[\'now\']: https://example.invalid/phish")
    block, _, model = reply.partition("\n\n---\n\n")
    assert "\\['now']" in block and "&lt;b>" in block and "&amp; y" in block
    assert "<b>" not in block
    sending = next(line for line in block.split("\n\n") if line.startswith("Sending"))
    assert not re.search(r"(?<!\\)\[", sending)  # every [ in the note is escaped
    assert model.startswith("[")  # the model's own text: escaped for HTML, a stray link-ref def
