"""The registry of ``ar-comms/0`` message kinds, loaded from ``schemas/comms/``.

Each kind is one JSON Schema, ``schemas/comms/<direction>/<kind>.json``, built on its
direction's envelope (``envelope.outbound.json`` or ``envelope.inbound.json``) with
``allOf``. Its ``x-autoresearch`` keyword holds what code needs beyond the shape:

- ``direction``: ``outbound`` (agent to coach) or ``inbound`` (coach to agent);
- ``summary``: one line, for the README index and CLI help;
- ``describe`` (inbound): the text a briefing shows, as format-string parts joined with no
  separator. A part is left out when a field it names is missing, null or ``""``;
- ``checks`` (inbound): names of the checks that need the workspace (``comms.inbound.CHECKS``).

Everything else that lists kinds reads this module: ``envelope.KINDS``,
``inbound.INBOUND_KINDS``, the ``comms inbound`` CLI and ``reader``'s known-kind filter.
Adding a kind is a schema file, examples, doc rows and, if inbound, a routing handler
(``schemas/comms/README.md``); ``tests/contract/comms/test_kind_registry.py`` fails when a
place is missing.

A problem has the form ``location: message``. The location is the JSON path with ``/``
(``(message)`` for the whole message), and the message is the failing schema node's
``x-message`` when it has one, else the validator's own text. A missing property is
reported at its own location, with that property's ``x-message`` (or ``required``).
"""

import functools
import json
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
COMMS_SCHEMAS = SCHEMAS / "comms"
DIRECTIONS = ("outbound", "inbound")
Direction = Literal["outbound", "inbound"]


@dataclass(frozen=True)
class Kind:
    name: str
    direction: Direction
    summary: str
    schema: dict
    describe: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()


def _schema_files() -> list[Path]:
    envelopes = [SCHEMAS / "message.json", *sorted(COMMS_SCHEMAS.glob("*.json"))]
    return envelopes + [
        path for d in DIRECTIONS for path in sorted(COMMS_SCHEMAS.glob(f"{d}/*.json"))
    ]


@functools.cache
def _registry() -> tuple[Registry, dict[str, dict[str, Kind]]]:
    registry = Registry()
    kinds: dict[str, dict[str, Kind]] = {direction: {} for direction in DIRECTIONS}
    for path in _schema_files():
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        if path.parent.name in DIRECTIONS:
            meta = schema["x-autoresearch"]
            kinds[path.parent.name][path.stem] = Kind(
                name=path.stem,
                direction=path.parent.name,
                summary=meta["summary"],
                schema=schema,
                describe=tuple(meta.get("describe", ())),
                checks=tuple(meta.get("checks", ())),
            )
    return registry, kinds


@functools.cache
def schema_validator(schema_id: str) -> Draft202012Validator:
    """A validator for the schema with this ``$id``, resolving refs between the schemas."""
    registry, _ = _registry()
    return Draft202012Validator(registry.contents(schema_id), registry=registry)


def outbound_kinds() -> tuple[str, ...]:
    return tuple(_registry()[1]["outbound"])


def inbound_kinds() -> tuple[str, ...]:
    return tuple(_registry()[1]["inbound"])


def get(direction: Direction, name: str) -> Kind:
    """The kind ``name`` of ``direction``; KeyError when there is none."""
    return _registry()[1][direction][name]


def _problems_of(error: ValidationError) -> list[str]:
    path = [str(part) for part in error.absolute_path]
    if error.validator != "required":
        message = error.schema.get("x-message", error.message)
        return [f"{'/'.join(path) or '(message)'}: {message}"]
    # One problem per missing property, at its own path (the error itself names only one).
    properties = error.schema.get("properties", {})
    return [
        f"{'/'.join([*path, name])}: {properties.get(name, {}).get('x-message', 'required')}"
        for name in error.validator_value
        if name not in error.instance
    ]


def problems(direction: Direction, message) -> list[str]:
    """Schema problems of a message: its kind's schema, or for an unknown kind the
    envelope plus a ``kind`` problem. Workspace checks (``Kind.checks``) are the caller's."""
    if not isinstance(message, dict):
        return ["a message must be a JSON object"]
    kind = message.get("kind")
    known = _registry()[1][direction].get(kind) if isinstance(kind, str) else None
    schema_id = known.schema["$id"] if known else f"autoresearch/comms/envelope.{direction}.json"
    errors = schema_validator(schema_id).iter_errors(message)
    found = sorted({problem for error in errors for problem in _problems_of(error)})
    if isinstance(kind, str) and known is None:
        names = _registry()[1][direction]
        found.append(f"kind: {kind!r} is not one of {', '.join(names)}")
    return found


def _named_fields(part: str) -> list[str]:
    return [field for _, field, _, _ in string.Formatter().parse(part) if field]


def _present(message: dict, field: str) -> bool:
    try:
        value, _ = string.Formatter().get_field(field, (), message)
    except (KeyError, IndexError, TypeError):
        return False
    return value is not None and value != ""


def describe(message: dict) -> str:
    """The text a briefing shows for a valid inbound message (its kind's ``describe``)."""
    parts = get("inbound", message["kind"]).describe
    return "".join(
        part.format_map(message)
        for part in parts
        if all(_present(message, field) for field in _named_fields(part))
    )
