"""Every ``tool.action {fields}`` signature the agents read matches the tool specs.

The prompts, skills, task types and wake prompts show calls in this short form, inside backticks:

    `tool.action {a, b: value, c?, d | e, f {g, h}, ...}`

- ``a`` is a field; ``b: value`` a field with an example value; ``c?`` an optional field.
- ``d | e`` means one of the two.
- ``f {g, h}``, ``f: {g, h}`` or ``f: [{g, h}]`` lists the keys of an object field or a list of
  objects, by the same rules.
- A closing ``...`` marks a fragment: the signature shows what this step is about and leaves other
  required fields out on purpose. Without it the signature is complete.

The rules: the tool and action must exist, and for the role that reads the file; every named field
and key must exist on that action; and a complete signature names every field and key the action
requires.

A dotted name in backticks before a brace is always read as a call, so a misspelt tool is found. A
bare name is read only when it is a tool, because skills also write ``methods {a, b}`` for a field.
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
OPENING = re.compile(r"`([A-Za-z_0-9]+)(?:\.([A-Za-z_0-9]+))?\s*\{")
# Names (one field, or alternatives joined by ``|``), then a ``: value`` or a ``{keys}``.
ITEM = re.compile(r"([a-z_]+\??(?:\s*\|\s*[a-z_]+\??)*)\s*(?::\s*(.*)|(\{.*\}))?", re.DOTALL)
NAME = re.compile(r"[a-z_]+")
QUOTED_VALUE = re.compile(r'"[^"]*"(?:\s+or\s+"[^"]*")*')
FRAGMENT = "..."
# The role that reads each folder or file; anything not listed here is read by both, or is shared
# text that may show one role's call.
READER_ROLES = {
    "watcher/wakes/task_start.md": "researcher",
    "watcher/wakes/task_resume.md": "researcher",
    "watcher/wakes/setup.md": "orchestrator",
    "watcher/wakes/review.md": "orchestrator",
    "watcher/wakes/management.md": "orchestrator",
    "watcher/wakes/anti_stall.md": "orchestrator",
    "skills/orchestrator": "orchestrator",
    "skills/researcher": "researcher",
    "prompts/orchestrator": "orchestrator",
    "prompts/researcher": "researcher",
    "task_types": "researcher",
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
        if match[2] is None and match[1] not in catalog.TOOLS:
            continue
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


def listed_objects(value: str) -> list[str]:
    """The text inside each object of a value written ``{...}`` or ``[{...}, {...}]``."""
    value = value.strip()
    elements = top_level_items(value[1:-1]) if value[:1] == "[" and value[-1:] == "]" else [value]
    return [e[1:-1] for e in elements if e[:1] == "{" and e[-1:] == "}"]


def _field_problems(label: str, body: str, properties: dict, required) -> tuple[list, list]:
    """(names ``body`` has that ``properties`` lacks, required names it leaves out), going into the
    keys listed for any field. A closing ``...`` allows leaving names out at its own level only."""
    unknown: list[str] = []
    inner_omitted: list[str] = []
    named: set[str] = set()
    items = top_level_items(body)
    for item in items:
        if item == FRAGMENT:
            continue
        match = ITEM.fullmatch(item)
        if match is None:
            unknown.append(f"{label}: cannot read {item!r}")
            continue
        names = NAME.findall(match[1])
        if (match[2] or "").startswith('"') and not QUOTED_VALUE.fullmatch(match[2].strip()):
            unknown.append(f"{label}: text after the quoted value in {item!r}")
        value = (match[2] or match[3] or "").strip()
        if value.startswith("{") and not value.endswith("}"):
            unknown.append(f"{label}: text after the keys in {item!r}")
        for name in names:
            # ``c?`` and ``d | e`` both let a reader leave the name out, so neither supplies it.
            if len(names) == 1 and "?" not in match[1]:
                named.add(name)
            if name not in properties:
                unknown.append(f"{label}: no field {name!r}")
                continue
            schema = properties[name].get("items") or properties[name]
            shown_objects = listed_objects(value)
            if shown_objects and schema.get("type") not in (None, "object"):
                unknown.append(f"{label}: {name} is a {schema['type']}, not an object")
            if not schema.get("properties"):
                continue
            for keys in shown_objects:
                inner = _field_problems(
                    f"{label}: {name}", keys, schema["properties"], schema.get("required", ())
                )
                unknown += inner[0]
                inner_omitted += [f"{name}.{key}" for key in inner[1]]
    if items and items[-1] == FRAGMENT:
        return unknown, inner_omitted
    omitted = [f for f in required if f not in named]
    return unknown, [*inner_omitted, *omitted]


def problems_with(signature: Signature) -> tuple[list[str], list[str]]:
    """(names that do not exist, required fields and keys a complete signature leaves out)."""
    label = f"{signature.source}: {signature.label} {{{signature.body}}}"
    spec: ToolSpec | None = catalog.TOOLS.get(signature.tool)
    if spec is None:
        return [f"{label}: no tool {signature.tool!r}"], []
    action = spec.action(signature.action) if signature.action else None
    if spec.actions is not None and action is None:
        return [f"{label}: {signature.tool} has no action {signature.action!r}"], []
    if spec.actions is None and signature.action:
        return [f"{label}: {signature.tool} has no actions"], []
    role = next((r for f, r in READER_ROLES.items() if signature.source.startswith(f)), None)
    if role is not None and (
        spec not in catalog.tools_for(role) or (action and action not in spec.actions_for(role))
    ):
        return [f"{label}: the {role} cannot call this"], []
    required = [*(action.required if action else ()), *spec.required]
    return _field_problems(label, signature.body, spec.accepted(action), required)


def test_every_signature_names_only_fields_the_action_has():
    problems = [p for signature in shipped_signatures() for p in problems_with(signature)[0]]
    assert problems == []


def test_complete_signatures_name_every_required_field():
    """A signature followed literally must not be refused; a fragment says it is one."""
    incomplete = {}
    for signature in shipped_signatures():
        omitted = problems_with(signature)[1]
        if omitted:
            incomplete[(signature.source, signature.label, signature.body)] = omitted
    assert not incomplete, f"name the required fields or end with ...: {incomplete}"


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
    signature = only('`task.wait {task, kind: credits, detail: "XP: need 2 credits"}`')
    assert problems_with(signature)[0] == []


def test_one_of_two_and_optional_marks_are_understood():
    assert problems_with(only("`claim.search {query, tags?, include_retired: true}`"))[0] == []
    assert problems_with(only("`version.adopt {policy_version | label, summary}`")) == ([], [])


def test_the_keys_of_an_object_field_are_checked():
    good = "`task.abandon {task, failure: {what_failed, next_step}}`"
    assert problems_with(only(good))[0] == []
    bad = "`task.abandon {task, failure: {what_went_wrong}}`"
    assert "no field 'what_went_wrong'" in problems_with(only(bad))[0][0]


@pytest.mark.parametrize(
    "text",
    [
        "`claim.search {query2}`",  # a name with more after it is not the name
        "`claim.search {query other}`",
        "`claim.search {query | tags | nope}`",  # every alternative counts
    ],
)
def test_a_field_is_read_whole(text):
    assert problems_with(only(text))[0] != []


def test_a_misspelt_tool_is_found():
    assert "no tool 'cliam'" in problems_with(only("`cliam.write {summary}`"))[0][0]


def test_a_bare_name_that_is_not_a_tool_is_not_a_call():
    assert signatures_in("test.md", "`methods {baseline, candidate}`") == []


def test_the_keys_of_a_list_of_objects_are_checked():
    good = "`claim.write {summary, body, tags, evidence: [{type, ref, stance}]}`"
    assert problems_with(only(good)) == ([], [])
    bad = "`claim.write {summary, body, tags, evidence: [{typo, ref, stance}]}`"
    assert "no field 'typo'" in problems_with(only(bad))[0][0]


def test_every_object_of_a_list_is_checked():
    text = "`claim.write {summary, body, tags, evidence: [{type, ref, stance}, {typo}]}`"
    unknown, omitted = problems_with(only(text))
    assert "no field 'typo'" in unknown[0] and "evidence.type" in omitted


@pytest.mark.parametrize(
    "text", ["`claim.write2 {summary}`", "`claim.write  {summary}`", "`claim.Write {summary}`"]
)
def test_unusual_spelling_does_not_escape_the_scan(text):
    assert problems_with(only(text)) != ([], [])


def test_keys_on_a_field_that_is_not_an_object_are_found():
    assert "not an object" in problems_with(only("`claim.search {query {typo}}`"))[0][0]


def test_a_quoted_choice_is_one_value():
    assert problems_with(only('`claim.search {query: "a" or "b"}`')) == ([], [])


def test_text_after_the_keys_of_an_object_is_found():
    text = "`task.abandon {task, failure: {what_failed, next_step} extra}`"
    assert "text after the keys" in problems_with(only(text))[0][0]


def test_a_missing_comma_after_a_quoted_value_is_found():
    assert (
        "text after the quoted" in problems_with(only('`claim.search {query: "a" typo: 3}`'))[0][0]
    )


def test_a_required_key_left_out_is_found():
    omitted = problems_with(only("`task.abandon {task, failure: {what_failed}}`"))[1]
    assert omitted == ["failure.next_step"]
    fragment = "`task.abandon {task, failure: {what_failed, ...}}`"
    assert problems_with(only(fragment)) == ([], [])


@pytest.mark.parametrize(
    "text",
    [
        "`claim.write {summary?, body, tags, evidence}`",
        "`claim.write {summary | tags, body, evidence}`",
    ],
)
def test_a_required_field_shown_as_leavable_counts_as_left_out(text):
    assert "summary" in problems_with(only(text))[1]


def test_an_outer_ellipsis_does_not_excuse_a_complete_inner_object():
    signature = "`task.abandon {task, failure: {what_failed}, ...}`"
    assert problems_with(only(signature))[1] == ["failure.next_step"]


def test_an_action_the_role_cannot_call_is_found():
    signature = Signature(
        "skills/researcher/x/SKILL.md", "goal", "create", "title, end_state, reason"
    )
    assert "the researcher cannot call this" in problems_with(signature)[0][0]
    other = Signature(
        "skills/orchestrator/x/SKILL.md", "goal", "create", "title, end_state, reason"
    )
    assert problems_with(other) == ([], [])


@pytest.mark.parametrize("body", ["reason", "reason, ..."])
def test_a_closing_ellipsis_allows_leaving_out_required_fields(body):
    omitted = problems_with(only(f"`experiment.abandon {{{body}}}`"))[1]
    assert (omitted == []) is body.endswith("...")


def enum_problems(signature: Signature) -> list[str]:
    """Example values in a signature that the field's enum would refuse. A value in angle brackets
    or with an alternative (``yes|no``, ``"yes" or "no"``) is a description, not an example."""
    spec = catalog.TOOLS.get(signature.tool)
    if spec is None:
        return []
    action = spec.action(signature.action) if signature.action else None
    properties = spec.accepted(action)
    problems = []
    for item in top_level_items(signature.body):
        match = ITEM.fullmatch(item)
        if match is None or match[2] is None:
            continue
        name, value = match[1].rstrip("?"), match[2].strip().strip('"')
        allowed = properties.get(name, {}).get("enum")
        if allowed is None or "<" in value or "|" in value or " or " in value:
            continue
        if value not in allowed:
            problems.append(f"{signature.source}: {signature.label} {name}: {value!r}")
    return problems


def test_every_example_value_is_one_the_field_accepts():
    assert [p for signature in shipped_signatures() for p in enum_problems(signature)] == []
