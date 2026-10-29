"""The one rule set behind the advisory hook (design C.4), shared by both profiles.

A rule recognises a shell command, a code snippet or a file path that bypasses a profile tool
and names the tool to use instead. The hook never blocks: the command runs and the note is
appended to its result. Stdlib only: this runs inside Hermes on every tool result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    tool: str
    note: str
    capability: str | None = None  # the capability whose repair Task the note points at


def _rule(name: str, pattern: str, tool: str, note: str, capability: str | None = None) -> Rule:
    return Rule(name, re.compile(pattern, re.IGNORECASE), tool, note, capability)


RULES: tuple[Rule, ...] = (
    _rule(
        "raw-submit",
        r"\bcoworld\s+submit\b",
        "submit",
        "A raw `coworld submit` leaves no Version or submission record, so the briefing, the "
        "champion sync and the user's clients will not know about it. Use the submit tool "
        "(it records the intent, the result and a notice).",
        "upload",
    ),
    _rule(
        "raw-upload",
        r"\bcoworld\s+upload-policy\b|\bsoftmax\s+upload\b",
        "upload",
        "A raw upload leaves no Version record and cannot be submitted by the submit tool. Use "
        "version.create and then the upload tool; it sends the exact built bytes and checks the "
        "hash.",
        "upload",
    ),
    _rule(
        "raw-xp",
        r"\bcoworld\s+xp-request\s+create\b",
        "xp",
        "A raw XP request is not recorded on any Experiment, so its results are lost to the "
        "decision rule and the watcher cannot wait for it. Use xp.request.",
        "evaluate",
    ),
    _rule(
        "identity-change",
        r"\bsoftmax\s+(login|logout|set-token|get-token)\b|\bcoworld\s+player\s+(use|unset)\b",
        "softmax_api",
        "Identity is fixed for this workspace: the Player credential and, for the Orchestrator, "
        "the User identity are provided. Use the platform or softmax_api tools, which pick the "
        "identity for you, instead of logging in or switching players.",
    ),
    _rule(
        "force-push",
        r"\bgit\b[^\n|;&]*\bpush\b[^\n|;&]*(\s--force(-with-lease)?\b|\s-f\b|\s\+\S)",
        "task",
        "Force-pushing a shared branch loses other Sessions' work. Keep every commit on your "
        "Task branch and finish with task.complete; the watcher merges reviewed work.",
    ),
    _rule(
        "store-access",
        r"(^|[\s/'\"=])\.state/|\bstore\.db\b|\bsqlite3\b",
        "goal/task/claim/experiment/version",
        "Records are changed through the record tools (goal, task, claim, experiment, version, "
        "directive, settings). Direct edits of the store are detected and reported, and the next "
        "tick restores the schema.",
    ),
)

# The arguments of Hermes tools that carry a command, code or a path.
TEXT_ARGS = ("command", "cmd", "code", "script", "path", "file_path", "filepath", "paths")


def texts_in(args: Any) -> list[str]:
    """Every string in a tool call's arguments that a rule may match."""
    texts: list[str] = []
    if isinstance(args, dict):
        for key in TEXT_ARGS:
            value = args.get(key)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(str(item) for item in value)
    elif isinstance(args, str):
        texts.append(args)
    return texts


def matches(tool_name: str, args: Any) -> list[Rule]:
    """The rules a tool call trips, each at most once, in rule order."""
    texts = texts_in(args)
    if not texts:
        return []
    joined = "\n".join(texts)
    return [rule for rule in RULES if rule.pattern.search(joined)]


def notes_for(tool_name: str, args: Any, broken: dict[str, str] | None = None) -> list[str]:
    """The notes to append to a tool result. ``broken`` maps capability → maintenance Task id."""
    notes = []
    for rule in matches(tool_name, args):
        note = f"[note] {rule.note}"
        if broken and rule.capability and rule.capability in broken:
            task = broken[rule.capability]
            note += (
                f" The {rule.tool} tool is currently broken (capability {rule.capability!r}); "
                f"its repair is {task}."
                if task
                else f" The {rule.tool} tool is currently broken (capability {rule.capability!r})."
            )
        notes.append(note)
    return notes
