"""Write actions with required fields, on several models and OpenRouter hosts, in full-toolset mode.

Costs real money, so it runs only on request: ``-m model`` and ``OPENROUTER_API_KEY`` (README,
"Develop and test"). Each case offers the role's whole tool list as a Hermes session does (the
IDE tools plus Hermes's built-ins, ``tool_choice: "auto"``) with a one-line instruction, and
judges the model's first call to the expected tool and action: its arguments must pass
``validate_args``, the check the tools run before anything else. Calls before it, usually a
read of the record, get a stub result (``run_case``); a model that has not reached the expected
action within ``MAX_REQUESTS`` requests fails the case. A recovery case starts from a history
holding one refused call, with the refusal text the tool returns today. The run ends with a
table of results per model and host (``harness.summary``), printed by this folder's
``conftest.py``.

Environment, all optional:
  IDE_TOOL_CALL_MODELS   comma-separated models (default: MEASUREMENT's models)
  IDE_TOOL_CALL_HOSTS    comma-separated host labels for every model (default: MEASUREMENT's,
                         else ``shipped``); see ``harness.provider_for``
  IDE_TOOL_CALL_CASES    comma-separated substrings; only cases whose id contains one run
                         (ids look like ``orchestrator-claim.write``, ``...-recovery``)
  IDE_TOOL_CALL_REPEATS  calls per case (default 1)
  IDE_TOOL_CALL_SCHEMAS  ``module:attribute`` naming another ``harness.SchemaSet``
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pytest

from research_core.tools import catalog
from research_core.tools.base import ToolContext, run_tool, validate_args
from tests.model import harness

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set"),
]

# Models and hosts of the standard measurement. ``shipped`` is the shipped pin (Moonshot's own
# endpoint for the Kimi models) or OpenRouter's default routing for a model without one. Kimi
# K3.1 Pro is served only by Moonshot, so its second host is Moonshot's other endpoint. Wafer
# and Relace served Kimi K3 in the 2026-11-02 incident.
MEASUREMENT: dict[str, list[str]] = {
    "moonshotai/kimi-k3.1-pro": [harness.SHIPPED, "moonshotai/highspeed"],
    "moonshotai/kimi-k3": [harness.SHIPPED, "wafer", "relace"],
    "z-ai/glm-5.3": [harness.SHIPPED, "z-ai"],
    "minimax/minimax-m3": [harness.SHIPPED, "minimax"],
}

SYSTEM = (
    "You are the {role} agent of a Softmax research workspace. Act on requests with your "
    "tools; the user is not watching and will not answer questions."
)
# A claim about a new champion: what the incident's wakes failed to record.
CHAMPION_CLAIM = (
    "Record a claim that the new champion of league L-7 is policy version pv-42 (it took over "
    "from pv-39 at rank 1); tag it champion and L-7, and cite the platform's champion record "
    "for L-7 (type doc, ref platform:league/L-7/champion) as supporting evidence."
)


@dataclass(frozen=True)
class Case:
    role: str
    tool: str
    action: str
    instruction: str
    # Recovery: the arguments of an earlier call that the tool refused. The history holds that
    # call and the tool's refusal, and the model's next call is judged.
    refused: dict | None = None

    @property
    def id(self) -> str:
        suffix = "-recovery" if self.refused is not None else ""
        return f"{self.role}-{self.tool}.{self.action}{suffix}"

    @property
    def label(self) -> str:
        suffix = " (recovery)" if self.refused is not None else ""
        return f"{self.role} {self.tool}.{self.action}{suffix}"


EXPERIMENT_CREATE = (
    "Create an experiment testing whether V-12's faster scouting beats V-11: hypothesis "
    "'earlier scouting raises the win rate', if true adopt V-12, if false keep V-11, motivated "
    "by T-4's replays; methods: baseline V-11, candidate V-12, 40 xp episodes each, primary "
    "metric win_rate (up), decide on a 3-point gain with n_min 40."
)
ORCHESTRATOR = [
    Case("orchestrator", "claim", "write", CHAMPION_CLAIM),
    Case(
        "orchestrator",
        "claim",
        "add_evidence",
        "Add experiment X-3 to claim C-5 as evidence that contradicts it.",
    ),
    Case("orchestrator", "claim", "retire", "Retire claim C-2: it is refuted by X-3."),
    Case(
        "orchestrator",
        "goal",
        "create",
        "Create a Goal 'Reach the top 3 of league L-7': done when our policy ranks in the top "
        "3; the user asked for it in message m-12.",
    ),
    Case(
        "orchestrator",
        "goal",
        "set_status",
        "Park Goal G-2 because league L-4 closed.",
    ),
    Case(
        "orchestrator",
        "task",
        "create",
        "Create a research Task for Goal G-1 titled 'Why do we lose early fights?': study 20 "
        "replays of lost games and report the three most common causes.",
    ),
    Case(
        "orchestrator",
        "task",
        "review",
        "Accept Task T-8's result and merge its commit: the tests pass and the win rate rose.",
    ),
    Case("orchestrator", "experiment", "create", EXPERIMENT_CREATE),
    Case(
        "orchestrator",
        "experiment",
        "add_run",
        "Record xp request req-9f2 as the candidate run of experiment X-4.",
    ),
    Case(
        "orchestrator",
        "experiment",
        "abandon",
        "Abandon experiment X-6: its baseline Version was deleted.",
    ),
    Case(
        "orchestrator",
        "directive",
        "add",
        "Save this standing instruction from the user's message m-31: 'never submit without "
        "asking me first'.",
    ),
    Case(
        "orchestrator",
        "settings",
        "update",
        "Set max_researchers to 2, as the user asked in message m-40.",
    ),
    Case(
        "orchestrator",
        "ui",
        "notify",
        "Tell the user that pv-42 is the new champion of league L-7.",
    ),
    Case(
        "orchestrator",
        "ui",
        "panel_set",
        "Show a markdown panel named 'standings' with the text '1. pv-42  2. pv-39'.",
    ),
]
RESEARCHER = [
    Case("researcher", "claim", "write", CHAMPION_CLAIM),
    Case(
        "researcher",
        "claim",
        "add_evidence",
        "Add replay ep-77 to claim C-5 as evidence that supports it.",
    ),
    Case("researcher", "experiment", "create", EXPERIMENT_CREATE),
    Case(
        "researcher",
        "experiment",
        "add_run",
        "Record xp request req-1c0 as the baseline run of experiment X-4.",
    ),
    Case(
        "researcher",
        "task",
        "propose",
        "Propose an improvement Task for Goal G-1 titled 'Cache path finding': memoize the "
        "path planner and measure the step time.",
    ),
    Case(
        "researcher",
        "task",
        "wait",
        "Mark Task T-5 as waiting for xp request req-1c0 to finish.",
    ),
    Case(
        "researcher",
        "task",
        "release",
        "Release Task T-5: it needs GPU credits this workspace does not have.",
    ),
    Case(
        "researcher",
        "task",
        "propose_submission",
        "Propose Version V-14 of Task T-5 for submission: it won 31 of 40 xp episodes.",
    ),
]
# The incident's refused claim write: fields of other claim actions in place of summary and tags.
INCIDENT_CLAIM_ARGS = {
    "action": "write",
    "body": "pv-42 replaced pv-39 as the champion of league L-7.",
    "evidence": [
        {"type": "doc", "ref": "platform:league/L-7/champion", "stance": "supports"},
    ],
    "see_also": [],
    "stance": "supports",
    "status": "superseded",
    "superseded_by": "pv-42",
}
RECOVERY = [
    Case("orchestrator", "claim", "write", CHAMPION_CLAIM, refused=INCIDENT_CLAIM_ARGS),
    Case("researcher", "claim", "write", CHAMPION_CLAIM, refused=INCIDENT_CLAIM_ARGS),
    Case(
        "orchestrator",
        "task",
        "create",
        ORCHESTRATOR[5].instruction,
        refused={"action": "create", "title": "Why do we lose early fights?", "goal": "G-1"},
    ),
]
CASES = [*ORCHESTRATOR, *RESEARCHER, *RECOVERY]
# Requests per case. Calls before the first one to the expected tool and action (usually a
# read of the record) are answered with STUB.
MAX_REQUESTS = 3
STUB = json.dumps({"ok": True, "note": "Stub result from the tool-call test: nothing to show."})


def refusal(case: Case) -> str:
    """The tool result the refused call got: ``run_tool``'s own text.

    The arguments are refused before the tool touches the store or the workspace, so the
    context needs only the role.
    """
    ctx = ToolContext(role=case.role, actor=None, workspace=None, env={})  # type: ignore[arg-type]
    text = run_tool(case.tool, case.refused, ctx)
    assert json.loads(text)["ok"] is False, text
    return text


def messages(case: Case) -> list[dict]:
    history = [
        {"role": "system", "content": SYSTEM.format(role=case.role)},
        {"role": "user", "content": case.instruction},
    ]
    if case.refused is None:
        return history
    call_id = "call_refused_1"
    return [
        *history,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": case.tool, "arguments": json.dumps(case.refused)},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": refusal(case)},
    ]


def target(case: Case, schemas: harness.SchemaSet, call: harness.Call) -> dict | None:
    """The call's arguments in catalog form if it is the expected tool and action, else None.

    Arguments that are not a JSON object count as an attempt at the expected tool, so they are
    judged rather than skipped.
    """
    if call.arguments is None:
        return {} if schemas.canonical(case.role, call.name, {})[0] == case.tool else None
    name, args = schemas.canonical(case.role, call.name, call.arguments)
    return args if (name, args.get("action")) == (case.tool, case.action) else None


def judge(case: Case, call: harness.Call, args: dict) -> tuple[str, str]:
    """The outcome of the first call to the expected tool and action, and what went wrong."""
    if call.arguments is None:
        return "invalid", f"arguments are not a JSON object: {call.raw[:200]}"
    spec = next(s for s in catalog.tools_for(case.role) if s.name == case.tool)
    _, problems = validate_args(spec, case.role, args)
    if problems:
        return "invalid", f"{'; '.join(problems)}; sent {sorted(args)}"
    return "valid", ""


def run_case(model: str, host: str, case: Case, schemas: harness.SchemaSet) -> harness.Result:
    """Ask until the model calls the expected tool and action, at most MAX_REQUESTS times.

    Models often read a record before they change it, so every other call is answered with
    STUB and recorded as a detour; the first call to the expected tool and action is judged.
    """
    result = harness.Result(model, host, case.label, "wrong_tool")
    history = messages(case)
    tools = harness.full_toolset(case.role, schemas)
    for _ in range(MAX_REQUESTS):
        try:
            reply = harness.complete(
                model,
                history,
                tools,
                tool_choice="auto",
                provider=harness.provider_for(model, host),
            )
        except harness.HostError as error:
            result.outcome, result.detail = "host_error", str(error)
            return result
        result.replies.append(reply)
        if not reply.calls:
            result.outcome, result.detail = "no_call", f"text: {reply.text[:150]!r}"
            return result
        for call in reply.calls:
            args = target(case, schemas, call)
            if args is not None:
                result.outcome, result.detail = judge(case, call, args)
                return result
            result.detours.append(f"{call.name} {call.raw[:120]}")
        history = [
            *history,
            reply.message,
            *({"role": "tool", "tool_call_id": c.id, "content": STUB} for c in reply.calls),
        ]
    result.detail = f"never called {case.tool}.{case.action}; called {result.detours}"
    return result


def selected() -> list[tuple[str, str, Case]]:
    models = harness.env_list("IDE_TOOL_CALL_MODELS") or list(MEASUREMENT)
    hosts = harness.env_list("IDE_TOOL_CALL_HOSTS")
    wanted = harness.env_list("IDE_TOOL_CALL_CASES")
    cases = [c for c in CASES if not wanted or any(w in c.id for w in wanted)]
    repeats = int(os.environ.get("IDE_TOOL_CALL_REPEATS") or 1)
    return [
        (model, host, case)
        for model in models
        for host in hosts or MEASUREMENT.get(model, [harness.SHIPPED])
        for case in cases
        for _ in range(repeats)
    ]


@pytest.mark.parametrize(
    ("model", "host", "case"),
    selected(),
    ids=lambda value: value.id if isinstance(value, Case) else str(value).rsplit("/", 1)[-1],
)
def test_write_action_first_call_is_valid(model: str, host: str, case: Case):
    schemas = harness.schema_set()
    harness.RESULTS.schemas = schemas.label
    result = run_case(model, host, case, schemas)
    harness.RESULTS.items.append(result)
    if result.outcome == "host_error":
        pytest.skip(f"{model} on {host}: {result.detail}")
    assert result.outcome == "valid", f"{result.outcome}: {result.detail}"
