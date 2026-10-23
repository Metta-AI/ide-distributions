import json
import sys
from pathlib import Path

import jsonschema
import yaml

from play import fresh, plugin
from play.context import Context
from play.launch import lock_sha
from play.limits import NAME_MAX, POLICY_NAME_MAX, SUFFIX_LENGTH

DISTRIBUTION = Path(__file__).resolve().parents[1]
PLAY_TOOLS = {
    "starter_fetch": ([], {"player"}),
    "program_show": ([], {"program"}),
    "program_change": (["text", "change"], {"text", "change"}),
    "program_edit": (["edits", "change"], {"edits", "change"}),
    "program_versions": ([], {"program"}),
    "program_upload": ([], {"name"}),
    "practice_request": ([], {"program", "episodes", "seats", "attempt"}),
    "practice_status": ([], {"request_id", "wait_seconds"}),
    "program_release": (["program", "user_said_yes"], {"program", "user_said_yes"}),
    "task_update": (
        ["step", "status", "detail"],
        {"title", "step", "status", "detail", "version", "outcome"},
    ),
    "fresh_start": (["confirm"], {"confirm"}),
}


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, schema, handler, description):
        assert name not in self.tools
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "description": description,
        }


def test_register_lists_exactly_the_play_tools_with_their_schemas():
    ctx = Ctx()
    plugin.register(ctx)
    assert list(ctx.tools) == list(PLAY_TOOLS)
    for name, (required, properties) in PLAY_TOOLS.items():
        tool = ctx.tools[name]
        schema = tool["schema"]
        assert tool["toolset"] == "play" and schema["name"] == name
        assert schema["description"] == tool["description"]
        parameters = schema["parameters"]
        jsonschema.Draft202012Validator.check_schema(parameters)
        assert parameters["type"] == "object" and parameters["additionalProperties"] is False
        assert parameters["required"] == required and set(parameters["properties"]) == properties
    for name in ("practice_request", "program_release"):
        assert "never retry" in ctx.tools[name]["description"]


def test_schemas_accept_and_refuse_the_documented_shapes():
    def valid(name, arguments):
        validator = jsonschema.Draft202012Validator(plugin.schema(name)["parameters"])
        return validator.is_valid(arguments)

    assert valid("starter_fetch", {}) and valid("starter_fetch", {"player": "baseline"})
    assert valid("program_upload", {"name": "my-first"}) and valid("program_upload", {})
    assert not valid("program_upload", {"name": "My First"})
    # The real limit: the name plus the platform's "-<8 characters>" suffix fits 64.
    assert valid("program_upload", {"name": "a" * NAME_MAX})
    assert not valid("program_upload", {"name": "a" * (NAME_MAX + 1)})
    assert NAME_MAX + 1 + SUFFIX_LENGTH == POLICY_NAME_MAX
    for named in ("towerless v2", "towerless:v2", "Towerless V2", "towerless"):
        assert valid("practice_request", {"program": named, "seats": [0, 5]}), named
        assert valid("program_versions", {"program": named}), named
    assert not valid("practice_request", {"program": "towerless v2", "seats": [0, 0]})
    assert valid("program_release", {"program": "towerless v2", "user_said_yes": True})
    assert not valid("program_release", {"program": "towerless", "user_said_yes": True})
    assert not valid("program_release", {"program": "towerless v2", "user_said_yes": False})
    assert not valid(
        "program_release", {"program": "towerless v2", "user_said_yes": True, "league_id": "x"}
    )
    assert valid("practice_status", {"request_id": "xreq_abc", "wait_seconds": 480})
    assert not valid("practice_status", {"wait_seconds": 481})
    assert valid("program_change", {"text": "x = 1\n", "change": "set x"})
    assert not valid("program_change", {"text": "x = 1\n"})
    assert valid("program_edit", {"edits": [{"find": "a", "replace": ""}], "change": "x"})
    assert not valid("program_edit", {"edits": [{"find": "", "replace": "b"}], "change": "x"})
    assert not valid("program_edit", {"edits": [], "change": "x"})
    assert valid(
        "task_update",
        {"title": "Run under towers less", "step": "understand", "status": "done", "detail": "ok"},
    )
    assert not valid("task_update", {"step": "deploy", "status": "done", "detail": "ok"})


def test_manifest_advertises_registered_tools():
    manifest = yaml.safe_load((DISTRIBUTION / "plugins/play/plugin.yaml").read_text())
    assert manifest["provides_tools"] == list(plugin.TOOLS)
    config = yaml.safe_load((DISTRIBUTION / "config.yaml").read_text())
    assert config["plugins"]["enabled"] == ["play"]
    # No raw file, terminal or Kanban tools: the lead changes the program through its own tools.
    assert config["platform_toolsets"]["api_server"] == ["play", "memory"]
    assert config["memory"]["memory_enabled"] is True
    assert config["memory"]["user_profile_enabled"] is True
    builder = yaml.safe_load((DISTRIBUTION / "builder/config.yaml").read_text())
    assert builder["memory"] == {"memory_enabled": False, "user_profile_enabled": False}


def test_handler_runs_the_operation_in_the_profile_venv(env, tmp_path):
    """The real subprocess boundary: a bad argument comes back as the operation's envelope."""
    home = Path(env["HERMES_HOME"])
    venv = home / "local/venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin/python"  # this test venv's interpreter, packages included
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    (home / "uv.lock").write_text("contract fixture\n")
    (venv / ".lock-sha").write_text(lock_sha(home))
    context = Context(home, {**env, "PYTHONPATH": str(DISTRIBUTION / "src")})
    envelope = plugin.call(context, "program_change", {"text": "WHILE 1\n", "change": "loop"})
    assert envelope["ok"] is False
    assert envelope["error"]["reason"] == "program_invalid"
    assert envelope["error"]["details"]["problems"][0]["line"] == 1
    assert json.loads(json.dumps(envelope)) == envelope


def test_fresh_start_clears_only_the_two_note_files(tmp_path):
    memories = tmp_path / "memories"
    memories.mkdir()
    (memories / "MEMORY.md").write_text("I like short answers.\n")
    (memories / "USER.md").write_text("Their name is Sam.\n")
    keep = [tmp_path / "SOUL.md", tmp_path / "config.yaml", memories / "other.md"]
    for path in keep:
        path.write_text("keep")
    assert fresh.clear_notes(tmp_path)["cleared"] == ["MEMORY.md", "USER.md"]
    assert (memories / "MEMORY.md").read_text() == (memories / "USER.md").read_text() == ""
    assert all(path.read_text() == "keep" for path in keep)
    assert fresh.clear_notes(tmp_path)["cleared"] == []
    assert fresh.clear_notes(tmp_path / "nowhere")["cleared"] == []


def test_fresh_start_schema_demands_the_confirmation():
    validator = jsonschema.Draft202012Validator(plugin.schema("fresh_start")["parameters"])
    assert validator.is_valid({"confirm": True})
    assert not validator.is_valid({}) and not validator.is_valid({"confirm": False})


def test_fresh_start_handler_refuses_without_confirmation(tmp_path, monkeypatch):
    memories = tmp_path / "memories"
    memories.mkdir()
    (memories / "USER.md").write_text("Their name is Sam.\n")
    monkeypatch.setattr(plugin, "profile_context", lambda: Context(tmp_path, {}))
    refused = json.loads(plugin.handler("fresh_start")({"confirm": False}))
    assert refused["ok"] is False and (memories / "USER.md").read_text()
    done = json.loads(plugin.handler("fresh_start")({"confirm": True}))
    assert done["ok"] is True and done["data"]["cleared"] == ["USER.md"]
