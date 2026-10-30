-- 0001_init.sql: the workspace store (<IDE_WORKSPACE>/.state/store.db).
-- Design: Appendix B of docs/designs/ide-profile-refactor-2026-10-28-final.html; plan:
-- docs/plans/ide-profile-refactor/02-store.md (Task 2.1) and the review amendments.
-- The store's shape is fixed machinery: Store.open compares a file's schema with the result of
-- these migrations and refuses any difference. Contents are writable through the library only;
-- the change_log triggers at the end make every other write visible.

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Append-only: the AFTER INSERT change_log trigger below detects rows written outside the
-- library; these two triggers refuse any change to a row once written.
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_kind ON events(kind, seq);
CREATE INDEX events_subject ON events(subject, seq);
CREATE TRIGGER events_append_only_update BEFORE UPDATE ON events BEGIN
    SELECT RAISE(ABORT, 'events is append-only');
END;
CREATE TRIGGER events_append_only_delete BEFORE DELETE ON events BEGIN
    SELECT RAISE(ABORT, 'events is append-only');
END;

-- One row per changed row, written by triggers with token NULL; Tx stamps its own rows with
-- its token before commit. A NULL token after commit is a write made outside the library.
CREATE TABLE change_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    tbl TEXT NOT NULL,
    row_id INTEGER,
    op TEXT NOT NULL,
    token TEXT
);
CREATE INDEX change_log_unmarked ON change_log(id) WHERE token IS NULL;

-- The watcher's bookkeeping of which events it handled (events itself never changes).
CREATE TABLE handled_events (
    seq INTEGER PRIMARY KEY REFERENCES events(seq),
    kind TEXT NOT NULL,
    run_id TEXT,
    session_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    handled_at TEXT,
    last_error TEXT
);

-- The Coworld registry (B.14): the platform id is the only stored identifier.
CREATE TABLE coworlds (
    coworld_id TEXT PRIMARY KEY,
    folder TEXT NOT NULL UNIQUE,
    mixin_version TEXT,
    tool_check_status TEXT,
    set_up_at TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    created TEXT
);
CREATE UNIQUE INDEX one_active ON coworlds(active) WHERE active = 1;

CREATE TABLE directives (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    scope TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','retired')),
    created TEXT,
    created_by TEXT,
    retired TEXT
);
CREATE INDEX directives_scope ON directives(scope, status);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated TEXT,
    updated_by TEXT,
    cites TEXT
);

-- The store side of ui.notify; each notice also writes an events row of kind notice.
CREATE TABLE notices (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    refs TEXT DEFAULT '[]',
    urgency TEXT CHECK(urgency IN ('low','normal','high')),
    created TEXT,
    created_by TEXT,
    read_at TEXT,
    read_by TEXT
);
CREATE INDEX notices_unread ON notices(id) WHERE read_at IS NULL;

-- Hermes sessions of every kind, registered before any Coworld is active; task_id points into
-- the Coworld store named by coworld_id, so it carries no foreign key.
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('chat','task','wake')),
    coworld_id TEXT,
    task_id INTEGER,
    goal_id INTEGER,
    event TEXT,
    parent TEXT,
    run_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    started TEXT NOT NULL,
    ended TEXT,
    last_seen TEXT,
    session_context TEXT,
    client_capabilities TEXT,
    drained INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX sessions_task ON sessions(coworld_id, task_id);
CREATE INDEX sessions_kind ON sessions(kind, ended);

CREATE TABLE ui_state (
    conversation TEXT PRIMARY KEY,
    doc TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 0,
    updated TEXT
);

-- change_log triggers: every record table, plus events on insert. meta and change_log
-- itself are not logged.
CREATE TRIGGER events_log_insert AFTER INSERT ON events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'events', NEW.rowid, 'insert');
END;
CREATE TRIGGER handled_events_log_insert AFTER INSERT ON handled_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'handled_events', NEW.rowid, 'insert');
END;
CREATE TRIGGER handled_events_log_update AFTER UPDATE ON handled_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'handled_events', OLD.rowid, 'update');
END;
CREATE TRIGGER handled_events_log_delete AFTER DELETE ON handled_events BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'handled_events', OLD.rowid, 'delete');
END;
CREATE TRIGGER coworlds_log_insert AFTER INSERT ON coworlds BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'coworlds', NEW.rowid, 'insert');
END;
CREATE TRIGGER coworlds_log_update AFTER UPDATE ON coworlds BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'coworlds', OLD.rowid, 'update');
END;
CREATE TRIGGER coworlds_log_delete AFTER DELETE ON coworlds BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'coworlds', OLD.rowid, 'delete');
END;
CREATE TRIGGER directives_log_insert AFTER INSERT ON directives BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'directives', NEW.rowid, 'insert');
END;
CREATE TRIGGER directives_log_update AFTER UPDATE ON directives BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'directives', OLD.rowid, 'update');
END;
CREATE TRIGGER directives_log_delete AFTER DELETE ON directives BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'directives', OLD.rowid, 'delete');
END;
CREATE TRIGGER settings_log_insert AFTER INSERT ON settings BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'settings', NEW.rowid, 'insert');
END;
CREATE TRIGGER settings_log_update AFTER UPDATE ON settings BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'settings', OLD.rowid, 'update');
END;
CREATE TRIGGER settings_log_delete AFTER DELETE ON settings BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'settings', OLD.rowid, 'delete');
END;
CREATE TRIGGER notices_log_insert AFTER INSERT ON notices BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'notices', NEW.rowid, 'insert');
END;
CREATE TRIGGER notices_log_update AFTER UPDATE ON notices BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'notices', OLD.rowid, 'update');
END;
CREATE TRIGGER notices_log_delete AFTER DELETE ON notices BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'notices', OLD.rowid, 'delete');
END;
CREATE TRIGGER sessions_log_insert AFTER INSERT ON sessions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'sessions', NEW.rowid, 'insert');
END;
CREATE TRIGGER sessions_log_update AFTER UPDATE ON sessions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'sessions', OLD.rowid, 'update');
END;
CREATE TRIGGER sessions_log_delete AFTER DELETE ON sessions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'sessions', OLD.rowid, 'delete');
END;
CREATE TRIGGER ui_state_log_insert AFTER INSERT ON ui_state BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'ui_state', NEW.rowid, 'insert');
END;
CREATE TRIGGER ui_state_log_update AFTER UPDATE ON ui_state BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'ui_state', OLD.rowid, 'update');
END;
CREATE TRIGGER ui_state_log_delete AFTER DELETE ON ui_state BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'ui_state', OLD.rowid, 'delete');
END;

INSERT INTO meta(key, value) VALUES ('schema_kind', 'workspace');
INSERT INTO meta(key, value) VALUES ('created', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
