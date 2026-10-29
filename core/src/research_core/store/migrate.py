"""Numbered SQL migrations, the expected schema and the rebuild that keeps rows (design B.2, B.10).

Migrations are ``migrations/<kind>/NNNN_name.sql``; ``PRAGMA user_version`` records the last
one applied. The store's shape is fixed machinery: ``expected_schema`` is what the migrations
produce on an empty database, ``actual_schema`` is what a file has, and ``Store.open`` refuses
to work through any difference. ``rebuild_keeping_rows`` is the watcher's repair.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
KINDS = ("coworld", "workspace")

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_SCHEMA_TYPES = ("table", "index", "trigger", "view")
# Tables FTS5 creates behind a virtual table; they are not part of the declared shape.
_FTS_SHADOW_SUFFIXES = ("data", "idx", "docsize", "config", "content")
_TIMESTAMP_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"not a store kind: {kind!r} (one of {', '.join(KINDS)})")


def migration_files(kind: str) -> list[tuple[int, Path]]:
    """The migrations of ``kind`` as ``(number, path)``, in numeric order."""
    _check_kind(kind)
    found: list[tuple[int, Path]] = []
    for path in sorted((MIGRATIONS_DIR / kind).iterdir()):
        if path.suffix != ".sql":
            continue
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise ValueError(f"migration file {path.name!r} is not named NNNN_name.sql")
        found.append((int(match.group(1)), path))
    found.sort()
    numbers = [number for number, _ in found]
    duplicates = sorted({f"{n:04d}" for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise ValueError(f"duplicate migration numbers: {', '.join(duplicates)}")
    return found


def latest_version(kind: str) -> int:
    return migration_files(kind)[-1][0]


def split_statements(script: str) -> list[str]:
    """Split a SQL script into statements, keeping trigger bodies whole."""
    statements: list[str] = []
    buffer: list[str] = []
    for line in script.splitlines(keepends=True):
        buffer.append(line)
        if sqlite3.complete_statement("".join(buffer)):
            statements.append("".join(buffer).strip())
            buffer = []
    rest = [line for line in buffer if line.strip() and not line.lstrip().startswith("--")]
    if rest:
        raise ValueError("the migration ends with an incomplete statement")
    return statements


def apply_migrations(conn: sqlite3.Connection, kind: str) -> int:
    """Apply every migration above ``PRAGMA user_version``; return the version reached.

    Each migration runs in its own ``BEGIN IMMEDIATE`` transaction and re-reads the version
    inside it, so two processes opening a brand-new store at once cannot both apply ``0001``.
    A failing migration rolls back whole. Calling this on a current store is a no-op.
    """
    for number, path in migration_files(kind):
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] >= number:
                conn.execute("COMMIT")
                continue
            for statement in split_statements(path.read_text()):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return conn.execute("PRAGMA user_version").fetchone()[0]


def normalise_sql(sql: str) -> str:
    """Whitespace collapsed to single spaces, lower case: the form schemas are compared in."""
    return " ".join(sql.split()).lower()


def actual_schema(conn: sqlite3.Connection) -> dict[str, str]:
    """``"<type> <name>" -> normalised SQL`` for every table, index, trigger and view.

    SQLite's own objects (``sqlite_sequence``, auto-indexes) and the shadow tables behind FTS5
    virtual tables are left out: they follow from the declared objects.
    """
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
    ).fetchall()
    fts_tables = {
        row[1] for row in rows if row[0] == "table" and " using fts5" in normalise_sql(row[2])
    }
    shadow = {f"{fts}_{suffix}" for fts in fts_tables for suffix in _FTS_SHADOW_SUFFIXES}
    schema: dict[str, str] = {}
    for type_, name, sql in rows:
        if type_ not in _SCHEMA_TYPES or name.startswith("sqlite_") or name in shadow:
            continue
        schema[f"{type_} {name}"] = normalise_sql(sql)
    return schema


@lru_cache
def expected_schema(kind: str) -> dict[str, str]:
    """The schema the migrations of ``kind`` produce on an empty database (cached, read-only)."""
    _check_kind(kind)
    conn = sqlite3.connect(":memory:")
    try:
        apply_migrations(conn, kind)
        return actual_schema(conn)
    finally:
        conn.close()


def schema_diff(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    """One problem per object that is missing, unexpected or defined differently."""
    problems: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            problems.append(f"missing {key}")
        elif key not in expected:
            problems.append(f"unexpected {key}")
        elif expected[key] != actual[key]:
            problems.append(f"{key} differs: expected {expected[key]!r}, actual {actual[key]!r}")
    return problems


@dataclass
class RebuildReport:
    tables: dict[str, int]
    dropped_columns: dict[str, list[str]]
    dropped_tables: list[str]
    user_version: int
    foreign_key_violations: int

    def to_dict(self) -> dict:
        return asdict(self)


def rebuild_keeping_rows(path: Path, kind: str) -> RebuildReport:
    """Replace the store file with one built from the migrations, keeping every row it can.

    Rows are copied table by table over the columns both shapes share (``events`` keeps its
    ``seq``); columns and tables the migrations do not know are dropped and reported. The copy's
    own ``change_log`` rows are marked with a ``rebuild:`` token and the old ``change_log`` is
    appended after them, so earlier unmarked writes stay visible. The new file is fsynced and
    moved into place atomically. Callers close their ``Store`` first: connections to the old
    file keep pointing at the replaced inode.
    """
    _check_kind(kind)
    path = Path(path)
    tmp = path.with_name(path.name + ".rebuild")
    for leftover in (tmp, *_sidecars(tmp)):
        leftover.unlink(missing_ok=True)
    # Fold the old store's WAL into its file, so the copy below reads everything.
    old = sqlite3.connect(path, isolation_level=None)
    try:
        old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        old.close()

    new = sqlite3.connect(tmp, isolation_level=None)
    try:
        new.execute("PRAGMA foreign_keys = OFF")
        user_version = apply_migrations(new, kind)
        new.execute("ATTACH DATABASE ? AS old", (str(path),))
        new.execute("BEGIN IMMEDIATE")
        try:
            report = _copy_rows(new)
            new.execute("COMMIT")
        except BaseException:
            new.execute("ROLLBACK")
            raise
        violations = len(new.execute("PRAGMA foreign_key_check").fetchall())
        new.execute("DETACH DATABASE old")
    finally:
        new.close()

    _fsync(tmp)
    os.replace(tmp, path)
    for sidecar in _sidecars(path):
        sidecar.unlink(missing_ok=True)
    _fsync(path.parent)
    return RebuildReport(
        tables=report[0],
        dropped_columns=report[1],
        dropped_tables=report[2],
        user_version=user_version,
        foreign_key_violations=violations,
    )


def _copy_rows(
    conn: sqlite3.Connection,
) -> tuple[dict[str, int], dict[str, list[str]], list[str]]:
    """Copy ``old.*`` into ``main.*`` (attached databases) over shared columns."""
    new_tables = _copyable_tables(conn, "main")
    old_tables = _copyable_tables(conn, "old")
    tables: dict[str, int] = {}
    dropped_columns: dict[str, list[str]] = {}
    for name in new_tables:
        if name == "change_log":
            continue
        if name not in old_tables:
            tables[name] = 0
            continue
        old_columns = _columns(conn, "old", name)
        new_columns = set(_columns(conn, "main", name))
        shared = [column for column in old_columns if column in new_columns]
        dropped = [column for column in old_columns if column not in new_columns]
        if dropped:
            dropped_columns[name] = dropped
        if shared:
            columns = ", ".join(f'"{column}"' for column in shared)
            # OR REPLACE: the migrations' own seed rows (meta) yield to the old store's values.
            conn.execute(
                f'INSERT OR REPLACE INTO main."{name}" ({columns})'
                f' SELECT {columns} FROM old."{name}"'
            )
        tables[name] = conn.execute(f'SELECT COUNT(*) FROM old."{name}"').fetchone()[0]
    # The copy fired the change_log triggers: mark those rows as this rebuild's, then carry the
    # old log over (new ids, same tokens) so unmarked writes from before the rebuild stay visible.
    conn.execute(
        "UPDATE change_log SET token = ? WHERE token IS NULL",
        (f"rebuild:{secrets.token_hex(4)}",),
    )
    if "change_log" in old_tables:
        shared = [c for c in _columns(conn, "old", "change_log") if c in _CHANGE_LOG_COPY]
        columns = ", ".join(f'"{column}"' for column in shared)
        conn.execute(
            f"INSERT INTO main.change_log ({columns}) SELECT {columns} FROM old.change_log"
            " ORDER BY id"
        )
        tables["change_log"] = conn.execute("SELECT COUNT(*) FROM old.change_log").fetchone()[0]
    dropped_tables = sorted(set(old_tables) - set(new_tables))
    return tables, dropped_columns, dropped_tables


_CHANGE_LOG_COPY = ("at", "tbl", "row_id", "op", "token")


def _copyable_tables(conn: sqlite3.Connection, schema: str) -> list[str]:
    """Ordinary tables of an attached database: no virtual tables, shadow tables or sqlite_*."""
    rows = conn.execute(
        f"SELECT name, sql FROM {schema}.sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
        " ORDER BY name"
    ).fetchall()
    virtual = {name for name, sql in rows if normalise_sql(sql).startswith("create virtual table")}
    shadow = {f"{name}_{suffix}" for name in virtual for suffix in _FTS_SHADOW_SUFFIXES}
    return [
        name
        for name, _ in rows
        if name not in virtual and name not in shadow and not name.startswith("sqlite_")
    ]


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal"))


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
