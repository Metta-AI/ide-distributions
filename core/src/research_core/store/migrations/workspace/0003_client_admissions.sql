CREATE TABLE client_admissions (
    op_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('open', 'message')),
    request TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','cancelled','failed','interrupted')),
    run_id TEXT,
    dispatch_started INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX client_admissions_pending ON client_admissions(status, created_at);
CREATE TRIGGER client_admissions_log_insert AFTER INSERT ON client_admissions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'client_admissions', NEW.rowid, 'insert');
END;
CREATE TRIGGER client_admissions_log_update AFTER UPDATE ON client_admissions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'client_admissions', NEW.rowid, 'update');
END;
CREATE TRIGGER client_admissions_log_delete AFTER DELETE ON client_admissions BEGIN
    INSERT INTO change_log(at, tbl, row_id, op)
    VALUES (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'client_admissions', OLD.rowid, 'delete');
END;
