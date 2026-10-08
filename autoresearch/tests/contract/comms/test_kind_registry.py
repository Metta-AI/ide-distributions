"""Conformance of the comms kind registry (``schemas/comms/``, ``comms.kinds``).

Every kind has a valid schema, examples that behave, a row in each document that lists kinds,
a routing handler if inbound, and a place in the CLI. Any kind a document lists without a
schema fails too. ``schemas/comms/README.md`` says how to add a kind.
"""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from autoresearch import cli
from autoresearch.comms import inbound, kinds
from autoresearch.runtime.routing import HANDLERS

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = kinds.COMMS_SCHEMAS / "examples"
KIND_ROW = re.compile(r"^\| `(\w+)` \|(.*)$", re.MULTILINE)
ALL_KINDS = [("outbound", name) for name in kinds.outbound_kinds()] + [
    ("inbound", name) for name in kinds.inbound_kinds()
]


def _rows(path: Path, heading: str) -> dict[str, str]:
    """Kind -> rest of its table row, in the section under ``heading``."""
    text = path.read_text(encoding="utf-8")
    section = text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]
    return dict(KIND_ROW.findall(section))


def _examples(*parts: str) -> list[tuple[str, str, Path]]:
    base = EXAMPLES.joinpath(*parts)
    return [
        (path.parent.parent.name, path.parent.name, path) for path in sorted(base.glob("*/*/*.json"))
    ]


VALID = _examples()
INVALID = _examples("invalid")


def _id(example) -> str:
    return "/".join([example[0], example[1], example[2].stem])


@pytest.mark.parametrize(("direction", "name"), ALL_KINDS)
def test_kind_schema_is_valid_and_consistent(direction, name):
    kind = kinds.get(direction, name)
    Draft202012Validator.check_schema(kind.schema)
    assert kind.schema["$id"] == f"autoresearch/comms/{direction}/{name}.json"
    assert kind.schema["x-autoresearch"]["direction"] == direction
    assert kind.schema["properties"]["kind"] == {"const": name}
    assert kind.summary
    if direction == "inbound":
        assert kind.describe, "an inbound kind needs describe (briefing text)"
        assert set(kind.checks) <= set(inbound.CHECKS)


def test_envelopes_and_the_message_json_alias_are_valid_schemas():
    for path in [kinds.SCHEMAS / "message.json", *kinds.COMMS_SCHEMAS.glob("envelope.*.json")]:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(("direction", "name"), ALL_KINDS)
def test_every_kind_has_a_valid_example(direction, name):
    assert (direction, name) in {(d, n) for d, n, _ in VALID}


@pytest.mark.parametrize("example", VALID, ids=_id)
def test_valid_example_passes(example):
    direction, name, path = example
    message = json.loads(path.read_text(encoding="utf-8"))
    assert (direction, name) in ALL_KINDS
    assert message["kind"] == name
    assert kinds.problems(direction, message) == []
    if direction == "inbound":
        assert kinds.describe(message)
    else:
        alias = kinds.schema_validator("autoresearch/message.json")
        assert list(alias.iter_errors(message)) == []


@pytest.mark.parametrize("example", INVALID, ids=_id)
def test_invalid_example_fails_for_its_reason(example):
    direction, name, path = example
    message = json.loads(path.read_text(encoding="utf-8"))
    assert (direction, name) in ALL_KINDS
    assert message["kind"] == name
    problems = kinds.problems(direction, message)
    assert any(message["_why"] in problem for problem in problems), problems


def test_every_inbound_kind_has_a_handler():
    assert set(HANDLERS) == set(kinds.inbound_kinds())


@pytest.mark.parametrize("name", kinds.inbound_kinds())
def test_cli_accepts_every_inbound_kind(name):
    args = cli.build_parser().parse_args(
        ["comms", "inbound", name, "--json", "{}", "--author", "preston"]
    )
    assert args.kind == name


def test_schemas_readme_lists_every_kind_with_its_summary():
    rows = _rows(kinds.COMMS_SCHEMAS / "README.md", "## Kinds")
    assert set(rows) == {name for _, name in ALL_KINDS}
    for direction, name in ALL_KINDS:
        assert rows[name] == f" {direction} | {kinds.get(direction, name).summary} |"


def test_doctrine_lists_every_kind():
    doctrine = ROOT / "doctrine" / "communication.md"
    assert set(_rows(doctrine, "## Outbound kinds")) == set(kinds.outbound_kinds())
    assert set(_rows(doctrine, "## Inbound kinds")) == set(kinds.inbound_kinds())


def test_communicate_skill_lists_every_outbound_kind():
    skill = ROOT / "skills" / "autoresearch" / "communicate" / "SKILL.md"
    assert set(_rows(skill, "## When to send each kind")) == set(kinds.outbound_kinds())
