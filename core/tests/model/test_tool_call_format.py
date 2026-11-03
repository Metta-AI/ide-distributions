"""Tool-call format against the models in use (design C.4, amendment A7.2).

Skipped without ``OPENROUTER_API_KEY``. For each role, each model its shipped ``config.yaml``
names (the primary and the fallbacks) and each of the role's tools, ask for exactly one call from
a one-line instruction and check that the returned arguments validate.
"""

import json
import os
import urllib.request
from pathlib import Path

import pytest
import yaml

from research_core import prepare
from research_core.tools import catalog
from research_core.tools.base import validate_args

REPO = Path(__file__).resolve().parents[3]


def shipped_models(role: str) -> list[str]:
    config = yaml.safe_load((REPO / role / "config.yaml").read_text())
    return [config["model"]["default"], *(f["model"] for f in config["fallback_providers"])]


# IDE_TOOL_CALL_MODELS (comma-separated) runs the Orchestrator's tools on other models instead.
CASES = (
    [("orchestrator", model) for model in os.environ["IDE_TOOL_CALL_MODELS"].split(",")]
    if os.environ.get("IDE_TOOL_CALL_MODELS")
    else [(role, m) for role in ("orchestrator", "researcher") for m in shipped_models(role)]
)
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
}
# A Researcher reads Goals; it cannot create one.
ROLE_INSTRUCTIONS = {"researcher": {"goal": "Read Goal G-1."}}

pytestmark = pytest.mark.skipif(not KEY, reason="OPENROUTER_API_KEY is not set")


def ask(model: str, schema: dict, instruction: str) -> dict:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": instruction}],
            "tools": [{"type": "function", "function": schema}],
            # One tool per request, so "required" forces it; Kimi K3.1 Pro's endpoint does not
            # support naming the function.
            "tool_choice": "required",
            # Hermes sends no temperature to Kimi (Moonshot accepts only its own); 0 elsewhere.
            **({} if model.rsplit("/", 1)[-1].startswith("kimi") else {"temperature": 0}),
            # The OpenRouter hosts the shipped config pins this model to, as production uses.
            **(
                {"provider": prepare.PROVIDER_ROUTING_MODELS[model]}
                if model in prepare.PROVIDER_ROUTING_MODELS
                else {}
            ),
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


@pytest.mark.parametrize(("role", "model"), CASES)
def test_models_emit_valid_tool_calls(role, model):
    instructions = {**INSTRUCTIONS, **ROLE_INSTRUCTIONS.get(role, {})}
    passes, failures = 0, []
    for spec in catalog.tools_for(role):
        args = ask(model, spec.schema(role), instructions[spec.name])
        _, problems = validate_args(spec, role, args)
        if problems:
            failures.append((spec.name, args, problems))
        else:
            passes += 1
    print(f"{role} on {model}: {passes} valid, {len(failures)} invalid")
    assert not failures, failures
