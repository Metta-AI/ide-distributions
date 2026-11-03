"""How a write-action case is run and judged, with a scripted model: no network, no key.

``write_actions.run_case`` and ``harness.summary`` decide what the paid measurement reports, so
their rules are pinned here: which call is judged, what production would refuse, how calls
before it are answered, and how host failures count.
"""

from __future__ import annotations

import json

import pytest

from tests.model import harness, write_actions
from tests.model.write_actions import Case, run_case

CLAIM_WRITE = Case("orchestrator", "claim", "write", "Record a claim.")
GOOD_CLAIM = {
    "action": "write",
    "summary": "pv-42 is the new champion of league L-7.",
    "body": "It took over from pv-39.",
    "tags": ["champion"],
    "evidence": [{"type": "doc", "ref": write_actions.CHAMPION_DOC, "stance": "supports"}],
}


def reply(*calls: tuple[str, dict | str], text: str = "") -> harness.Reply:
    """A model reply making ``calls`` (name and arguments; a string is sent as raw text)."""
    made = []
    for index, (name, args) in enumerate(calls):
        raw = args if isinstance(args, str) else json.dumps(args)
        made.append(
            harness._call({"id": f"c{index}", "function": {"name": name, "arguments": raw}})
        )
    message = {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw}}
            for c in made
        ],
    }
    return harness.Reply(message, made, text, "Host", 1000, 500, 10, 0.001, 0.1)


class Script:
    """A fake ``harness.complete``: returns the given replies in order, keeps each request."""

    def __init__(self, *replies: harness.Reply | Exception):
        self.replies = list(replies)
        self.requests: list[list[dict]] = []

    def __call__(self, model, messages, tools, *, tool_choice, provider) -> harness.Reply:
        self.requests.append(messages)
        next_reply = self.replies.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply


@pytest.fixture
def script(monkeypatch):
    def install(*replies):
        fake = Script(*replies)
        monkeypatch.setattr(harness, "complete", fake)
        monkeypatch.setattr(harness, "full_toolset", lambda role, schemas, model: [])
        return fake

    return install


def run(case: Case = CLAIM_WRITE) -> harness.Result:
    return run_case("m/model", "host", case, harness.ShippedSchemas())


# --- which call is judged (finding A) ---------------------------------------------------------


def test_a_call_with_every_argument_dropped_is_judged_not_answered(script):
    fake = script(reply(("claim", {})))
    result = run()
    assert result.outcome == "invalid"
    assert "action is required" in result.detail
    assert len(fake.requests) == 1


def test_unparsable_arguments_to_the_tool_are_judged(script):
    script(reply(("claim", "{not json")))
    result = run()
    assert result.outcome == "invalid"
    assert "not a JSON object" in result.detail


def test_another_write_action_of_the_tool_is_a_bad_first_call(script):
    script(reply(("claim", {"action": "retire", "claim": "C-2", "status": "refuted"})))
    result = run()
    assert result.outcome == "invalid"
    assert "'retire' instead of write" in result.detail


def test_a_read_of_the_tool_comes_first_and_the_write_is_judged(script):
    fake = script(
        reply(("claim", {"action": "search", "query": "champion"})), reply(("claim", GOOD_CLAIM))
    )
    result = run()
    assert (result.outcome, len(result.detours)) == ("valid", 1)
    answered = fake.requests[1][-1]
    assert answered["role"] == "tool" and answered["content"] == write_actions.RESULT


def test_a_call_production_refuses_is_answered_with_the_tools_refusal(script):
    fake = script(reply(("task", {"action": "read"})), reply(("claim", GOOD_CLAIM)))
    assert run().outcome == "valid"
    refusal = json.loads(fake.requests[1][-1]["content"])
    assert refusal["ok"] is False
    assert "task is required for read" in refusal["error"]


def test_hermes_tools_get_the_plausible_result(script):
    fake = script(reply(("todo_list", {"todos": []})), reply(("claim", GOOD_CLAIM)))
    assert run().outcome == "valid"
    assert fake.requests[1][-1]["content"] == write_actions.RESULT


def test_text_after_other_calls_is_told_apart_from_a_plain_text_answer(script):
    script(reply(text="Done."))
    assert run().outcome == "no_call"
    script(reply(("todo_list", {"todos": []})), reply(text="I could not find it."))
    assert run().outcome == "stopped"


def test_never_reaching_the_tool_within_the_limit_is_a_wrong_tool(script):
    script(*(reply(("todo_list", {"todos": []})) for _ in range(write_actions.MAX_REQUESTS)))
    result = run()
    assert result.outcome == "wrong_tool"
    assert len(result.detours) == write_actions.MAX_REQUESTS


def test_a_recovery_history_holds_the_refused_call_and_the_tools_refusal(script):
    fake = script(reply(("claim", GOOD_CLAIM)))
    case = write_actions.RECOVERY[0]
    assert run(case).outcome == "valid"
    assistant, tool_result = fake.requests[0][-2:]
    sent = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
    assert sent == write_actions.INCIDENT_CLAIM_ARGS
    assert "summary is required for write" in json.loads(tool_result["content"])["error"]


# --- what production refuses (finding B) ------------------------------------------------------


def judged(args: dict, case: Case = CLAIM_WRITE) -> tuple[str, str]:
    call = harness.Call("c0", case.tool, args, json.dumps(args))
    return write_actions.judge(case, call, args)


def test_a_complete_claim_write_is_valid():
    assert judged(GOOD_CLAIM) == ("valid", "")


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"evidence": []}, "at least one evidence link"),
        ({"evidence": [{"type": "doc", "ref": write_actions.CHAMPION_DOC}]}, "missing stance"),
        (
            {"evidence": [{"type": "doc", "ref": write_actions.CHAMPION_DOC, "stance": "maybe"}]},
            "stance 'maybe'",
        ),
        (
            {"evidence": [{"type": "doc", "ref": "L-7 page", "stance": "supports"}]},
            "does not match",
        ),
        ({"summary": " ".join(["word"] * 41)}, "41 words"),
    ],
)
def test_claim_writes_the_store_refuses_are_invalid(change, problem):
    outcome, detail = judged({**GOOD_CLAIM, **change})
    assert outcome == "invalid"
    assert detail.startswith("record check:") and problem in detail


def test_experiment_methods_of_the_wrong_shape_are_invalid():
    case = Case("orchestrator", "experiment", "create", "Create an experiment.")
    args = {
        "action": "create",
        "summary": "Does faster scouting win more?",
        "hypothesis": {
            "statement": "Earlier scouting raises the win rate.",
            "if_true": "adopt V-12",
            "if_false": "keep V-11",
            "motivation": "T-4's replays",
        },
        "methods": {"baseline": "V-11", "candidate": "V-12", "metrics": "win_rate"},
    }
    outcome, detail = judged(args, case)
    assert outcome == "invalid" and "methods.metrics must be a non-empty list" in detail
    args["methods"] = {
        "baseline": "V-11",
        "candidate": "V-12",
        "procedure": "40 xp episodes each",
        "metrics": [{"name": "win_rate", "direction": "up", "role": "primary"}],
        "decision_rule": {"text": "a 3-point gain", "n_min": 40},
    }
    assert judged(args, case) == ("valid", "")


# --- host failures (finding F) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "rejected"), [(400, True), (422, True), (404, True), (429, False), (402, False)]
)
def test_only_request_refusals_count_as_rejections(status, rejected):
    assert harness._is_rejection(status) is rejected
    assert harness._is_rejection(503) is False


def test_a_rejected_request_counts_and_a_host_error_does_not(script, monkeypatch):
    monkeypatch.setattr(harness, "RESULTS", harness.Results(schemas="shipped"))
    script(harness.HostError("HTTP 400: bad schema", rejected=True))
    harness.RESULTS.items.append(run())
    script(harness.HostError("HTTP 503: overloaded"))
    harness.RESULTS.items.append(run(write_actions.RECOVERY[0]))
    script(reply(("claim", GOOD_CLAIM)))
    harness.RESULTS.items.append(run(write_actions.RESEARCHER[0]))
    assert [r.outcome for r in harness.RESULTS.items] == ["rejected", "host_error", "valid"]
    table = "\n".join(harness.summary())
    assert "REJ" in table and "ERR" in table
    rows = {line.split("  ")[0]: line for line in table.splitlines()}
    assert "1/2 50%" in rows["valid, all cases"]  # the rejection counts, the 503 does not
    assert "0/0" not in rows["recovery valid"] and "-" in rows["recovery valid"]


# --- the request (finding C) ------------------------------------------------------------------


def test_the_request_sends_no_temperature_and_hermes_default_reasoning(monkeypatch):
    monkeypatch.setattr(harness, "reasoning_models", lambda: frozenset({"a/thinker"}))
    body = harness.request_body("a/thinker", [], [], tool_choice="auto", provider=None)
    assert "temperature" not in body
    assert body["reasoning"] == {"enabled": True, "effort": "medium"}
    plain = harness.request_body("a/plain", [], [], tool_choice="auto", provider={"only": ["x"]})
    assert "reasoning" not in plain and plain["provider"] == {"only": ["x"]}


def test_full_toolset_mode_needs_the_hermes_source(monkeypatch):
    monkeypatch.delenv("IDE_HERMES_SRC", raising=False)
    harness.hermes_sanitizers.cache_clear()
    with pytest.raises(RuntimeError, match="IDE_HERMES_SRC"):
        harness.full_toolset("orchestrator", harness.ShippedSchemas(), "m/model")
    harness.hermes_sanitizers.cache_clear()
