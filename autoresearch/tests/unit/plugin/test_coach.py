"""The IDE coach chat (plugin.coach): profile environment, read tools, confirm-gated
proposals, exact-line commands, the chat tool allowlist and the chat budget."""

import json
import os
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
    monkeypatch.setattr(coach.subprocess, "run", fake)
    monkeypatch.setattr(coach, "STORE", proposals.Store())
    monkeypatch.setattr(coach, "_sessions", {})
    return types.SimpleNamespace(home=home, root=tmp_path / "root", resolver=resolver, cli=fake)


HANDLERS = {name: handler for name, _, handler in coach.tool_specs()}


def _turn(session="s1", line="hi", platform="api_server", first=False):
    return adapters.pre_llm_call(
        is_first_turn=first, session_id=session, platform=platform, user_message=line,
        model="m", turn_id="t",
    )


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
    assert reply.startswith("ok\n\n")
    assert "Stop kill-steals" in reply and "urgency now" in reply
    assert "something harmless" not in reply
    assert reply.rstrip().endswith(f"confirm {pid}")

    context = _turn(line=f"confirm {pid}\n")
    payload = json.dumps(STEER | {"proposed_topic": None})
    [send] = chat.cli.sends()
    assert send == [CLI, "comms", "inbound", "steer", "--json", payload, "--author", "coach:ply_t"]
    sent_env = next(kw["env"] for argv, kw in chat.cli.calls if argv == send)
    assert sent_env["HERMES_HOME"] == str(chat.home)
    assert "sent it as I-SENT0" in context
    assert "Sent " + pid + " as I-SENT0." in _reply(text="Done.")

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
    assert M_ID in reply and '"Which opening next?"' in reply and 'choice "2"' in reply


# --- 5. a confirm cannot be forged ---


def _shown_proposal(chat):
    _turn()
    pid = _propose()["proposal"]
    _reply()
    return pid


def test_a_confirm_in_another_session_sends_nothing(chat):
    pid = _shown_proposal(chat)
    _turn(session="s2", line=f"confirm {pid}")
    assert chat.cli.sends() == []
    _turn(line=f"confirm {pid}")
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
    assert len(chat.cli.sends()) == 1


def test_a_failed_send_is_reported_and_not_retried(chat):
    pid = _shown_proposal(chat)
    chat.cli.stdout["comms"] = json.dumps({"ok": False, "error": {"message": "push failed"}})
    context = _turn(line=f"confirm {pid}")
    assert "sending it failed: push failed" in context
    assert "Nothing was sent" in _reply()


# --- 6. exact-line commands ---


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_pause_and_resume_lines_send_control(chat, action):
    context = _turn(line=f"  /{action}-research  ")
    [send] = chat.cli.sends()
    assert send == [CLI, "comms", "inbound", "control", "--json", json.dumps({"action": action}),
                    "--author", "coach:ply_t"]
    assert "I-SENT0" in context
    assert f"Sent /{action}-research as I-SENT0." in _reply()


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
