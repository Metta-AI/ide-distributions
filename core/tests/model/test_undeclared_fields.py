"""A small closed-schema test: does a model send a field the schema does not declare?

Kimi models on Moonshot's endpoint do not, so a tool list frozen before a required field was added
cannot be called correctly, however clear the refusal (``tools/schema_drift.py`` finds such a
list). Measured on 2026-11-04: undeclared 0/12, declared 12/12. Much of what this repository does
about stale schemas rests on that, so a provider or model change that moves it should be noticed.

Runs only with ``-m model`` and ``OPENROUTER_API_KEY``, on ``IDE_TOOL_CALL_MODELS`` (default Kimi
K3.1 Pro), ``IDE_TOOL_CALL_REPEATS`` calls per variant.
"""

from __future__ import annotations

import os

import pytest

from tests.model import harness

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set"),
]
DEFAULT_MODEL = "moonshotai/kimi-k3.1-pro"
DEFAULT_REPEATS = 6
INSTRUCTION = (
    "Call record_note with note set to 'pv-42 is the champion' and ticket set to 'T-77'. "
    "Both values must be in the call."
)
NOTE = {"type": "string", "description": "The note."}
TICKET = {"type": "string", "description": "The ticket the note belongs to."}


def record_note(**extra_properties: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "record_note",
            "description": "Record a note.",
            "parameters": {
                "type": "object",
                "properties": {"note": NOTE, **extra_properties},
                "required": ["note"],
                "additionalProperties": False,
            },
        },
    }


def expected(model: str, case: str, tool: dict, expect_ticket: bool) -> harness.Result:
    """Valid when the reply makes a call whose arguments hold ``ticket`` exactly as expected."""
    try:
        reply = harness.complete(
            model,
            [{"role": "user", "content": INSTRUCTION}],
            [tool],
            tool_choice="required",
            provider=harness.provider_for(model, harness.SHIPPED),
        )
    except harness.HostError as error:
        outcome = "rejected" if error.rejected else "host_error"
        return harness.Result(model, harness.SHIPPED, case, outcome, str(error))
    if not reply.calls:
        detail = f"text: {reply.text[:150]!r}"
        return harness.Result(model, harness.SHIPPED, case, "no_call", detail, replies=[reply])
    call = reply.calls[0]
    if call.arguments is None:
        detail = f"arguments are not a JSON object: {call.raw[:150]}"
        return harness.Result(model, harness.SHIPPED, case, "invalid", detail, replies=[reply])
    sent = "ticket" in call.arguments
    outcome = "valid" if sent == expect_ticket else "invalid"
    detail = f"ticket {'sent' if sent else 'left out'}: {call.raw}"[:200]
    return harness.Result(model, harness.SHIPPED, case, outcome, detail, replies=[reply])


CALLS = [
    (model, case, tool, expect_ticket)
    for model in harness.env_list("IDE_TOOL_CALL_MODELS") or [DEFAULT_MODEL]
    for case, tool, expect_ticket in (
        ("closed schema, ticket undeclared", record_note(), False),
        ("closed schema, ticket declared", record_note(ticket=TICKET), True),
    )
    for _ in range(int(os.environ.get("IDE_TOOL_CALL_REPEATS") or DEFAULT_REPEATS))
]


@pytest.mark.parametrize(
    ("model", "case", "tool", "expect_ticket"),
    CALLS,
    ids=[f"{call[0].rsplit('/', 1)[-1]}-{call[1]}" for call in CALLS],
)
def test_a_closed_schema_decides_whether_the_field_is_sent(model, case, tool, expect_ticket):
    result = expected(model, case, tool, expect_ticket)
    harness.RESULTS.schemas = "one small closed schema (ok = the field is sent only if declared)"
    harness.RESULTS.items.append(result)
    if result.outcome == "host_error":
        pytest.skip(result.detail)
    assert result.outcome == "valid", result.detail
