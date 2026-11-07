"""The middleware that drops ``<mandatory_tool_use>`` from what Hermes sends."""

from __future__ import annotations

from research_core.plugin.hermes_prompt import without_mandatory_tool_use

BLOCK = "<mandatory_tool_use>\nNEVER answer from memory\n</mandatory_tool_use>\n\n"
HEAD = "<tool_persistence>\nkeep going\n</tool_persistence>\n\n"
TAIL = "<act_dont_ask>\nact\n</act_dont_ask>\n"
PROMPT = HEAD + BLOCK + TAIL
KEPT = HEAD + TAIL


def request_with(system):
    return {
        "model": "moonshotai/kimi-k3.1-pro",
        "messages": [system, {"role": "user", "content": f"Why does it say {BLOCK}"}],
        "max_tokens": 100,
    }


def test_drops_only_the_block_from_a_string_system_message():
    request = request_with({"role": "system", "content": PROMPT})
    sent = without_mandatory_tool_use(request=request, provider="openrouter")["request"]
    assert sent["messages"][0] == {"role": "system", "content": KEPT}
    assert sent["messages"][1] == request["messages"][1]  # the user may quote it
    assert sent["max_tokens"] == 100
    assert request["messages"][0]["content"] == PROMPT  # the stored history is not touched


def test_keeps_a_cache_marker_on_a_system_message_in_parts():
    marker = {"type": "ephemeral"}
    system = {
        "role": "system",
        "content": [{"type": "text", "text": PROMPT, "cache_control": marker}],
    }
    sent = without_mandatory_tool_use(request=request_with(system))["request"]
    assert sent["messages"][0]["content"] == [
        {"type": "text", "text": KEPT, "cache_control": marker}
    ]


def test_leaves_a_request_without_the_block_alone():
    assert (
        without_mandatory_tool_use(request=request_with({"role": "system", "content": KEPT}))
        is None
    )
    assert without_mandatory_tool_use(request={"input": []}) is None
    quoted_only = {"messages": [{"role": "user", "content": BLOCK}]}
    assert without_mandatory_tool_use(request=quoted_only) is None


def test_a_malformed_request_never_raises():
    assert without_mandatory_tool_use(request={"messages": ["not a message"]}) is None
