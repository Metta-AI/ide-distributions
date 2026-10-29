"""Validate a client's context block against the published schema (design A.7).

Hermes's interpreter has no ``jsonschema`` package, so this is a small stdlib validator for the
subset the contract files use: object properties with ``additionalProperties: false``,
``required``, ``type``, ``const``, ``maxLength``, ``maxItems``, ``items``, ``$ref`` into
``$defs`` and the ``limits`` block. The rules of the design: drop unknown fields, refuse
malformed or oversize fields with a clear error, never crash a turn.
"""

from __future__ import annotations

import json
from typing import Any

JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _resolve(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    if not ref.startswith("#/$defs/"):
        raise ValueError(f"unsupported $ref {ref}")
    return root["$defs"][ref.split("/")[-1]]


def _check(value: Any, schema: dict, root: dict, path: str, problems: list[str]) -> Any:
    """Return the kept value (unknown object fields dropped) or ``None`` when refused."""
    schema = _resolve(schema, root)
    expected = JSON_TYPES.get(schema.get("type", ""))
    if expected is not None and (
        not isinstance(value, expected) or (expected is int and isinstance(value, bool))
    ):
        problems.append(f"{path}: must be {schema['type']}")
        return None
    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: must be {schema['const']!r}")
        return None
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        problems.append(f"{path}: at most {schema['maxLength']} characters (got {len(value)})")
        return None
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{path}: at most {schema['maxItems']} items (got {len(value)})")
            return None
        if "items" in schema:
            kept = []
            for index, item in enumerate(value):
                checked = _check(item, schema["items"], root, f"{path}[{index}]", problems)
                if checked is not None:
                    kept.append(checked)
            return kept
        return value
    if isinstance(value, dict) and "properties" in schema:
        kept = {}
        for key, item in value.items():
            if key in schema["properties"]:
                checked = _check(item, schema["properties"][key], root, f"{path}.{key}", problems)
                if checked is not None:
                    kept[key] = checked
            elif schema.get("additionalProperties", True) is False:
                problems.append(f"{path}.{key}: unknown field, dropped")
        missing = [key for key in schema.get("required", ()) if key not in kept]
        for key in missing:
            problems.append(f"{path}.{key}: required")
        if missing and path != "context":
            return None  # an object without its required part is refused whole
        return kept
    return value


def validate(block: Any, schema: dict) -> tuple[dict, list[str]]:
    """``(kept fields, problems)``. Never raises on bad input."""
    problems: list[str] = []
    if not isinstance(block, dict):
        return {}, ["context: must be a JSON object"]
    limits = schema.get("limits", {})
    total = len(json.dumps(block, separators=(",", ":")).encode())
    if "total_bytes" in limits and total > limits["total_bytes"]:
        problems.append(f"context: at most {limits['total_bytes']} bytes (got {total}); refused")
        return {}, problems
    if "client_data" in block and "client_data_bytes" in limits:
        size = len(json.dumps(block["client_data"], separators=(",", ":")).encode())
        if size > limits["client_data_bytes"]:
            problems.append(
                f"context.client_data: at most {limits['client_data_bytes']} bytes (got {size})"
            )
            block = {k: v for k, v in block.items() if k != "client_data"}
    kept = _check(block, schema, schema, "context", problems)
    return (kept if isinstance(kept, dict) else {}), problems
