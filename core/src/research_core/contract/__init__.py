"""The client contract files the profiles publish (design §10, A.2) and their validator."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Literal

CONTRACT_VERSION = 1
CONTRACT_DIR = Path(__file__).resolve().parent
SchemaName = Literal["context", "events", "ui-state"]
BLOCK_LABEL = "Context v1:"


@cache
def schema(name: SchemaName) -> dict:
    return json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text())


def validate_context(block: object) -> tuple[dict, list[str]]:
    from research_core.contract.validate import validate

    return validate(block, schema("context"))


def find_block(text: str) -> tuple[object, str | None]:
    """The context block in a user message, as ``(parsed JSON or None, parse error)``.

    The block is the text after the last ``Context v1:`` line. A message with no label yields
    ``(None, None)``; a label followed by text that is not JSON yields ``(None, error)``.
    """
    index = text.rfind(BLOCK_LABEL)
    if index < 0:
        return None, None
    raw = text[index + len(BLOCK_LABEL) :].strip()
    try:
        return json.loads(raw), None
    except ValueError as error:
        return None, f"context block is not valid JSON: {error}"
