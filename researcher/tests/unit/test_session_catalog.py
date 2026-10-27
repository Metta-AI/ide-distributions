"""The `capabilities` tool's filtering and output (ide_research_core.session_catalog).

The Hermes sources are replaced with fixed lists; the real ones are covered by the `hermes`
integration test in tests/integration/test_install.py.
"""

import json

import pytest

from ide_research_core import session_catalog
from ide_research_core.session_catalog import Entry, capabilities, summary

TOOLS = [
    Entry("terminal", "terminal", "Run a shell command."),
    Entry("read_file", "file", "Read a text file with line numbers."),
    Entry("card_create", "orchestrator", "Create a native Kanban card.", deferred=True),
]
SKILLS = [
    Entry(
        "replay-diagnosis",
        "autoresearch",
        "Use when an investigation needs a cause seen in replays.",
    ),
    Entry("card", "autoresearch", "Use when executing one native research assignment."),
]


def catalog(**kwargs):
    return capabilities(tools=lambda: TOOLS, skills=lambda: SKILLS, **kwargs)


def test_lists_everything_grouped_with_deferred_marked():
    result = catalog()
    assert result["tools"] == {
        "file": ["read_file: Read a text file with line numbers."],
        "orchestrator": [
            "card_create: Create a native Kanban card. [deferred: call through tool_call]"
        ],
        "terminal": ["terminal: Run a shell command."],
    }
    assert result["skills"] == {
        "autoresearch": [
            "card: Use when executing one native research assignment.",
            "replay-diagnosis: Use when an investigation needs a cause seen in replays.",
        ]
    }
    assert (result["tools_count"], result["skills_count"]) == (3, 2)
    assert "skill_view" in result["read_more"] and "tool_call" in result["read_more"]


def test_query_matches_name_group_or_description_case_insensitively():
    assert catalog(query="REPLAY")["skills_count"] == 1
    assert catalog(query="kanban")["tools"] == {
        "orchestrator": [
            "card_create: Create a native Kanban card. [deferred: call through tool_call]"
        ]
    }
    assert catalog(query="file")["tools_count"] == 1  # the group name matches
    assert catalog(query="nothing like this")["tools"] == {}


def test_kind_limits_the_result_and_skips_the_other_source():
    def unavailable():
        raise AssertionError("the other source is not read")

    tools_only = capabilities(kind="tools", tools=lambda: TOOLS, skills=unavailable)
    assert "skills" not in tools_only and tools_only["tools_count"] == 3
    skills_only = capabilities(kind="skills", tools=unavailable, skills=lambda: SKILLS)
    assert "tools" not in skills_only and skills_only["skills_count"] == 2
    with pytest.raises(ValueError):
        catalog(kind="hooks")


def test_summary_keeps_the_first_sentence_and_bounds_length():
    assert summary("Run a command.  Then more detail.") == "Run a command."
    assert summary("one\nline   description") == "one line description"
    long = "word " * 100
    assert len(summary(long)) == session_catalog.SUMMARY_CHARACTERS
    assert summary(long).endswith("…")
    assert summary(None) == ""


def test_handler_reports_failures_as_json(monkeypatch):
    def broken():
        raise RuntimeError("no registry")

    monkeypatch.setattr(
        session_catalog.capabilities,
        "__kwdefaults__",
        {
            "tools": broken,
            "skills": lambda: SKILLS,
        },
    )
    assert json.loads(session_catalog.handle({})) == {
        "ok": False,
        "error": "RuntimeError: no registry",
    }


def test_register_uses_the_given_toolset():
    calls = []

    class Context:
        def register_tool(self, **kwargs):
            calls.append(kwargs)

    session_catalog.register(Context(), "researcher")
    (call,) = calls
    assert call["name"] == "capabilities" and call["toolset"] == "researcher"
    assert call["schema"]["parameters"]["properties"]["kind"]["enum"] == ["tools", "skills"]
    assert call["schema"]["parameters"]["required"] == []
