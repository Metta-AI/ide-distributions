"""Conformance of the comms kind registry (``schemas/comms/``, ``comms.kinds``).

Every kind has a valid schema, examples that behave, a row in each document that lists kinds
(agreeing with its metadata), a routing handler if inbound, and goes through ``comms inbound``.
Any kind a document lists without a schema fails too. The README's loading recipe works as
written. ``schemas/comms/README.md`` says how to add a kind.
"""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ide_research_core.comms import inbound, kinds
from ide_research_core.comms.publisher import publish_runtime
from ide_research_core.comms.reader import read_since
from ide_research_core.runtime.handoff import ACK_REQUIRED_KINDS
from ide_research_core.runtime.ledger import canonical_lock
from ide_research_core.runtime.routing import HANDLERS

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = kinds.COMMS_SCHEMAS / "examples"
README = kinds.COMMS_SCHEMAS / "README.md"
DOCTRINE = ROOT / "doctrine" / "communication.md"
SKILL = ROOT / "skills" / "autoresearch" / "communicate" / "SKILL.md"
KIND_ROW = re.compile(r"^\| `(\w+)` \|(.*)$", re.MULTILINE)
ALL_KINDS = [("outbound", name) for name in kinds.outbound_kinds()] + [
    ("inbound", name) for name in kinds.inbound_kinds()
]
# Fields the envelope or the submitter sets, not the payload of `comms inbound`.
ENVELOPE_FIELDS = {"schema", "id", "kind", "author", "created", "received_at", "on_behalf_of"}


def _rows(path: Path, heading: str) -> dict[str, list[str]]:
    """Kind -> the other cells of its table row, in the section under ``heading``."""
    text = path.read_text(encoding="utf-8")
    section = text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]
    return {
        name: [cell.strip() for cell in rest.strip().strip("|").split(" | ")]
        for name, rest in KIND_ROW.findall(section)
    }


def _examples(*parts: str) -> list[tuple[str, str, Path]]:
    base = EXAMPLES.joinpath(*parts)
    return [
        (path.parent.parent.name, path.parent.name, path)
        for path in sorted(base.glob("*/*/*.json"))
    ]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


VALID = _examples()
INVALID = _examples("invalid")


def _id(example) -> str:
    return "/".join([example[0], example[1], example[2].stem])


@pytest.mark.parametrize(("direction", "name"), ALL_KINDS)
def test_kind_schema_is_valid_and_consistent(direction, name):
    kind = kinds.get(direction, name)
    Draft202012Validator.check_schema(kind.schema)
    assert kind.schema["$id"] == f"comms/{direction}/{name}.json"
    assert kind.schema["x-autoresearch"]["direction"] == direction
    assert kind.schema["properties"]["kind"] == {"const": name}
    assert kind.summary
    if direction == "inbound":
        assert kind.describe, "an inbound kind needs describe (briefing text)"
        assert kind.ack in ("card", "runtime", "none")
        assert set(kind.checks) <= set(inbound.CHECKS)
    else:
        assert kind.ack is None


def test_every_schema_id_is_its_path_under_schemas():
    comms = kinds.COMMS_SCHEMAS
    for path in [
        kinds.SCHEMAS / "message.json",
        *comms.glob("*.json"),
        *comms.glob("*bound/*.json"),
    ]:
        schema = _load(path)
        Draft202012Validator.check_schema(schema)
        assert schema["$id"] == path.relative_to(kinds.SCHEMAS).as_posix()


@pytest.mark.parametrize(("direction", "name"), ALL_KINDS)
def test_every_kind_has_a_valid_example(direction, name):
    assert (direction, name) in {(d, n) for d, n, _ in VALID}


@pytest.mark.parametrize("example", VALID, ids=_id)
def test_valid_example_passes(example):
    direction, name, path = example
    message = _load(path)
    assert (direction, name) in ALL_KINDS
    assert message["kind"] == name
    assert kinds.problems(direction, message) == []
    if direction == "inbound":
        assert kinds.describe(message)
    else:
        alias = kinds.schema_validator("message.json")
        assert list(alias.iter_errors(message)) == []


@pytest.mark.parametrize("example", INVALID, ids=_id)
def test_invalid_example_fails_for_its_reason(example):
    direction, name, path = example
    message = _load(path)
    assert (direction, name) in ALL_KINDS
    assert message["kind"] == name
    problems = kinds.problems(direction, message)
    assert any(message["_why"] in problem for problem in problems), problems


def test_the_readme_recipe_validates_the_examples(monkeypatch):
    """Runs the README's Python block exactly as written, from the autoresearch directory."""
    text = README.read_text(encoding="utf-8")
    [recipe] = re.findall(r"^```python\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    monkeypatch.chdir(ROOT)
    namespace: dict = {}
    exec(recipe, namespace)  # noqa: S102 - our own documentation
    problems = namespace["problems"]
    for direction, name, path in VALID:
        schema = "message.json" if direction == "outbound" else f"comms/{direction}/{name}.json"
        assert problems(_load(path), schema) == [], path
    for direction, name, path in INVALID:
        schema = "message.json" if direction == "outbound" else f"comms/{direction}/{name}.json"
        assert problems(_load(path), schema), path


def test_every_inbound_kind_has_a_handler():
    assert set(HANDLERS) == set(kinds.inbound_kinds())


def test_card_acknowledged_kinds_are_the_ones_card_finish_requires():
    card = {name for name in kinds.inbound_kinds() if kinds.get("inbound", name).ack == "card"}
    assert card == ACK_REQUIRED_KINDS


@pytest.mark.parametrize("name", kinds.inbound_kinds())
def test_comms_inbound_accepts_each_kind_end_to_end(board, preston, name):
    """The CLI, run as an external agent would, submits each kind's first valid example."""
    example = next(_load(path) for d, n, path in VALID if (d, n) == ("inbound", name))
    payload = {key: value for key, value in example.items() if key not in ENVELOPE_FIELDS}
    if "ids" in payload:  # a seen names published messages
        with canonical_lock(board.canonical):
            publish_runtime(board.canonical, "note", "T", "B", [], allow_no_remote=True)
        payload["ids"] = [read_since(board.canonical, "t", after=0)[0]["id"]]
    assert preston.send(name, **payload).startswith("I-")


def test_schemas_readme_lists_every_kind():
    rows = _rows(README, "## Kinds")
    assert set(rows) == {name for _, name in ALL_KINDS}
    for direction, name in ALL_KINDS:
        assert rows[name] == [direction, f"`{direction}/{name}.json`"]


def test_doctrine_lists_every_kind_with_its_metadata():
    outbound_rows = _rows(DOCTRINE, "## Outbound kinds")
    inbound_rows = _rows(DOCTRINE, "## Inbound kinds")
    assert set(outbound_rows) == set(kinds.outbound_kinds())
    assert set(inbound_rows) == set(kinds.inbound_kinds())
    for name, (_, reply_expected) in outbound_rows.items():
        expected = kinds.get("outbound", name).expects_response
        assert reply_expected.startswith("Yes") == expected, name
    for name, (_, acknowledge) in inbound_rows.items():
        ack = kinds.get("inbound", name).ack
        assert acknowledge.startswith("Yes") == (ack == "card"), name
        assert ("runtime acknowledges" in acknowledge) == (ack == "runtime"), name


def test_communicate_skill_lists_every_outbound_kind_and_the_card_acknowledged_ones():
    assert set(_rows(SKILL, "## When to send each kind")) == set(kinds.outbound_kinds())
    [line] = [
        line
        for line in SKILL.read_text(encoding="utf-8").splitlines()
        if "Acknowledge each" in line
    ]
    named = set(re.findall(r"`(\w+)`", line)) & set(kinds.inbound_kinds())
    assert named == ACK_REQUIRED_KINDS
