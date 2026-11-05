"""Every ``tool.action {fields}`` signature the agents read matches the tool specs.

The prompts, skills, task types and wake prompts show calls in this short form, inside backticks:

    `tool.action {a, b: value, c?, d | e, f {g, h}, ...}`

- ``a`` is a field; ``b: value`` a field with an example value; ``c?`` an optional field.
- ``d | e`` means one of the two.
- ``f {g, h}`` or ``f: {g, h}`` lists the keys of an object field.
- A closing ``...`` marks a fragment: the signature shows what this step is about and leaves other
  required fields out on purpose. Without it the signature is complete.

The rules: every named field and key must exist on that action, and a complete signature names
every field the action requires. A field the caller generates rather than decides (see
``is_generated``) never has to be named. ``KNOWN_INCOMPLETE`` lists the signatures that break the
second rule today; the test fails when an entry is no longer needed, so the list only shrinks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import research_core
from research_core.tools import catalog
from research_core.tools.base import ToolSpec

CORE = Path(research_core.__file__).parent
SOURCES = ("prompts", "skills", "task_types", "watcher/wakes")

catalog._load_tool_modules()
OPENING = re.compile(r"`(" + "|".join(sorted(catalog.TOOLS)) + r")(?:\.([a-z_]+))? ?\{")
ITEM = re.compile(r"([a-z_]+)\??(?:\s*\|\s*([a-z_]+)\??)?\s*:?\s*(\{.*\})?", re.DOTALL)
FRAGMENT = "..."

# Signatures with no closing ``...`` that leave out a required field: (file, tool.action, fields).
KNOWN_INCOMPLETE = {
    (
        "skills/common/method/analyze-opponents/SKILL.md",
        "xp.request",
        "opponents: [<policy version id>]",
    ),
    (
        "skills/common/method/analyze-opponents/SKILL.md",
        "task.propose",
        "type: improvement, experiment: X-n",
    ),
    (
        "skills/common/method/candidate/SKILL.md",
        "task.wait",
        'kind: process, ref: <pid>, detail: "build of <commit>"',
    ),
    ("skills/common/method/candidate/SKILL.md", "version.create", "resume: <token>"),
    ("skills/common/method/candidate/SKILL.md", "version.create", "resume"),
    ("skills/common/method/experiment/SKILL.md", "task.wait", "kind: xp, ref: [<request ids>]"),
    ("skills/common/method/experiment/SKILL.md", "task.wait", "kind: credits"),
    ("skills/common/method/experiment/SKILL.md", "task.wait", "kind: process, ref: <pid>"),
    ("skills/common/method/experiment/SKILL.md", "experiment.abandon", "reason"),
    (
        "skills/common/method/replay-diagnosis/SKILL.md",
        "claim.retire",
        "status: refuted, type: replay, ref, note",
    ),
    ("skills/common/method/run-eval/SKILL.md", "task.wait", "kind: xp, ref: [<request ids>]"),
    ("skills/common/method/scouting/SKILL.md", "claim.retire", "status: superseded, superseded_by"),
    ("skills/common/ops/repair/SKILL.md", "task.complete", "outcome: failed, failure"),
    ("skills/common/platform/submission/SKILL.md", "task.wait", "kind: submission"),
    ("skills/researcher/proposing/SKILL.md", "task.complete", 'proposed_tasks: ["T-7", "T-8"]'),
    ("skills/researcher/proposing/SKILL.md", "platform.active_policies", "exclude_player: true"),
    ("task_types/ideation.md", "platform.active_policies", "exclude_player: true"),
    ("task_types/ideation.md", "task.complete", 'outcome: "failed", failure'),
    ("task_types/improvement.md", "task.wait", 'kind: "xp", ref: [...]'),
    ("task_types/improvement.md", "task.complete", 'outcome: "failed", failure'),
    ("task_types/maintenance.md", "task.complete", 'outcome: "failed", failure'),
    ("task_types/research.md", "task.complete", 'outcome: "failed", failure'),
    ("watcher/wakes/review.md", "task.review", "accept, merge, reason"),
}


@dataclass(frozen=True)
class Signature:
    source: str
    tool: str
    action: str | None
    body: str  # the text between the braces, with runs of white space made one space

    @property
    def label(self) -> str:
        return f"{self.tool}.{self.action}" if self.action else self.tool


def closing_brace(text: str, start: int) -> int:
    """The index of the brace that closes the one before ``start``; quoted text does not count."""
    depth, quoted = 1, False
    for index in range(start, len(text)):
        character = text[index]
        if character == '"':
            quoted = not quoted
        elif not quoted and character == "{":
            depth += 1
        elif not quoted and character == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"no closing brace after {text[start : start + 60]!r}")


def signatures_in(source: str, text: str) -> list[Signature]:
    found = []
    for match in OPENING.finditer(text):
        body = text[match.end() : closing_brace(text, match.end())]
        found.append(Signature(source, match[1], match[2], " ".join(body.split())))
    return found


def shipped_signatures() -> list[Signature]:
    found = []
    for folder in SOURCES:
        for path in sorted((CORE / folder).rglob("*.md")):
            found.extend(signatures_in(str(path.relative_to(CORE)), path.read_text()))
    return found


def top_level_items(body: str) -> list[str]:
    """``body`` split at the commas outside brackets and quotes."""
    items, current, depth, quoted = [], "", 0, False
    for character in body:
        if character == '"':
            quoted = not quoted
        elif not quoted and character in "{[":
            depth += 1
        elif not quoted and character in "}]":
            depth -= 1
        if character == "," and depth == 0 and not quoted:
            items.append(current.strip())
            current = ""
        else:
            current += character
    items.append(current.strip())
    return [item for item in items if item]


def is_generated(schema: dict) -> bool:
    """A field the caller generates rather than decides: a fresh id that makes a retry safe. The
    tool descriptions explain it, so a signature showing what to decide leaves it out."""
    return "intent ID" in schema.get("description", "")


def _key_problems(label: str, keys_text: str, schema: dict) -> list[str]:
    """Keys listed for an object field, against the keys its schema declares."""
    schema = schema.get("items") or schema
    declared = schema.get("properties")
    if not declared:
        return []
    problems = []
    for item in top_level_items(keys_text):
        match = ITEM.match(item)
        name = match[1] if match else item
        if item != FRAGMENT and name not in declared:
            problems.append(f"{label}: no key {name!r}")
    return problems


def problems_with(signature: Signature) -> tuple[list[str], list[str]]:
    """(fields or keys that do not exist, required fields a complete signature leaves out)."""
    spec: ToolSpec = catalog.TOOLS[signature.tool]
    label = f"{signature.source}: {signature.label} {{{signature.body}}}"
    action = spec.action(signature.action) if signature.action else None
    if spec.actions is not None and action is None:
        return [f"{label}: {signature.tool} has no action {signature.action!r}"], []
    if spec.actions is None and signature.action:
        return [f"{label}: {signature.tool} has no actions"], []
    accepted = spec.accepted(action)
    unknown: list[str] = []
    named: set[str] = set()
    items = top_level_items(signature.body)
    for item in items:
        if item == FRAGMENT:
            continue
        match = ITEM.match(item)
        if match is None:
            unknown.append(f"{label}: cannot read {item!r}")
            continue
        for name in filter(None, (match[1], match[2])):
            named.add(name)
            if name not in accepted:
                unknown.append(f"{label}: no field {name!r}")
            elif match[3]:
                unknown.extend(_key_problems(f"{label}: {name}", match[3][1:-1], accepted[name]))
    if items and items[-1] == FRAGMENT:
        return unknown, []
    required = [*(action.required if action else ()), *spec.required]
    omitted = [f for f in required if f not in named and not is_generated(accepted[f])]
    return unknown, omitted


def test_every_signature_names_only_fields_the_action_has():
    problems = [p for signature in shipped_signatures() for p in problems_with(signature)[0]]
    assert problems == []


def test_complete_signatures_name_every_required_field():
    """A signature followed literally must not be refused; a fragment says it is one."""
    incomplete = {}
    for signature in shipped_signatures():
        omitted = problems_with(signature)[1]
        if omitted:
            key = (signature.source, signature.label, signature.body)
            incomplete[key] = omitted
    new = {key: omitted for key, omitted in incomplete.items() if key not in KNOWN_INCOMPLETE}
    stale = KNOWN_INCOMPLETE - set(incomplete)
    assert not new, f"signatures leaving out required fields (name them or end with ...): {new}"
    assert not stale, f"no longer incomplete, remove from KNOWN_INCOMPLETE: {sorted(stale)}"


# --- the reader itself ------------------------------------------------------------------------


def only(text: str) -> Signature:
    found = signatures_in("test.md", text)
    assert len(found) == 1
    return found[0]


def test_the_scan_finds_signatures_in_every_kind_of_source():
    sources = {signature.source.split("/")[0] for signature in shipped_signatures()}
    assert {"skills", "task_types", "watcher"} <= sources


def test_a_field_the_action_does_not_have_is_found():
    unknown, _ = problems_with(only("`claim.search {query, mood: high}`"))
    assert len(unknown) == 1 and "no field 'mood'" in unknown[0]


def test_a_field_of_another_action_is_found():
    unknown, _ = problems_with(only("`claim.search {query, superseded_by}`"))
    assert "no field 'superseded_by'" in unknown[0]


def test_an_action_that_does_not_exist_is_found():
    unknown, _ = problems_with(only("`claim.rewrite {query}`"))
    assert "no action 'rewrite'" in unknown[0]


def test_commas_in_quoted_examples_do_not_split_fields():
    signature = only('`task.wait {task, kind: credits, detail: "XP: 3 requests, about 2 credits"}`')
    assert problems_with(signature)[0] == []


def test_one_of_two_and_optional_marks_are_understood():
    assert problems_with(only("`claim.search {query, tags?, include_retired: true}`"))[0] == []
    assert problems_with(only("`version.adopt {policy_version | label, summary}`")) == ([], [])


def test_the_keys_of_an_object_field_are_checked():
    good = "`task.abandon {task, failure: {what_failed, next_step}}`"
    assert problems_with(only(good))[0] == []
    bad = "`task.abandon {task, failure: {what_went_wrong}}`"
    assert "no key 'what_went_wrong'" in problems_with(only(bad))[0][0]


@pytest.mark.parametrize("body", ["reason", "reason, ..."])
def test_a_closing_ellipsis_allows_leaving_out_required_fields(body):
    omitted = problems_with(only(f"`experiment.abandon {{{body}}}`"))[1]
    assert (omitted == []) is body.endswith("...")
