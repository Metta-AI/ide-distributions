"""Small helpers shared by the tool modules: parameter schemas and reference arguments."""

from __future__ import annotations

from typing import Any

from research_core import refs
from research_core.tools.base import ToolError

STRING = {"type": "string"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
STRINGS = {"type": "array", "items": {"type": "string"}}
OBJECT = {"type": "object"}


def string(description: str, **extra: Any) -> dict:
    return {"type": "string", "description": description, **extra}


def integer(description: str) -> dict:
    return {"type": "integer", "description": description}


def boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


def strings(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def obj(description: str) -> dict:
    return {"type": "object", "description": description}


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
