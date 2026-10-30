"""Tool-call format against the models in use (design C.4, amendment A7.2).

Skipped without ``OPENROUTER_API_KEY``. For each tool and each configured model, ask for exactly
one call from a one-line instruction and check that the returned arguments validate.
"""

import json
import os
import urllib.request

import pytest

from research_core.tools import catalog
from research_core.tools.base import validate_args

MODELS = os.environ.get("IDE_TOOL_CALL_MODELS", "moonshotai/kimi-k3,z-ai/glm-5.3").split(",")
KEY = os.environ.get("OPENROUTER_API_KEY")
INSTRUCTIONS = {
    "goal": "Create a Goal titled 'Beat the house' with end state 'rank first' because I asked.",
    "task": "Read Task T-3.",
    "claim": "Search claims about aggression.",
    "experiment": "Read experiment X-2.",
    "version": "List the last 3 Versions.",
    "directive": "List the active directives.",
    "settings": "Read the setting max_researchers.",
    "ui": "Notify the user that V-1 was submitted.",
    "workspace": "Show the environment guide.",
    "upload": "Upload Version V-2.",
    "submit": "Submit V-2 to league_x because the user asked in message m-1.",
    "xp": "Report the status of experiment X-2.",
    "platform": "List the leagues.",
    "forum_post": "Post 'Hello' with body 'First post' to the forum.",
    "wiki_edit": "Edit wiki w page p: title T, body B, base revision r1.",
    "softmax_api": "GET /whoami.",
    "github": "List my GitHub repositories.",
    "attachment_download": "Download the attachment with sha256 " + "a" * 64 + ".",
}

pytestmark = pytest.mark.skipif(not KEY, reason="OPENROUTER_API_KEY is not set")


def ask(model: str, schema: dict, instruction: str) -> dict:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": instruction}],
            "tools": [{"type": "function", "function": schema}],
            "tool_choice": {"type": "function", "function": {"name": schema["name"]}},
            "temperature": 0,
        }
    ).encode()
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    calls = payload["choices"][0]["message"].get("tool_calls") or []
    assert calls, payload
    return json.loads(calls[0]["function"]["arguments"] or "{}")


@pytest.mark.parametrize("model", MODELS)
def test_models_emit_valid_tool_calls(model):
    passes, failures = 0, []
    for spec in catalog.tools_for("orchestrator"):
        args = ask(model, spec.schema("orchestrator"), INSTRUCTIONS[spec.name])
        _, problems = validate_args(spec, "orchestrator", args)
        if problems:
            failures.append((spec.name, args, problems))
        else:
            passes += 1
    print(f"{model}: {passes} valid, {len(failures)} invalid")
    assert not failures, failures
