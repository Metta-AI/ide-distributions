"""``research serve``, the local seam server (plan Task 6.2, design §10, A.4, A.11, A6.1).

Every test runs a :class:`SeamServer` on a background thread against :class:`FakeHermesRuns`
and a fresh workspace with ``cow_fake`` active, and drives it through the
:class:`ScriptedClient` (Task 6.3), so the client is exercised by the same tests.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

import pytest

from research_core import contract
from research_core.client import ScriptedClient, SeamError
from research_core.serve import SeamServer
from research_core.store import coworlds, goals, notices, tasks, ui_state
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.tools.base import ToolContext
from research_core.watcher.hermes_api import FakeHermesRuns

HARNESS = Actor.harness()
COWORLD_ID = "cow_fake"


@dataclass
class Seam:
    server: SeamServer
    hermes: FakeHermesRuns
    ctx: ToolContext
    client: ScriptedClient

    @property
    def wstore(self) -> Store:
        return self.ctx.wstore

    @property
    def store(self) -> Store:
        return self.ctx.store

    def run_input(self, run_id: str) -> str:
        return self.hermes.runs[run_id].input

    def starts(self) -> int:
        return sum(1 for method, _ in self.hermes.calls if method == "start")

    def say(self, text, context=None):
        receipt = self.client.say(text, context)
        return receipt | self.client.wait_admission(receipt["op_id"])


def make_ctx(tmp_path, coworld: bool = True) -> ToolContext:
    ctx = ToolContext.from_env(
        "orchestrator", {"IDE_WORKSPACE": str(tmp_path), "IDE_SESSION_ID": "test-harness"}
    )
    ctx.workspace.ensure_dirs()
    if coworld:
        coworlds.register(ctx.wstore, HARNESS, COWORLD_ID, folder=COWORLD_ID)
        coworlds.activate(ctx.wstore, HARNESS, COWORLD_ID)
        ctx.workspace.coworld(COWORLD_ID).ensure_dirs()
    return ctx


def start_seam(tmp_path, *, coworld: bool = True) -> Seam:
    ctx = make_ctx(tmp_path, coworld=coworld)
    hermes = FakeHermesRuns()
    server = SeamServer(ws=ctx.workspace, hermes=hermes)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return Seam(server, hermes, ctx, ScriptedClient(server.url))


@pytest.fixture
def seam(tmp_path):
    seam = start_seam(tmp_path)
    yield seam
    seam.server.shutdown()


def context_block(text: str) -> dict:
    parsed, error = contract.find_block(text)
    assert error is None and isinstance(parsed, dict), text
    return parsed


# --- session-open and message -------------------------------------------------------------


def test_session_open_registers_a_chat_session_with_its_context(seam):
    session_id = seam.client.open(
        client_id="ide-1",
        kind="ide",
        guide="client-ide",
        session_context={"mode": {"name": "research"}},
        capabilities={"views": ["replay"], "panels": ["markdown"]},
    )
    assert session_id.startswith("api_")
    from research_core.store import sessions

    row = sessions.read(seam.wstore, session_id)
    assert row["kind"] == "chat" and row["profile"] == "orchestrator"
    assert row["coworld_id"] == COWORLD_ID
    assert row["session_context"] == {"mode": {"name": "research"}}
    assert row["client_capabilities"] == {"views": ["replay"], "panels": ["markdown"]}


def test_session_open_refuses_a_missing_client(seam):
    with pytest.raises(SeamError) as info:
        seam.client._post("/session-open", {"capabilities": {}})
    assert info.value.status == 400 and "client" in info.value.body["error"]


def test_message_stamps_client_coworld_and_version_into_the_block(seam):
    seam.client.open(client_id="coplay-7", kind="desktop", guide="client-coplay")
    result = seam.say(
        "what is the champion?",
        context={"contract_version": 1, "mode": {"name": "coach", "instruction": "brief"}},
    )
    assert result["delivered"] == "queued" and result["problems"] == []
    text = seam.run_input(result["run_id"])
    assert text.startswith("what is the champion?\n\nContext v1:\n")
    block = context_block(text)
    assert block["client"] == {"id": "coplay-7", "kind": "desktop", "guide": "client-coplay"}
    assert block["coworld"] == COWORLD_ID
    assert block["contract_version"] == 1
    assert block["mode"] == {"name": "coach", "instruction": "brief"}
    # Only the server's three stamps: the turn hook adds notices and the changed-since note.
    assert set(block) == {"client", "coworld", "contract_version", "mode"}
    from research_core.store import sessions

    assert sessions.read(seam.wstore, result["session_id"])["run_id"] == result["run_id"]


def test_message_without_a_context_still_carries_the_stamps(seam):
    seam.client.open()
    result = seam.say("hello")
    block = context_block(seam.run_input(result["run_id"]))
    assert block == {
        "client": {"id": "scripted", "kind": "test"},
        "coworld": COWORLD_ID,
        "contract_version": 1,
    }


def test_malformed_context_yields_200_with_problems_and_the_run_still_starts(seam):
    seam.client.open()
    result = seam.say(
        "go",
        context={
            "contract_version": 1,
            "bogus": 1,
            "mode": {"name": "x" * 41},
            "on_screen": ["G-1"],
            "client": {"id": "spoofed", "kind": "other"},
        },
    )
    assert result["delivered"] == "queued"
    assert any("bogus" in p for p in result["problems"])
    assert any("at most 40 characters" in p for p in result["problems"])
    block = context_block(seam.run_input(result["run_id"]))
    assert "mode" not in block and block["on_screen"] == ["G-1"]
    assert block["client"] == {"id": "scripted", "kind": "test"}  # the server's stamp wins
    # A context that is not even an object: the stamps alone make the block.
    seam.hermes.complete(result["run_id"])
    result = seam.client._post(
        "/message", {"session_id": seam.client.session_id, "message": "x", "context": "junk"}
    )
    assert result["delivered"] == "queued"
    assert result["problems"] == ["context: must be a JSON object"]
    result |= seam.client.wait_admission(result["op_id"])
    assert context_block(seam.run_input(result["run_id"]))["coworld"] == COWORLD_ID


def test_client_showing_another_coworld_is_told_so(seam):
    seam.client.open()
    result = seam.say("hi", context={"contract_version": 1, "coworld": "cow_other"})
    assert context_block(seam.run_input(result["run_id"]))["coworld"] == COWORLD_ID
    assert any("cow_other" in p and COWORLD_ID in p for p in result["problems"])


def test_no_active_coworld_leaves_the_client_coworld_in_place(tmp_path):
    seam = start_seam(tmp_path, coworld=False)
    try:
        seam.client.open()
        context = {"contract_version": 1, "coworld": "cow_p"}
        result = seam.say("set up pudge", context=context)
        assert context_block(seam.run_input(result["run_id"]))["coworld"] == "cow_p"
        assert seam.client.health()["coworld"] is None
    finally:
        seam.server.shutdown()


def test_second_message_waits_until_the_previous_run_finishes(seam):
    seam.client.open()
    first = seam.say("start the work")
    second = seam.client.say("and also check the forum")
    assert second["delivered"] == "queued" and second["run_id"] is None
    assert seam.starts() == 1
    seam.hermes.complete(first["run_id"], "done")
    dispatched = seam.client.wait_admission(second["op_id"])
    assert dispatched["run_id"] != first["run_id"]
    assert seam.starts() == 2


def test_message_to_an_unknown_session_is_404(seam):
    with pytest.raises(SeamError) as info:
        seam.client._post("/message", {"session_id": "api_0_nope", "message": "x"})
    assert info.value.status == 404


def test_busy_gateway_preserves_confirmed_message_until_available(seam):
    seam.client.open()
    seam.hermes.set_busy(True)
    receipt = seam.client.say("hello")
    assert receipt["delivered"] == "queued"
    with pytest.raises(TimeoutError):
        seam.client.wait_admission(receipt["op_id"], timeout=0.7)
    seam.hermes.set_busy(False)
    assert seam.client.wait_admission(receipt["op_id"])["status"] == "running"


# --- stream and snapshot --------------------------------------------------------------------


def test_stream_delivers_new_events_as_sse_with_ids_and_resumes(seam):
    before = seam.client.events("workspace", after=0)
    assert before and before[-1]["kind"] == "coworld.switched"
    assert [e["seq"] for e in before] == list(range(1, len(before) + 1))
    assert all(e["store"] == "workspace" for e in before)
    last = before[-1]["seq"]
    notices.add(seam.wstore, HARNESS, text="Submitted V-1", refs=["V-1"])
    new = seam.client.events("workspace", after=last)
    assert [e["kind"] for e in new] == ["notice"]
    assert new[0]["seq"] == last + 1 and new[0]["payload"]["text"] == "Submitted V-1"
    assert seam.client.events("workspace", after=new[0]["seq"]) == []


def test_stream_raw_frames_and_last_event_id_header(seam):
    notices.add(seam.wstore, HARNESS, text="one")
    notices.add(seam.wstore, HARNESS, text="two")
    last = seam.wstore.last_seq()
    request = urllib.request.Request(f"{seam.server.url}/stream?store=workspace&after=0")
    request.add_header("Last-Event-ID", str(last - 1))
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        lines = []
        while len(lines) < 2:  # the first frame's id and data; comments and blanks skipped
            line = response.readline().decode()
            if line.strip() and not line.startswith(":"):
                lines.append(line)
    frame = lines
    assert frame[0] == f"id: {last}\n"
    assert frame[1].startswith("data: ")
    assert json.loads(frame[1][len("data: ") :])["payload"]["text"] == "two"


def test_stream_of_the_coworld_store_and_snapshot_seq_matches(seam):
    goals.create(seam.store, HARNESS, title="Win", end_state="margin > 0", reason="r")
    events = seam.client.events("coworld", after=0)
    assert [e["kind"] for e in events] == ["goal.created"]
    assert events[0]["store"] == "coworld"
    snapshot = seam.client.snapshot("coworld")
    assert snapshot["store"] == "coworld" and snapshot["coworld_id"] == COWORLD_ID
    assert snapshot["seq"] == events[-1]["seq"]
    assert snapshot["records"]["goals"][0]["title"] == "Win"
    workspace = seam.client.snapshot("workspace")
    assert workspace["seq"] == seam.client.events("workspace", after=0)[-1]["seq"]
    assert workspace["records"]["coworlds"][0]["coworld_id"] == COWORLD_ID


def test_stream_and_snapshot_refuse_bad_store_names(seam):
    with pytest.raises(SeamError) as info:
        seam.client.snapshot("nope")
    assert info.value.status == 400
    with pytest.raises(SeamError) as info:
        seam.client.events("workspace", after="x")
    assert info.value.status == 400


def test_coworld_stream_without_an_active_coworld_is_404(tmp_path):
    seam = start_seam(tmp_path, coworld=False)
    try:
        with pytest.raises(SeamError) as info:
            seam.client.snapshot("coworld")
        assert info.value.status == 404 and "no active Coworld" in info.value.body["error"]
    finally:
        seam.server.shutdown()


# --- UI actions ----------------------------------------------------------------------------


def open_ask(seam, conversation: str, ask_id: str = "ask_1") -> None:
    ui_state.patch(
        seam.wstore,
        HARNESS,
        conversation,
        [
            {"op": "add", "path": "/asks", "value": {}},
            {
                "op": "add",
                "path": f"/asks/{ask_id}",
                "value": {"question": "Submit V-2?", "options": ["yes", "no"], "status": "open"},
            },
        ],
    )


def test_ui_action_answer_patches_the_ask_and_steers_the_live_run(seam):
    session_id = seam.client.open()
    run = seam.say("propose a submission")
    open_ask(seam, session_id)
    result = seam.client.answer("ask_1", "yes")
    assert result["accepted"] is True and result["delivered"] == "steer"
    assert result["conversation"] == session_id
    ask = ui_state.get(seam.wstore, session_id)["doc"]["asks"]["ask_1"]
    assert ask["status"] == "answered" and ask["answer"] == "yes"
    assert ask["answered_by"] == "user:local"
    assert seam.hermes.steered[run["run_id"]] == ["[UI action] answer to ask_1: yes"]
    assert seam.starts() == 1


def test_ui_action_answer_starts_a_run_when_none_is_live_and_finds_workspace_asks(seam):
    seam.client.open()
    open_ask(seam, "workspace", "ask_w")
    result = seam.client.answer("ask_w", {"choice": "no"})
    assert result["delivered"] == "start" and result["conversation"] == "workspace"
    assert seam.run_input(result["run_id"]) == '[UI action] answer to ask_w: {"choice": "no"}'
    assert ui_state.get(seam.wstore, "workspace")["doc"]["asks"]["ask_w"]["answer"] == {
        "choice": "no"
    }
    with pytest.raises(SeamError) as info:  # the first answer wins
        seam.client.answer("ask_w", "yes")
    assert info.value.status == 409
    with pytest.raises(SeamError) as info:
        seam.client.answer("ask_missing", "yes")
    assert info.value.status == 404


def test_reaction_writes_a_ui_state_change_and_starts_no_run(seam):
    session_id = seam.client.open()
    seq = seam.wstore.last_seq()
    result = seam.client.react("msg-42", "thumbs_up")
    assert result["accepted"] is True and result["delivered"] == "none"
    assert seam.starts() == 0 and seam.hermes.runs == {}
    events = seam.wstore.events_after(seq)
    assert [e.kind for e in events] == ["ui.patch"]
    assert events[0].payload["ops"][-1]["path"] == "/reactions/msg-42"
    doc = ui_state.get(seam.wstore, session_id)["doc"]
    assert doc["reactions"]["msg-42"]["value"] == "thumbs_up"
    assert doc["reactions"]["msg-42"]["by"] == "user:local"
    seam.client.react("msg-43", "thumbs_down")
    assert set(ui_state.get(seam.wstore, session_id)["doc"]["reactions"]) == {"msg-42", "msg-43"}


def test_ui_action_unknown_action_and_bad_payload(seam):
    seam.client.open()
    with pytest.raises(SeamError) as info:
        seam.client._post(
            "/ui-action", {"session_id": seam.client.session_id, "action": "dance", "payload": {}}
        )
    assert info.value.status == 400 and "answer, reaction" in info.value.body["error"]
    with pytest.raises(SeamError) as info:
        seam.client._post(
            "/ui-action", {"session_id": seam.client.session_id, "action": "answer", "payload": {}}
        )
    assert info.value.status == 400 and "ask_id" in info.value.body["error"]


# --- store apply ----------------------------------------------------------------------------


def propose_task(seam) -> dict:
    goals.create(seam.store, HARNESS, title="Win", end_state="margin > 0", reason="r")
    return tasks.create(
        seam.store,
        Actor.session("api_1_researcher"),
        type="research",
        title="Try a wider search",
        description="d",
        goal_id=1,
        status="proposed",
    )


def test_store_apply_with_a_user_actor_approves_a_proposed_task(seam):
    task = propose_task(seam)
    assert task["status"] == "proposed"
    result = seam.client.apply("user:james", "tasks.approve", {"task_id": "T-1"})
    assert result["ok"] is True and result["store"] == "coworld"
    assert result["record"]["status"] == "unclaimed"
    assert result["record"]["approved_by"] == "user:james"
    assert result["seq"] == seam.store.last_seq()
    assert seam.store.events_after(result["seq"] - 1)[0].kind == "task.approved"


def test_store_apply_refuses_outside_the_allow_list_and_session_actors(seam):
    propose_task(seam)
    with pytest.raises(SeamError) as info:
        seam.client.apply("user:james", "goals.create", {"title": "x"})
    assert info.value.status == 400 and "not an op store apply runs" in info.value.body["error"]
    with pytest.raises(SeamError) as info:
        seam.client.apply("session:api_1_x", "tasks.approve", {"task_id": 1})
    assert info.value.status == 400 and "sessions use the tools" in info.value.body["error"]
    with pytest.raises(SeamError) as info:
        seam.client.apply("user:james", "tasks.approve", {"task_id": "G-1"})
    assert info.value.status == 400 and info.value.body["problems"]
    assert tasks.read(seam.store, 1)["status"] == "proposed"


def test_store_apply_pause_writes_the_workspace_setting(seam):
    result = seam.client.apply("user:james", "workspace.pause", {"cites": "ui:pause-button"})
    assert result["store"] == "workspace" and result["record"]["value"] is True
    from research_core.store import settings

    assert settings.get(seam.wstore, "paused") is True


# --- the rest -----------------------------------------------------------------------------


def test_health_and_run_status(seam):
    health = seam.client.health()
    assert health == {
        "ok": True,
        "profile": "orchestrator",
        "coworld": COWORLD_ID,
        "contract_version": 1,
    }
    seam.client.open()
    run = seam.say("hi")
    status = seam.client.run(run["run_id"])
    assert status["status"] == "running" and status["terminal"] is False
    seam.hermes.complete(run["run_id"], "all done")
    status = seam.client.run(run["run_id"])
    assert status["status"] == "completed" and status["output"] == "all done"
    assert seam.client.run("run_missing")["status"] == "unknown"


def test_client_page_is_served_at_root_and_ui(seam):
    for path in ("/", "/ui"):
        with urllib.request.urlopen(f"{seam.server.url}{path}", timeout=2) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            page = response.read().decode("utf-8")
        assert "<title>Orchestrator desk</title>" in page
        # The page opens a Session on load and reads the two routes added for it.
        assert "/session-open" in page and "/status" in page and "/stream?store=" in page


def test_status_reports_the_watcher_fields_from_the_workspace_store(seam, monkeypatch, capsys):
    from research_core.store import settings
    from research_core.store._common import now_iso
    from research_core.watcher import cli as watcher_cli

    assert seam.client._get("/status") == {
        "ok": True,
        "heartbeat": None,
        "heartbeat_age_s": None,
        "has_work": False,
        "waiting_on": [],
        "paused": False,
        "paused_drained": False,
        "sleep_started": None,
    }
    seam.wstore.set_meta("heartbeat", now_iso())
    seam.wstore.set_meta("has_work", "1")
    seam.wstore.set_meta("has_work_reasons", json.dumps(["wake:queued", "task:T-1 xp"]))
    seam.wstore.set_meta("paused_drained", "1")
    settings.update(seam.wstore, HARNESS, "paused", True, cites="test")
    status = seam.client._get("/status")
    assert status["has_work"] is True and status["waiting_on"] == ["wake:queued", "task:T-1 xp"]
    assert status["paused"] is True and status["paused_drained"] is True
    assert 0 <= status["heartbeat_age_s"] < 60
    # It is what ``research watcher status`` prints for the same workspace.
    monkeypatch.setenv("IDE_WORKSPACE", str(seam.ctx.workspace.root))
    assert watcher_cli.run_status(None) == 0
    printed = json.loads(capsys.readouterr().out)
    assert set(printed) == set(status)
    assert {k: v for k, v in printed.items() if k != "heartbeat_age_s"} == {
        k: v for k, v in status.items() if k != "heartbeat_age_s"
    }


def test_unknown_get_paths_still_404_beside_the_page(seam):
    for path in ("/nowhere", "/ui/anything", "/index.html", "/ui.html"):
        with pytest.raises(SeamError) as info:
            seam.client._get(path)
        assert info.value.status == 404, path


def test_fresh_workspace_storage_is_ready_before_concurrent_workers_start(tmp_path):
    ctx = make_ctx(tmp_path, coworld=False)
    assert not ctx.workspace.store_path.exists()
    server = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns())
    # Existing fixtures register a Coworld first, hiding first-open SQLite journal/schema races.
    assert ctx.workspace.store_path.exists()
    with server.stores() as stores:
        assert stores.wstore.schema_status().ok
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(SeamError) as info:
            ScriptedClient(server.url)._get("/nowhere")
        assert info.value.status == 404
    finally:
        server.shutdown()


def test_unknown_routes_and_bad_json_are_json_errors(seam):
    with pytest.raises(SeamError) as info:
        seam.client._get("/nowhere")
    assert info.value.status == 404
    request = urllib.request.Request(f"{seam.server.url}/message", data=b"{not json", method="POST")
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request, timeout=2)
    assert info.value.code == 400
    assert "JSON" in json.loads(info.value.read())["error"]


def test_serve_command_is_registered():
    from research_core import cli

    parser = cli.build_parser()
    args = parser.parse_args(["serve", "--port", "0", "--profile", "orchestrator"])
    assert args.port == 0 and args.profile == "orchestrator" and callable(args.run)
