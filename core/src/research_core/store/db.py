"""Store and Tx: opening a store, token-marked transactions, events, detection (design B.2, B.10).

Every library write is one ``BEGIN IMMEDIATE`` transaction (``Store.tx``), so concurrent writers
serialise. Triggers log each change to ``change_log`` with a NULL token; before commit the
transaction stamps its own rows with its token, so any NULL token left after a commit is a write
made outside the library (``unmarked_changes``). ``open`` applies pending migrations and refuses
a file whose schema differs from the migrations (``SchemaMismatch``).

Error messages never name the store path (B.10).
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from research_core.store import migrate
from research_core.store._common import Clock, json_field, now_iso
from research_core.store.actors import Actor
from research_core.store.errors import SchemaMismatch
from research_core.store.events import Event

STATE_README = """\
# .state

Records live in store.db and are changed through the profile tools (goal, task, claim, ...).
Direct edits are detected and reported. Do not edit files here.
"""


@dataclass(frozen=True)
class SchemaStatus:
    ok: bool
    diff: list[str]
    user_version: int
    expected_version: int


class Store:
    """One open connection to a Coworld or workspace store."""

    def __init__(
        self,
        path: Path,
        kind: Literal["coworld", "workspace"],
        conn: sqlite3.Connection,
        clock: Clock | None = None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.conn = conn
        self.clock = clock
        self._tx: Tx | None = None

    @classmethod
    def open(
        cls, path: Path, *, kind: Literal["coworld", "workspace"], clock: Clock | None = None
    ) -> Store:
        """Open (creating and migrating if needed), check the schema, write the README.

        WAL journal, foreign keys on, a 5 s busy timeout. Pending migrations are applied; a
        schema that differs from the migrations raises ``SchemaMismatch`` with every difference.
        """
        if kind not in migrate.KINDS:
            raise ValueError(f"not a store kind: {kind!r}")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL").fetchone()
            conn.execute("PRAGMA foreign_keys = ON")
            if conn.execute("PRAGMA user_version").fetchone()[0] < migrate.latest_version(kind):
                migrate.apply_migrations(conn, kind)
            store = cls(path, kind, conn, clock)
            status = store.schema_status()
            if not status.ok:
                raise SchemaMismatch(status.diff)
        except BaseException:
            conn.close()
            raise
        readme = path.parent / "README.md"
        if not readme.exists():
            readme.write_text(STATE_README)
        return store

    # --- reads ---------------------------------------------------------------------------

    def q(self, sql: str, params: tuple | list | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def now(self) -> str:
        """The current time from the store's clock (tests inject one)."""
        return now_iso(self.clock)

    def events_after(self, seq: int, limit: int = 500) -> list[Event]:
        rows = self.q("SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (seq, limit))
        return [Event.from_row(row) for row in rows]

    def last_seq(self) -> int:
        return self.one("SELECT COALESCE(MAX(seq), 0) FROM events")[0]

    def get_meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return None if row is None else row[0]

    def set_meta(self, key: str, value: str) -> None:
        """``meta`` is the store's own bookkeeping (heartbeat, has_work); it is not logged."""
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # --- the schema check and detection --------------------------------------------------

    def schema_status(self) -> SchemaStatus:
        expected = migrate.expected_schema(self.kind)
        actual = migrate.actual_schema(self.conn)
        diff = migrate.schema_diff(expected, actual)
        user_version = self.one("PRAGMA user_version")[0]
        expected_version = migrate.latest_version(self.kind)
        if user_version != expected_version:
            diff.append(f"user_version is {user_version}, expected {expected_version}")
        if "table meta" in actual:
            schema_kind = self.get_meta("schema_kind")
            if schema_kind != self.kind:
                diff.append(f"meta.schema_kind is {schema_kind!r}, expected {self.kind!r}")
        return SchemaStatus(
            ok=not diff, diff=diff, user_version=user_version, expected_version=expected_version
        )

    def unmarked_changes(self) -> list[dict[str, Any]]:
        """``change_log`` rows no library transaction stamped: writes made outside the library."""
        rows = self.q(
            "SELECT id, at, tbl, row_id, op FROM change_log WHERE token IS NULL ORDER BY id"
        )
        return [dict(row) for row in rows]

    def acknowledge_unmarked(self, ids: list[int], note: str = "reported") -> int:
        """Stamp reported unmarked rows ``ack:<note>`` so they are not reported again."""
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        cursor = self.conn.execute(
            f"UPDATE change_log SET token = ? WHERE token IS NULL AND id IN ({placeholders})",
            (f"ack:{note}", *ids),
        )
        return cursor.rowcount

    # --- writes --------------------------------------------------------------------------

    def tx(self, actor: Actor) -> Tx:
        """One transaction: ``with store.tx(actor) as tx: ...``. Nested use raises."""
        return Tx(self, actor)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Tx:
    """One ``BEGIN IMMEDIATE`` transaction with a token; commit stamps ``change_log``.

    ``at`` is the transaction's timestamp: every row and event written in it shares one time.
    """

    def __init__(self, store: Store, actor: Actor) -> None:
        self.store = store
        self.actor = actor
        self.token = secrets.token_hex(8)
        self.at = store.now()
        self._open = False
        self._start_id = 0

    def __enter__(self) -> Tx:
        if self.store._tx is not None:
            raise RuntimeError("a transaction is already open on this store")
        conn = self.store.conn
        conn.execute("BEGIN IMMEDIATE")
        self.store._tx = self
        self._open = True
        # Rows above this id are ours: the write lock is held from here until commit.
        self._start_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM change_log").fetchone()[0]
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        conn = self.store.conn
        try:
            if exc_type is None:
                conn.execute(
                    "UPDATE change_log SET token = ? WHERE token IS NULL AND id > ?",
                    (self.token, self._start_id),
                )
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            self._open = False
            self.store._tx = None
        return False

    def _check_open(self) -> None:
        if not self._open:
            raise RuntimeError("the transaction is not open")

    def execute(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Cursor:
        self._check_open()
        return self.store.conn.execute(sql, params)

    def q(self, sql: str, params: tuple | list | dict = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def event(self, kind: str, subject: str, payload: dict[str, Any]) -> int:
        """Append an ``events`` row by this transaction's actor; returns its ``seq``."""
        if not isinstance(payload, dict):
            raise TypeError("an event payload is a dict")
        cursor = self.execute(
            "INSERT INTO events(at, actor, kind, subject, payload) VALUES (?, ?, ?, ?, ?)",
            (self.at, str(self.actor), kind, subject, json_field(payload)),
        )
        return cursor.lastrowid
