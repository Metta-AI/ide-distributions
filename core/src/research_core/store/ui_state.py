"""UI state (design A.10, A.11): one JSON document per conversation, changed by JSON Patches.

Agent-facing UI state (what the agent showed, the panels it set, the ``agent-context`` panel)
is a JSON object per conversation. ``patch`` applies an RFC 6902 patch (``add``, ``replace``,
``remove`` and ``test``; paths are JSON Pointers with the ``~0`` and ``~1`` escapes) to the
conversation's document in one transaction, bumps ``version`` once per patch and emits
``ui.patch`` carrying the operations, so a client holding the previous version applies the same
patch instead of reloading. Any failing operation refuses the whole patch and leaves the
document as it was. ``get`` returns the current document (the snapshot a client starts from).

Which store: both migrations have a ``ui_state`` table and the conversation key decides where a
row lives. A chat Session's conversation is its Hermes session id and a wake's is the fixed
conversation ``workspace`` (A3.5); both are workspace-level, so their rows live in the
**workspace store**. The Coworld store's ``ui_state`` table is unused for now; the functions
work on either store and callers pass the workspace store.
"""

from __future__ import annotations

import copy
from typing import Any

from research_core.store._common import json_field, parse_json, require
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError

OPS = ("add", "replace", "remove", "test")


def get(store: Store, conversation: str) -> dict[str, Any]:
    """The conversation's document; an empty one at version 0 when nothing was written."""
    row = store.one("SELECT * FROM ui_state WHERE conversation = ?", (conversation,))
    if row is None:
        return {"conversation": conversation, "doc": {}, "version": 0, "updated": None}
    return _record(row)


def patch(
    store: Store, actor: Actor, conversation: str, ops: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply a JSON Patch to the conversation's document; emits ``ui.patch`` with the ops."""
    problems: list[str] = []
    require(problems, isinstance(conversation, str) and conversation, "conversation is required")
    problems.extend(_validate_ops(ops))
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        row = tx.one("SELECT * FROM ui_state WHERE conversation = ?", (conversation,))
        doc = {} if row is None else parse_json(row["doc"], default={})
        version = (0 if row is None else row["version"]) + 1
        doc = apply_patch(doc, ops)
        if row is None:
            tx.execute(
                "INSERT INTO ui_state(conversation, doc, version, updated) VALUES (?, ?, ?, ?)",
                (conversation, json_field(doc), version, tx.at),
            )
        else:
            tx.execute(
                "UPDATE ui_state SET doc = ?, version = ?, updated = ? WHERE conversation = ?",
                (json_field(doc), version, tx.at, conversation),
            )
        tx.event(
            "ui.patch", conversation, {"conversation": conversation, "version": version, "ops": ops}
        )
        return _record(tx.one("SELECT * FROM ui_state WHERE conversation = ?", (conversation,)))


def apply_patch(doc: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """A copy of ``doc`` with ``ops`` applied in order; ``StoreError`` names the failing op."""
    doc = copy.deepcopy(doc)
    for index, op in enumerate(ops):
        doc = _apply(doc, op, index)
    return doc


# --- validation and the patch operations ---------------------------------------------------


def _validate_ops(ops: object) -> list[str]:
    if not isinstance(ops, list) or not ops:
        return ["ops is a non-empty list of JSON Patch operations"]
    problems: list[str] = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            problems.append(f"op {index}: not an object")
            continue
        name = op.get("op")
        if name not in OPS:
            problems.append(f"op {index}: op is one of {', '.join(OPS)} (got {name!r})")
            continue
        path = op.get("path")
        if not isinstance(path, str) or (path and not path.startswith("/")):
            problems.append(f'op {index} ({name}): path is a JSON Pointer ("" or starting with /)')
        if name != "remove" and "value" not in op:
            problems.append(f"op {index} ({name} {path}): value is required")
    return problems


def _apply(doc: dict[str, Any], op: dict[str, Any], index: int) -> dict[str, Any]:
    name, path = op["op"], op["path"]
    label = f"op {index} ({name} {path})"
    tokens = _tokens(path)
    if not tokens:
        return _apply_to_root(doc, op, label)
    parent = _walk(doc, tokens[:-1], label)
    last = tokens[-1]
    value = copy.deepcopy(op.get("value"))
    if isinstance(parent, dict):
        if name == "add":
            parent[last] = value
            return doc
        if last not in parent:
            raise StoreError([f"{label}: the path does not exist"])
        _apply_at(parent, last, name, value, label)
        return doc
    if isinstance(parent, list):
        if name == "add" and last == "-":
            parent.append(value)
            return doc
        position = _index(last, label)
        if name == "add":
            if not 0 <= position <= len(parent):
                raise StoreError([f"{label}: index {position} is out of range"])
            parent.insert(position, value)
            return doc
        if not 0 <= position < len(parent):
            raise StoreError([f"{label}: the path does not exist"])
        _apply_at(parent, position, name, value, label)
        return doc
    raise StoreError([f"{label}: the path goes through a value that is not an object or a list"])


def _apply_to_root(doc: dict[str, Any], op: dict[str, Any], label: str) -> dict[str, Any]:
    name = op["op"]
    if name == "remove":
        raise StoreError([f"{label}: the root cannot be removed"])
    if name == "test":
        if doc != op["value"]:
            raise StoreError([f"{label}: the value differs"])
        return doc
    if not isinstance(op["value"], dict):
        raise StoreError([f"{label}: the root is a JSON object"])
    return copy.deepcopy(op["value"])


def _apply_at(container: Any, key: Any, name: str, value: Any, label: str) -> None:
    """replace, remove or test at an existing key of a dict or index of a list."""
    if name == "replace":
        container[key] = value
    elif name == "remove":
        del container[key]
    elif container[key] != value:
        raise StoreError([f"{label}: the value differs"])


def _walk(doc: Any, tokens: list[str], label: str) -> Any:
    node = doc
    for token in tokens:
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and _is_index(token) and int(token) < len(node):
            node = node[int(token)]
        else:
            raise StoreError([f"{label}: the path does not exist"])
    return node


def _tokens(path: str) -> list[str]:
    """JSON Pointer tokens: ``~1`` is ``/`` and ``~0`` is ``~``, unescaped in that order."""
    if path == "":
        return []
    return [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]


def _index(token: str, label: str) -> int:
    if not _is_index(token):
        raise StoreError([f"{label}: {token!r} is not a list index"])
    return int(token)


def _is_index(token: str) -> bool:
    return token.isdigit() and (token == "0" or not token.startswith("0"))


def _record(row: Any) -> dict[str, Any]:
    return {
        "conversation": row["conversation"],
        "doc": parse_json(row["doc"], default={}),
        "version": row["version"],
        "updated": row["updated"],
    }
