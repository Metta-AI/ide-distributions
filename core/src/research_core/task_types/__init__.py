"""Task types: the deliverables a Task of each type must show before it completes (design B.4).

Each type is a file ``<type>.md`` beside this module. The file opens with a fenced ```json
block, the front matter, followed by the Researcher instructions (the body; entry 7 writes
them). Stdlib only: the plugin loads these inside Hermes.

Front matter: ``required`` lists Task fields that must be set at completion; ``checklist``
lists items with ``key`` (the entry in the completion checklist), ``text`` (what the item is,
reported when it is missing), ``pattern`` (a regex every value must match), ``min_items`` and
``max_items`` (the value is a list of that size; without them it is one value), ``none_ok``
(``"none: <reason>"`` is accepted in place of the value) and ``required_when`` (the item is
required only when that Task field is set; a repair Task names its capability).
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TYPES = ("research", "improvement", "ideation", "maintenance")

_FRONT_MATTER = re.compile(r"\A```json\n(?P<front>.*?)\n```\n?(?P<body>.*)\Z", re.DOTALL)
_NONE_WITH_REASON = re.compile(r"^none:\s*\S")
_ITEM_KEYS = {"key", "text", "pattern", "min_items", "max_items", "none_ok", "required_when"}


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    text: str
    pattern: str | None = None
    min_items: int | None = None
    max_items: int | None = None
    none_ok: bool = False
    required_when: str | None = None

    @property
    def is_list(self) -> bool:
        return self.min_items is not None or self.max_items is not None


@dataclass(frozen=True)
class TaskType:
    name: str
    front: dict[str, Any]
    body: str

    @property
    def required(self) -> list[str]:
        return list(self.front.get("required", []))

    @property
    def checklist(self) -> list[ChecklistItem]:
        return [ChecklistItem(**item) for item in self.front.get("checklist", [])]


@functools.cache
def load(type: str) -> TaskType:
    """The type file ``<type>.md``, parsed; ``ValueError`` for an unknown or malformed type."""
    if type not in TYPES:
        raise ValueError(f"unknown task type {type!r}; types: {', '.join(TYPES)}")
    path = Path(__file__).with_name(f"{type}.md")
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"task type file {path.name} must open with a ```json front matter block")
    front = json.loads(match.group("front"))
    _check_front(path.name, front)
    return TaskType(type, front, match.group("body").strip())


def check(
    type: str, checklist: Mapping[str, Any], *, task_fields: Mapping[str, Any] | None = None
) -> list[str]:
    """What ``checklist`` lacks for a Task of ``type``: one line per missing or malformed item.

    Each line is ``<key>: <text>``, with the shortfall in parentheses when a value was given.
    ``task_fields`` is the Task's stored fields; with it, ``required`` fields and items with
    ``required_when`` are checked too. Without it those checks are skipped.
    """
    loaded = load(type)
    problems: list[str] = []
    if task_fields is not None:
        for name in loaded.required:
            if not task_fields.get(name):
                problems.append(f"Task field {name} must be set")
    for item in loaded.checklist:
        if item.required_when is not None:
            if task_fields is None or not task_fields.get(item.required_when):
                continue
        problem = _check_item(item, checklist.get(item.key))
        if problem is not None:
            problems.append(problem)
    return problems


def _check_item(item: ChecklistItem, value: Any) -> str | None:
    label = f"{item.key}: {item.text}"
    if value is None or value == "" or value == []:
        return label
    if isinstance(value, str) and value.startswith("none"):
        if item.none_ok and _NONE_WITH_REASON.match(value):
            return None
        if item.none_ok:
            return f"{label} (none needs a reason: none: <reason>)"
    pattern = re.compile(item.pattern) if item.pattern else None
    if item.is_list:
        if not isinstance(value, list | tuple):
            return f"{label} (expected a list, got {value!r})"
        if item.min_items is not None and len(value) < item.min_items:
            return f"{label} (at least {item.min_items}, got {len(value)})"
        if item.max_items is not None and len(value) > item.max_items:
            return f"{label} (at most {item.max_items}, got {len(value)})"
        bad = [v for v in value if not isinstance(v, str) or (pattern and not pattern.search(v))]
        if bad:
            return f"{label} (not in the expected form: {', '.join(map(repr, bad))})"
        return None
    if not isinstance(value, str) or (pattern and not pattern.search(value)):
        return f"{label} (got {value!r})"
    return None


def _check_front(name: str, front: Any) -> None:
    problems: list[str] = []
    if not isinstance(front, dict):
        raise ValueError(f"{name}: the front matter must be a JSON object")
    required = front.get("required", [])
    if not isinstance(required, list) or not all(isinstance(f, str) for f in required):
        problems.append("required must be a list of Task field names")
    items = front.get("checklist")
    if not isinstance(items, list) or not items:
        problems.append("checklist must be a non-empty list")
        items = []
    for item in items:
        if not isinstance(item, dict) or not {"key", "text"} <= set(item):
            problems.append(f"checklist item needs key and text: {item!r}")
            continue
        unknown = set(item) - _ITEM_KEYS
        if unknown:
            problems.append(f"checklist item {item['key']} has unknown fields {sorted(unknown)}")
        if "pattern" in item:
            try:
                re.compile(item["pattern"])
            except re.error as error:
                problems.append(f"checklist item {item['key']} pattern does not compile: {error}")
        for bound in ("min_items", "max_items"):
            if bound in item and (
                isinstance(item[bound], bool) or not isinstance(item[bound], int)
            ):
                problems.append(f"checklist item {item['key']} {bound} must be an int")
    if problems:
        raise ValueError(f"{name}: " + "; ".join(problems))
