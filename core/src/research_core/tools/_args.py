"""Small helpers shared by the tool modules: parameter schemas and reference arguments."""

from __future__ import annotations

from typing import Any

from research_core import refs
from research_core.tools.base import ToolError

STRING = {"type": "string"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
STRINGS = {"type": "array", "items": {"type": "string"}}


def string(description: str, **extra: Any) -> dict:
    return {"type": "string", "description": description, **extra}


def integer(description: str) -> dict:
    return {"type": "integer", "description": description}


def boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


def strings(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


# Object fields always spell out their keys: Hermes's schema sanitizer turns an object schema
# without ``properties`` into ``{"type": "object", "properties": {}}``, and weak models then send
# ``{}`` or ``[{}]``. ``base.validate_args`` checks only top-level fields; the handlers check the
# keys, so ``required`` and ``closed`` must say what the handler refuses, no more and no less.


def obj(
    description: str, properties: dict[str, dict], *, required: tuple = (), closed: bool = False
) -> dict:
    """An object with known keys; ``closed`` when the handler refuses keys it does not know."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if description:
        schema["description"] = description
    if required:
        schema["required"] = list(required)
    if closed:
        schema["additionalProperties"] = False
    return schema


def objects(description: str, item: dict, *, min_items: int | None = None) -> dict:
    """A list of objects shaped like ``item`` (an :func:`obj`)."""
    schema: dict[str, Any] = {"type": "array", "items": item}
    if description:
        schema["description"] = description
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


def free_object(description: str) -> dict:
    """An object whose keys are the caller's own, such as query parameters or a JSON body."""
    schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": True}
    if description:
        schema["description"] = description
    return schema


def any_of(description: str, *types: dict) -> dict:
    """A field that takes one of several types. A field with no ``type`` becomes a string under
    Hermes's Moonshot sanitizer (``agent/moonshot_schema.py``); an ``anyOf`` survives it."""
    return {"anyOf": list(types), "description": description}


def record_number(value: Any, kind: str, field: str) -> int:
    """The integer id behind a record argument given as ``G-3``, ``"3"`` or ``3``."""
    if isinstance(value, bool):
        raise ToolError(
            f"{field} must be a {kind} reference such as {refs.RECORD_PREFIXES_BY_KIND[kind]}-3"
        )
    if isinstance(value, int):
        if value < 1:
            raise ToolError(f"{field} must be a positive {kind} id")
        return value
    text = str(value).strip()
    if text.isdigit() and int(text) >= 1:
        return int(text)
    ref = refs.parse(text)
    if ref.kind == "record" and ref.record_kind == kind and ref.number:
        return ref.number
    expected = refs.RECORD_PREFIXES_BY_KIND[kind]
    raise ToolError(f"{field} must be a {kind} reference such as {expected}-3, not {text!r}")


def record_numbers(values: Any, kind: str, field: str) -> list[int]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ToolError(f"{field} must be a list of {kind} references")
    return [record_number(value, kind, field) for value in values]
