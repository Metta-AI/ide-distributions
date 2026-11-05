"""The tool schemas the models see: object fields spell out their keys, every field is
described, and a field several actions share has one shape."""

import json

import pytest

from research_core.tools import catalog
from research_core.tools.base import ActionSpec, ToolSpec, field_description, usage, validate_args

ROLES = catalog.ROLES


def specs():
    seen = {}
    for role in ROLES:
        for spec in catalog.tools_for(role):
            seen[spec.name] = spec
    return list(seen.values())


def declared_fields(spec: ToolSpec):
    """(where, name, schema) for every field as its action (or the tool) declares it."""
    for name, schema in spec.params.items():
        yield spec.name, name, schema
    for action in spec.actions or ():
        for name, schema in action.params.items():
            yield f"{spec.name}.{action.name}", name, schema


def nested(path: str, schema: dict):
    """Every schema under ``schema`` (itself included), with a dotted path."""
    yield path, schema
    for key, sub in (schema.get("properties") or {}).items():
        yield from nested(f"{path}.{key}", sub)
    if isinstance(schema.get("items"), dict):
        yield from nested(f"{path}[]", schema["items"])
    for index, branch in enumerate(schema.get("anyOf") or ()):
        yield from nested(f"{path}|{index}", branch)


@pytest.mark.parametrize("role", ROLES)
def test_every_object_declares_its_keys_and_every_array_its_items(role):
    """Hermes's sanitizer turns an object without properties into an empty one, and an untyped
    field into a string for Kimi models; neither may reach a model."""
    problems = []
    for spec in catalog.tools_for(role):
        for name, schema in spec.properties(role).items():
            for path, node in nested(f"{spec.name}.{name}", schema):
                if node.get("type") == "object" and "properties" not in node:
                    problems.append(f"{path}: object without properties")
                if node.get("type") == "array" and not isinstance(node.get("items"), dict):
                    problems.append(f"{path}: array without items")
                if "type" not in node and "anyOf" not in node:
                    problems.append(f"{path}: no type")
    assert problems == []


def test_every_field_and_key_is_described():
    problems = []
    for spec in specs():
        for where, name, schema in declared_fields(spec):
            for path, node in nested(f"{where}:{name}", schema):
                if "|" in path:
                    continue  # anyOf branches are types; the field carries the description
                if path.endswith("[]"):
                    continue  # list items are described by the list
                if not str(node.get("description", "")).strip():
                    problems.append(path)
    assert problems == []


def test_a_field_shared_by_actions_has_one_shape():
    """The tool list shows a shared field once, shaped as its first action declares it."""
    problems = []
    for spec in specs():
        shapes: dict[str, tuple[str, dict]] = {}
        for action in spec.actions or ():
            for name, schema in action.params.items():
                shape = {k: v for k, v in schema.items() if k != "description"}
                first = shapes.setdefault(name, (action.name, shape))
                if first[1] != shape:
                    problems.append(f"{spec.name}.{name}: {first[0]} and {action.name} differ")
    assert problems == []


def test_claim_evidence_items_carry_the_handler_rules():
    write = catalog.TOOLS["claim"].schema("researcher")["parameters"]["properties"]["evidence"]
    assert write["minItems"] == 1
    item = write["items"]
    assert set(item["properties"]) == {"type", "ref", "stance", "note"}
    assert item["required"] == ["type", "ref", "stance"]
    assert "additionalProperties" not in item  # the store ignores extra keys, it does not refuse
    assert "code <path>:<a>-<b>@<sha>" in item["properties"]["ref"]["description"]


def test_task_failure_matches_the_store():
    from research_core.store import tasks

    failure = catalog.TOOLS["task"].schema("researcher")["parameters"]["properties"]["failure"]
    assert failure["required"] == list(tasks.FAILURE_REQUIRED)
    assert set(failure["properties"]) == set(tasks.FAILURE_REQUIRED + tasks.FAILURE_OPTIONAL)
    assert failure["additionalProperties"] is False  # unknown keys are refused


def test_a_field_description_names_its_actions_write_side_first():
    tags = catalog.TOOLS["claim"].schema("orchestrator")["parameters"]["properties"]["tags"]
    text = tags["description"]
    assert text.startswith("write: Keywords")
    assert text.index("write:") < text.index("search:")
    assert text.endswith("Required for write; optional for search.")


def test_field_description_forms():
    def action(name, description, required=()):
        return ActionSpec(name, "", {"f": {"type": "string", "description": description}}, required)

    one = [action("a", "The thing.", ("f",))]
    assert field_description("f", one, 3) == "The thing. Required for a."
    same = [action("a", "The thing."), action("b", "The thing.", ("f",))]
    assert field_description("f", same, 2) == "The thing. Required for b."
    differ = [action("a", "A filter."), action("b", "The value.", ("f",))]
    assert field_description("f", differ, 3) == (
        "b: The value. a: A filter. Required for b; optional for a."
    )


def test_a_refusal_shows_an_object_fields_keys():
    how_to = usage(catalog.TOOLS["claim"], "researcher", "write")
    assert how_to["required"]["evidence"].endswith("Shape: [{type, ref, stance, note?}]")


GENERATED_FIELDS = ("operation_id", "assignment_id", "task_updated")


@pytest.mark.parametrize("role", ["orchestrator", "researcher"])
def test_no_schema_or_refusal_offers_a_field_code_fills_in(role):
    for spec in catalog.tools_for(role):
        assert not set(GENERATED_FIELDS) & set(spec.schema(role)["parameters"]["properties"])
        for action in spec.actions_for(role) or [None]:
            shown = json.dumps(usage(spec, role, action.name if action else None))
            assert not [name for name in GENERATED_FIELDS if name in shown]


def test_a_frozen_schema_may_still_send_them_on_the_actions_that_took_them():
    task = catalog.TOOLS["task"]
    args = {
        "action": "assign",
        "task": "T-1",
        "profile": "researcher",
        "context": "Repair",
        "rationale": "Fit",
        **dict.fromkeys(GENERATED_FIELDS, "value"),
    }
    assert validate_args(task, "orchestrator", args)[1] == []
    _, problems = validate_args(task, "orchestrator", {"action": "list", "operation_id": "value"})
    assert problems == ["operation_id is not a field of list"]

