import json
import sys
from pathlib import Path

import jsonschema
import yaml

from play import plugin
from play.context import Context
from play.launch import lock_sha

DISTRIBUTION = Path(__file__).resolve().parents[1]
PLAY_TOOLS = {
    "starter_fetch": ([], {"player"}),
    "program_upload": (["path", "name"], {"path", "name"}),
    "practice_request": (["policy_version"], {"policy_version", "episodes", "seats", "attempt"}),
    "practice_status": (["request_id"], {"request_id"}),
    "program_release": (["policy_version"], {"policy_version"}),
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
    assert valid("program_upload", {"path": "program.bas", "name": "my-first"})
    assert not valid("program_upload", {"path": "program.bas", "name": "My First"})
    assert valid("practice_request", {"policy_version": "p-ply_1:v3", "seats": [0, 5]})
    assert not valid("practice_request", {"policy_version": "p-ply_1:v3", "seats": [0, 0]})
    assert not valid("practice_request", {"policy_version": "p-ply_1"})
    assert valid("practice_status", {"request_id": "xreq_abc"})
    assert not valid("program_release", {"policy_version": "p:v1", "league_id": "other"})


def test_manifest_advertises_registered_tools():
    manifest = yaml.safe_load((DISTRIBUTION / "plugins/play/plugin.yaml").read_text())
    assert manifest["provides_tools"] == list(plugin.TOOLS)
    config = yaml.safe_load((DISTRIBUTION / "config.yaml").read_text())
    assert config["plugins"]["enabled"] == ["play"]
    assert config["platform_toolsets"]["api_server"] == ["play", "kanban"]


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
    outside = tmp_path / "x.bas"
    outside.write_text("10 END\n")
    envelope = plugin.call(context, "program_upload", {"path": str(outside), "name": "a"})
    assert envelope["ok"] is False
    assert envelope["error"]["reason"] == "bad_arguments"
    assert "outside the program workspace" in envelope["error"]["message"]
    assert json.loads(json.dumps(envelope)) == envelope
