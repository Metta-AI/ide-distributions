"""Directives: standing user instructions on the workspace or on a Goal (design B.14).

A directive lives where its scope does: ``workspace`` rows in the workspace store, ``goal:<n>``
rows in that Goal's Coworld store (amendment A2.4), so each store's briefing lists what it
owns. ``add`` and ``update`` take whichever store they are given and refuse a scope that
belongs in the other one; a Goal scope must name an existing Goal.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from research_core.store._common import display_id, fetch_row, require
from research_core.store.errors import StoreError
from research_core.store.registry import DIRECTIVE, record_from_row

if TYPE_CHECKING:
    from research_core.store.actors import Actor
    from research_core.store.db import Store

WORKSPACE_SCOPE = "workspace"
_GOAL_SCOPE = re.compile(r"^goal:([1-9][0-9]*)$")


def _is_text(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _check_scope(problems: list[str], store: Store, scope: object) -> None:
    """``workspace`` only in the workspace store; ``goal:<n>`` only in a Coworld store."""
    match = _GOAL_SCOPE.match(scope) if isinstance(scope, str) else None
    if scope != WORKSPACE_SCOPE and match is None:
        problems.append(f"scope must be {WORKSPACE_SCOPE} or goal:<n>, not {scope!r}")
        return
    if scope == WORKSPACE_SCOPE:
        require(
            problems,
            store.kind == "workspace",
            f"scope {scope}: workspace directives live in the workspace store",
        )
        return
    if not require(
        problems,
        store.kind == "coworld",
        f"scope {scope}: Goal directives live in the Coworld store",
    ):
        return
    goal_id = int(match.group(1))
    exists = store.one("SELECT 1 FROM goals WHERE id = ?", (goal_id,))
    require(problems, exists is not None, f"G-{goal_id} does not exist")


def add(store: Store, actor: Actor, *, text: str, scope: str, source: str) -> dict[str, Any]:
    """A new active directive; ``source`` is the message it came from; event ``directive.added``."""
    with store.tx(actor) as tx:
        problems: list[str] = []
        require(problems, _is_text(text), "text is required")
        _check_scope(problems, store, scope)
        require(problems, _is_text(source), "source is required: the message it came from")
        if problems:
            raise StoreError(problems)
        cursor = tx.execute(
            "INSERT INTO directives(text, scope, source, status, created, created_by)"
            " VALUES (?, ?, ?, 'active', ?, ?)",
            (text, scope, source, tx.at, str(actor)),
        )
        directive_id = cursor.lastrowid
        tx.event("directive.added", display_id("D", directive_id), {"scope": scope})
    return read(store, directive_id)


def update(
    store: Store,
    actor: Actor,
    directive_id: int,
    *,
    text: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Change an active directive's text or scope; event ``directive.updated``."""
    with store.tx(actor) as tx:
        row = fetch_row(store, "directives", directive_id, display=f"D-{directive_id}")
        shown = display_id("D", row["id"])
        problems: list[str] = []
        require(
            problems, row["status"] == "active", f"{shown} is retired; add a new directive instead"
        )
        require(
            problems, text is not None or scope is not None, "nothing to update: give text or scope"
        )
        if text is not None:
            require(problems, _is_text(text), "text is required")
        if scope is not None:
            _check_scope(problems, store, scope)
        if problems:
            raise StoreError(problems)
        changed = {name: value for name, value in (("text", text), ("scope", scope)) if value}
        assignments = ", ".join(f"{name} = ?" for name in changed)
        tx.execute(
            f"UPDATE directives SET {assignments} WHERE id = ?", (*changed.values(), directive_id)
        )
        tx.event("directive.updated", shown, {"fields": sorted(changed)})
    return read(store, directive_id)


def retire(store: Store, actor: Actor, directive_id: int) -> dict[str, Any]:
    """Retire an active directive (kept, never deleted); event ``directive.retired``."""
    with store.tx(actor) as tx:
        row = fetch_row(store, "directives", directive_id, display=f"D-{directive_id}")
        shown = display_id("D", row["id"])
        if row["status"] != "active":
            raise StoreError([f"{shown} is already retired"])
        tx.execute(
            "UPDATE directives SET status = 'retired', retired = ? WHERE id = ?",
            (tx.at, directive_id),
        )
        tx.event("directive.retired", shown, {"scope": row["scope"]})
    return read(store, directive_id)


def read(store: Store, directive_id: int) -> dict[str, Any]:
    row = fetch_row(store, "directives", directive_id, display=f"D-{directive_id}")
    return record_from_row(DIRECTIVE, row)


def list(
    store: Store, scope: str | None = None, status: str | None = "active"
) -> list[dict[str, Any]]:
    """Directives oldest first; ``scope`` narrows to one, ``status=None`` lists retired too."""
    if status is not None and status not in DIRECTIVE.statuses:
        raise StoreError(
            [f"status must be one of {', '.join(DIRECTIVE.statuses)} or null, not {status!r}"]
        )
    rows = store.q(
        "SELECT * FROM directives WHERE (? IS NULL OR scope = ?) AND (? IS NULL OR status = ?)"
        " ORDER BY id",
        (scope, scope, status, status),
    )
    return [record_from_row(DIRECTIVE, row) for row in rows]
