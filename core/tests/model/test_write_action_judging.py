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


def test_taking_the_task_first_is_not_judged_as_the_write(script):
    case = Case("researcher", "task", "ask", "Ask about T-5.")
    fake = script(
        reply(("task", {"action": "claim", "task": "T-5"})),
        reply(("task", {"action": "ask", "task": "T-5", "question": "Which division?"})),
    )
    assert len(run(case).detours) == 1
    assert len(fake.requests) == 2


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


def test_a_deeper_recovery_history_repeats_the_call_with_the_short_refusal(script):
    fake = script(reply(("claim", GOOD_CLAIM)))
    case = next(c for c in write_actions.RECOVERY if c.refusals == 3 and c.tool == "claim")
    assert case.label.endswith("(after 3 refused)")
    assert run(case).outcome == "valid"
    history = fake.requests[0][2:]
    assert [m["role"] for m in history] == ["assistant", "tool"] * 3
    results = [json.loads(m["content"]) for m in history if m["role"] == "tool"]
    assert "usage" in results[0]
    assert all(
        "This exact call was refused" in r["error"] and r["ok"] is False for r in results[1:]
    )
    assert "refused 3 times" in results[2]["error"]


def test_every_recovery_case_starts_from_a_call_the_tool_refuses():
    for case in write_actions.RECOVERY:
        history = write_actions.refused_history(case, harness.ShippedSchemas())
        assert json.loads(history[1]["content"])["ok"] is False, case.id


def test_a_captured_context_replaces_the_stand_ins(script, monkeypatch, tmp_path):
    capture = tmp_path / "context.json"
    hermes_tool = {"type": "function", "function": {"name": "hermes_only", "parameters": {}}}
    capture.write_text(
        json.dumps(
            {
                "orchestrator": {"system": "The real prompt.", "hermes_tools": [hermes_tool]},
                "researcher": {"system": "The other prompt.", "hermes_tools": []},
            }
        )
    )
    monkeypatch.setenv("IDE_TOOL_CALL_CONTEXT", str(capture))
    fake = script(reply(("claim", GOOD_CLAIM)))
    assert run().outcome == "valid"
    assert fake.requests[0][0] == {"role": "system", "content": "The real prompt."}


# --- what the summary reports --------------------------------------------------------------------


def test_reasoning_naming_an_omitted_field_is_reported(script):
    good = reply(("claim", {**GOOD_CLAIM}))
    good.reasoning = "I must add the summary, the tags and see_also, and a body."
    script(good)
    assert run().reasoning_gaps == ["see_also"]
    without_tags = {k: v for k, v in GOOD_CLAIM.items() if k != "tags"}
    bad = reply(("claim", without_tags))
    bad.reasoning = "The write needs tags."
    script(bad)
    result = run()
    assert result.outcome == "invalid" and result.reasoning_gaps == ["tags (required)"]


def test_a_field_name_inside_another_word_is_not_a_gap():
    assert harness.reasoning_gaps(["tag"], [], {}, "I will add tags later") == []


def test_reasoning_field_is_read_from_either_key():
    assert harness.reasoning_of({"reasoning": "a"}) == "a"
    assert harness.reasoning_of({"reasoning_content": "b"}) == "b"
    assert harness.reasoning_of({}) == ""


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [(5, 10, 0.24, 0.76), (10, 10, 0.72, 1.0), (0, 12, 0.0, 0.24), (45, 50, 0.79, 0.96)],
)
def test_wilson_interval_matches_known_values(successes, total, low, high):
    found = harness.wilson_interval(successes, total)
    assert found == pytest.approx((low, high), abs=0.01)


@pytest.mark.parametrize(
    ("successes", "total", "below"),
    [(17, 20, True), (18, 20, False), (49, 55, True), (50, 55, False), (55, 55, False)],
)
def test_only_a_rate_clearly_under_the_floor_counts_as_below(successes, total, below):
    assert harness.clearly_below(successes, total, 0.95) is below


def test_the_summary_shows_sample_size_and_interval(script, monkeypatch):
    monkeypatch.setattr(harness, "RESULTS", harness.Results(schemas="shipped"))
    for valid in (True, True, False):
        script(reply(("claim", GOOD_CLAIM if valid else {})))
        harness.RESULTS.items.append(run())
    table = "\n".join(harness.summary())
    rows = {line.split("  ")[0]: line for line in table.splitlines()}
    assert "2/3 67% [21-94]" in rows["first-try valid"]
    assert "2/3" in rows[CLAIM_WRITE.label]


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
    assert "0/0" not in rows["after 1 refusal"] and "-" in rows["after 1 refusal"]


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
